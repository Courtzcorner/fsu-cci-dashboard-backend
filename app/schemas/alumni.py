from datetime import date
from typing import Optional

from pydantic import BaseModel


class AlumniOut(BaseModel):
    id: str
    organization: str
    first_name: str
    last_name: str
    full_name: str
    graduation_year: Optional[int] = None
    major: Optional[str] = None
    degree: Optional[str] = None
    university: Optional[str] = None
    job_title: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    career_category: Optional[str] = None
    seniority: Optional[str] = None
    location_original: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    country: Optional[str] = None
    metro_area: Optional[str] = None
    display_location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_normalization_status: str
    linkedin_url: Optional[str] = None
    verified: bool
    verification_status: str
    verification_date: Optional[date] = None
    profile_completion: int

    # --- Additive: alumni account profile linking (see
    # app.routers.user_profile_routes / app.routers.public_profile_routes).
    # These never affect filtering, pagination, or counting above - they
    # only describe whether a confirmed self-service profile exists. ---
    has_public_profile: bool = False
    public_profile_url: Optional[str] = None
    profile_photo_url: Optional[str] = None


class AlumniListMeta(BaseModel):
    organization: str
    total: int
    page: int
    page_size: int
    total_pages: int = 0


class AlumniListResponse(BaseModel):
    data: list[AlumniOut]
    meta: AlumniListMeta
