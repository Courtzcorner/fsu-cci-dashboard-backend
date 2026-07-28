from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class AuthenticatedUserOut(BaseModel):
    id: str
    username: str
    role: str
    # Additive: True only for a seeded temporary account that has not yet
    # completed POST /auth/complete-first-login. The frontend must treat
    # this as a hard redirect-to-setup signal, but the backend enforces it
    # independently (see app.deps.get_current_user) regardless of what the
    # frontend does with it.
    must_change_credentials: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthenticatedUserOut


class MeOut(BaseModel):
    id: str
    username: str
    role: str
    alumni_id: str | None = None
    must_change_credentials: bool


class CompleteFirstLoginRequest(BaseModel):
    """Deliberately does NOT accept role, user_id, alumni_id, or any admin
    /permission field - `extra="forbid"` rejects any such field outright
    instead of silently ignoring it."""

    new_username: str = Field(..., min_length=1, max_length=64)
    new_password: str = Field(..., min_length=1, max_length=256)
    confirm_password: str = Field(..., min_length=1, max_length=256)

    model_config = {"extra": "forbid"}


class CompleteFirstLoginResponse(BaseModel):
    success: bool = True
    message: str = "Credentials updated. Please sign in again with your new username and password."
    # Tells the frontend explicitly what to do next - no access token is
    # returned, and the temporary token used to call this endpoint is
    # revoked server-side before this response is sent.
    action: str = "sign_out_and_redirect_to_login"
    username: str
    credentials_updated_at: datetime


class LogoutResponse(BaseModel):
    success: bool = True
    message: str = "Signed out. All existing sessions for this account have been revoked."
