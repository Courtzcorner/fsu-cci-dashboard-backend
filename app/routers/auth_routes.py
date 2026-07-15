import logging

from fastapi import APIRouter, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.csv_user_store import get_user
from app.schemas.auth import AuthenticatedUserOut, LoginRequest, TokenResponse
from app.security import create_access_token, verify_password

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])
limiter = Limiter(key_func=get_remote_address)

settings = get_settings()

GENERIC_LOGIN_ERROR = "Invalid username or password"

# Valid bcrypt hash of a fixed dummy value, used to run a bcrypt comparison
# for unknown usernames so response timing doesn't leak whether an account
# exists in data/users.csv.
_DUMMY_HASH = "$2b$12$CnJIN3XbmrkzDNwNaezfO.hEy3ytYCCMjpT3GnN/4VKvJ8rlRD9JS"


@router.post("/login", response_model=TokenResponse, responses={401: {"description": "Invalid credentials"}})
@limiter.limit(settings.login_rate_limit)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    """Authenticates against the backend-only data/users.csv file. The CSV
    contents (including password hashes) are never returned to the caller.
    """
    user = get_user(payload.username)

    password_hash = user.password_hash if user else _DUMMY_HASH
    password_ok = verify_password(payload.password, password_hash)

    if not user or not password_ok:
        logger.info("Failed login attempt for username=%s", payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    token, expires_in = create_access_token(username=user.username, role=user.role)
    logger.info("Successful login for username=%s role=%s", user.username, user.role)

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=AuthenticatedUserOut(username=user.username, role=user.role),
    )
