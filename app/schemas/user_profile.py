from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class WorkHistoryIn(BaseModel):
    employer: str = Field(..., min_length=1, max_length=255)
    job_title: Optional[str] = Field(None, max_length=255)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: bool = False
    description: Optional[str] = Field(None, max_length=4000)
    display_order: int = 0

    model_config = {"extra": "forbid"}


class WorkHistoryOut(BaseModel):
    id: str
    employer: str
    job_title: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: bool
    description: Optional[str] = None
    display_order: int

    model_config = {"from_attributes": True}


class EducationHistoryIn(BaseModel):
    institution: str = Field(..., min_length=1, max_length=255)
    # Never required: the imported CSV may only ever have a single
    # free-text "Education" value.
    degree: Optional[str] = Field(None, max_length=255)
    field_of_study: Optional[str] = Field(None, max_length=255)
    start_year: Optional[int] = None
    graduation_year: Optional[int] = None
    is_current: bool = False
    display_order: int = 0

    model_config = {"extra": "forbid"}


class EducationHistoryOut(BaseModel):
    id: str
    institution: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_year: Optional[int] = None
    graduation_year: Optional[int] = None
    is_current: bool
    display_order: int

    model_config = {"from_attributes": True}


class PrivacySettingsIn(BaseModel):
    show_email: Optional[bool] = None
    show_phone: Optional[bool] = None
    show_birthday: Optional[bool] = None
    show_location: Optional[bool] = None
    show_current_employer: Optional[bool] = None
    show_job_title: Optional[bool] = None
    show_education: Optional[bool] = None
    show_linkedin: Optional[bool] = None
    show_social_links: Optional[bool] = None
    show_work_history: Optional[bool] = None
    show_education_history: Optional[bool] = None

    model_config = {"extra": "forbid"}


class PrivacySettingsOut(BaseModel):
    show_email: bool
    show_phone: bool
    show_birthday: bool
    show_location: bool
    show_current_employer: bool
    show_job_title: bool
    show_education: bool
    show_linkedin: bool
    show_social_links: bool
    show_work_history: bool
    show_education_history: bool

    model_config = {"from_attributes": True}


class UserProfileUpdateRequest(BaseModel):
    """Fields the authenticated user may edit on their own profile.
    Linking fields (alumni_id/link_status/...) are deliberately absent -
    they are only ever changed via the matching endpoints below."""

    profile_photo_url: Optional[str] = Field(None, max_length=512)
    first_name: Optional[str] = Field(None, max_length=128)
    last_name: Optional[str] = Field(None, max_length=128)
    preferred_name: Optional[str] = Field(None, max_length=128)
    headline: Optional[str] = Field(None, max_length=255)
    current_city: Optional[str] = Field(None, max_length=128)
    current_state: Optional[str] = Field(None, max_length=64)
    current_country: Optional[str] = Field(None, max_length=128)
    current_job_title: Optional[str] = Field(None, max_length=255)
    current_employer: Optional[str] = Field(None, max_length=255)
    current_university: Optional[str] = Field(None, max_length=255)
    bio: Optional[str] = Field(None, max_length=4000)

    primary_email: Optional[str] = Field(None, max_length=255)
    secondary_email: Optional[str] = Field(None, max_length=255)
    phone_number: Optional[str] = Field(None, max_length=32)
    personal_website: Optional[str] = Field(None, max_length=512)

    birthday: Optional[date] = None
    pronouns: Optional[str] = Field(None, max_length=64)
    hometown: Optional[str] = Field(None, max_length=255)

    linkedin_url: Optional[str] = Field(None, max_length=512)
    github_url: Optional[str] = Field(None, max_length=512)
    instagram_url: Optional[str] = Field(None, max_length=512)
    x_url: Optional[str] = Field(None, max_length=512)
    personal_website_url: Optional[str] = Field(None, max_length=512)

    available_to_speak: Optional[bool] = None
    available_to_mentor: Optional[bool] = None
    speaker_topics: Optional[str] = Field(None, max_length=1000)
    mentoring_topics: Optional[str] = Field(None, max_length=1000)
    preferred_engagement_types: Optional[str] = Field(None, max_length=500)

    model_config = {"extra": "forbid"}


