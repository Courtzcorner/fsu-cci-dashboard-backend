import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import CurrentUser, get_current_user_allow_pending_credentials
from app.models.mixins import utcnow
from app.models.user import User
from app.schemas.auth import (
    AuthenticatedUserOut,
    CompleteFirstLoginRequest,
    CompleteFirstLoginResponse,
    LoginRequest,
    LogoutResponse,
    MeOut,
    TokenResponse,
)
from app.security import create_access_token, hash_password, verify_password
from app.services.audit_service import record_audit_log
from app.services.credential_setup_service import ValidationError, validate_new_password, validate_new_username

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])
limiter = Limiter(key_func=get_remote_address)

settings = get_settings()

GENERIC_LOGIN_ERROR = "Invalid username or password"

# Valid bcrypt hash of a fixed dummy value, used to run a bcrypt comparison
# for unknown usernames so response timing doesn't leak whether an account
# exists.
_DUMMY_HASH = "$2b$12$CnJIN3XbmrkzDNwNaezfO.hEy3ytYCCMjpT3GnN/4VKvJ8rlRD9JS"


@router.post("/login", response_model=TokenResponse, responses={401: {"description": "Invalid credentials"}})
@limiter.limit(settings.login_rate_limit)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticates against the `users` table (the shared database).
    Never returns a password hash or any internal identifier.

    A temporary account (`must_change_credentials=True`) authenticates
    exactly like any other account here - the ONLY difference is the
    `must_change_credentials` flag surfaced on `user`, and that the
    resulting token is rejected by every route except the three exempted
    ones in this router (enforced in app.deps.get_current_user, not just
    by this flag being present in the response).
    """
    user = db.query(User).filter(User.username == payload.username).first()

    password_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = verify_password(payload.password, password_hash)

    if not user or not password_ok or not user.is_active:
        logger.info("Failed login attempt for username=%s", payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    token, expires_in = create_access_token(username=user.username, role=user.role, token_version=user.token_version)
    logger.info(
        "Successful login for username=%s role=%s must_change_credentials=%s",
        user.username, user.role, user.must_change_credentials,
    )

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=AuthenticatedUserOut(
            id=user.id, username=user.username, role=user.role, must_change_credentials=user.must_change_credentials
        ),
    )


@router.get("/auth/me", response_model=MeOut)
def get_me(current_user: CurrentUser = Depends(get_current_user_allow_pending_credentials)) -> MeOut:
    """One of the three routes a temporary (must_change_credentials=True)
    token is allowed to call - lets the frontend re-check auth state
    without granting any dashboard access."""
    return MeOut(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        alumni_id=current_user.alumni_id,
        must_change_credentials=current_user.must_change_credentials,
    )


@router.post("/auth/logout", response_model=LogoutResponse)
@limiter.limit(settings.login_rate_limit)
def logout(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user_allow_pending_credentials),
    db: Session = Depends(get_db),
) -> LogoutResponse:
    """There is no server-side session store for this stateless-JWT
    system, so "logout" is implemented as revoking every token already
    issued for this account: bumping `token_version` makes the `tv` claim
    on every existing token (including the one used to call this
    endpoint) stop matching, so app.deps.get_current_user rejects them
    on their very next use."""
    user = db.get(User, current_user.id)
    if user is not None:
        user.token_version = (user.token_version or 0) + 1
        db.commit()
        logger.info("User logged out (all sessions revoked) for username=%s", current_user.username)
    return LogoutResponse()


@router.post("/auth/complete-first-login", response_model=CompleteFirstLoginResponse)
@limiter.limit(settings.login_rate_limit)
def complete_first_login(
    payload: CompleteFirstLoginRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user_allow_pending_credentials),
    db: Session = Depends(get_db),
) -> CompleteFirstLoginResponse:
    """The only endpoint (besides GET /auth/me and POST /auth/logout) a
    temporary account's token is allowed to call. Updates the SAME User
    row identified by the token's user_id - role, alumni_id, UserProfile,
    work/education history, privacy settings, and every other
    user_id-keyed relationship are completely untouched, since nothing
    here ever creates a second account.
    """
    if not current_user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credential setup is not required for this account",
        )

    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        new_username = validate_new_username(payload.new_username, current_username=user.username, db=db)
        new_password = validate_new_password(payload.new_password, payload.confirm_password)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    previous_username = user.username
    now = utcnow()

    # Role, id, alumni_id, and every relationship keyed off user_id are
    # never touched below - only username/password/credential-state
    # fields on this exact row.
    user.previous_username = previous_username
    user.username = new_username
    user.password_hash = hash_password(new_password)
    user.must_change_credentials = False
    user.credentials_updated_at = now
    user.username_changed_at = now
    # Revokes the temporary token (and any other outstanding token) for
    # this account - the user must sign in again, which is the whole
    # point of this endpoint never returning a new access token.
    user.token_version = (user.token_version or 0) + 1

    record_audit_log(
        db,
        user_id=user.id,
        action="first_login_credentials_completed",
        entity_type="user",
        entity_id=user.id,
        details={
            "previous_username": previous_username,
            "new_username": new_username,
            "role": user.role,
            "credentials_updated_at": now.isoformat(),
        },
    )
    db.commit()

    logger.info(
        "First-login credential setup completed for user_id=%s previous_username=%s new_username=%s",
        user.id, previous_username, new_username,
    )

    return CompleteFirstLoginResponse(username=new_username, credentials_updated_at=now)
