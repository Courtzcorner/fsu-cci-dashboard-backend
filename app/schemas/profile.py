from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    id: str
    first_name: str
    last_name: str
    full_name: str
    preferred_name: Optional[str] = None
    verified_legal_name: Optional[str] = None
    legal_name_verified: bool
    legal_name_verification_status: str
    legal_name_verified_at: Optional[date] = None

    graduation_date: Optional[date] = None
    graduation_year: Optional[int] = None
    job_title: Optional[str] = None
    company: Optional[str] = None
    job_location: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

    profile_image_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    bio: Optional[str] = None
    profile_visibility: str
    profile_completion: int


class ProfileUpdateRequest(BaseModel):
    """Only these fields may be self-edited by an alumni user. Any other
    field (id, organization membership, role, verification_status,
    legal_name_verified, verified_legal_name, created_at, updated_at,
    alumni_id) is rejected by simply not existing on this schema.
    """

    graduation_date: Optional[date] = None
    graduation_year: Optional[int] = None
    job_title: Optional[str] = Field(None, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    job_location: Optional[str] = Field(None, max_length=512)
    city: Optional[str] = Field(None, max_length=128)
    state: Optional[str] = Field(None, max_length=64)
    country: Optional[str] = Field(None, max_length=128)
    linkedin_url: Optional[str] = Field(None, max_length=512)
    bio: Optional[str] = Field(None, max_length=4000)
    profile_visibility: Optional[str] = None

    model_config = {"extra": "forbid"}


class ProfilePhotoUploadResponse(BaseModel):
    profile_image_url: str


class LegalNameChangeRequestIn(BaseModel):
    requested_legal_name: str = Field(..., min_length=1, max_length=256)
    reason: Optional[str] = Field(None, max_length=2000)


class LegalNameChangeRequestOut(BaseModel):
    id: str
    alumni_id: str
    requested_legal_name: str
    reason: Optional[str] = None
    status: str
    reviewed_by_user_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}