class UserProfileOut(BaseModel):
    id: str
    user_id: str

    profile_photo_url: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    preferred_name: Optional[str] = None
    headline: Optional[str] = None
    current_city: Optional[str] = None
    current_state: Optional[str] = None
    current_country: Optional[str] = None
    current_job_title: Optional[str] = None
    current_employer: Optional[str] = None
    current_university: Optional[str] = None
    bio: Optional[str] = None

    primary_email: Optional[str] = None
    secondary_email: Optional[str] = None
    phone_number: Optional[str] = None
    personal_website: Optional[str] = None

    birthday: Optional[date] = None
    pronouns: Optional[str] = None
    hometown: Optional[str] = None

    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    instagram_url: Optional[str] = None
    x_url: Optional[str] = None
    personal_website_url: Optional[str] = None

    available_to_speak: bool
    available_to_mentor: bool
    speaker_topics: Optional[str] = None
    mentoring_topics: Optional[str] = None
    preferred_engagement_types: Optional[str] = None

    privacy: PrivacySettingsOut

    alumni_id: Optional[str] = None
    link_status: str
    link_confidence: Optional[int] = None
    linked_at: Optional[datetime] = None
    linked_by: Optional[str] = None
    needs_review: bool

    work_history: list[WorkHistoryOut] = []
    education_history: list[EducationHistoryOut] = []

    model_config = {"from_attributes": True}


class MatchCandidateOut(BaseModel):
    alumni_id: str
    full_name: str
    university: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    match_type: str
    score: int
    matched_signals: list[str]


class FindMatchResponse(BaseModel):
    link_status: str
    candidates: list[MatchCandidateOut]


class LinkActionResponse(BaseModel):
    link_status: str
    alumni_id: Optional[str] = None
    link_confidence: Optional[int] = None
    linked_at: Optional[datetime] = None
    linked_by: Optional[str] = None
    needs_review: bool = False


class PublicWorkHistoryOut(BaseModel):
    employer: str
    job_title: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: bool
    description: Optional[str] = None


class PublicEducationHistoryOut(BaseModel):
    institution: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_year: Optional[int] = None
    graduation_year: Optional[int] = None
    is_current: bool


class PublicProfileOut(BaseModel):
    """Combines safe imported directory fields with public,
    privacy-filtered user-supplied fields. Every private field
    (email/phone/birthday/etc.) is simply omitted (None) unless the
    profile owner has explicitly enabled it - never included and masked
    client-side."""

    alumni_id: str
    full_name: str
    has_user_profile: bool

    # Safe imported directory fields (already visible to any
    # authenticated user via GET /alumni-data - not newly exposed here).
    university: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    display_location: Optional[str] = None
    verification_status: Optional[str] = None

    # Public, privacy-gated user-supplied fields.
    profile_photo_url: Optional[str] = None
    preferred_name: Optional[str] = None
    headline: Optional[str] = None
    bio: Optional[str] = None
    pronouns: Optional[str] = None
    hometown: Optional[str] = None

    email: Optional[str] = None
    phone_number: Optional[str] = None
    birthday: Optional[date] = None

    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    instagram_url: Optional[str] = None
    x_url: Optional[str] = None
    personal_website_url: Optional[str] = None

    available_to_speak: bool = False
    available_to_mentor: bool = False
    speaker_topics: Optional[str] = None
    mentoring_topics: Optional[str] = None

    work_history: list[PublicWorkHistoryOut] = []
    education_history: list[PublicEducationHistoryOut] = []


class AdminProfileLinkActionOut(BaseModel):
    user_profile_id: str
    alumni_id: Optional[str] = None
    link_status: str
    linked_at: Optional[datetime] = None
    linked_by: Optional[str] = None
    needs_review: bool = False


class AdminProfileMatchCandidateOut(BaseModel):
    user_profile_id: str
    user_id: str
    username: str
    profile_full_name: Optional[str] = None
    link_status: str
    needs_review: bool
    candidates: list[MatchCandidateOut]
