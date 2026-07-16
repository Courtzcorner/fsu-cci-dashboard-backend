from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class EventOut(BaseModel):
    id: str
    organization_id: str
    title: str
    description: Optional[str] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    location: Optional[str] = None
    virtual_url: Optional[str] = None
    registration_url: Optional[str] = None
    event_type: Optional[str] = None
    is_published: bool
    created_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    start_date: datetime
    end_date: Optional[datetime] = None
    location: Optional[str] = Field(None, max_length=255)
    virtual_url: Optional[str] = Field(None, max_length=512)
    registration_url: Optional[str] = Field(None, max_length=512)
    event_type: Optional[str] = Field(None, max_length=64)
    is_published: bool = False


class EventUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    location: Optional[str] = Field(None, max_length=255)
    virtual_url: Optional[str] = Field(None, max_length=512)
    registration_url: Optional[str] = Field(None, max_length=512)
    event_type: Optional[str] = Field(None, max_length=64)
    is_published: Optional[bool] = None

    model_config = {"extra": "forbid"}


class SpeakerOut(BaseModel):
    id: str
    organization_id: str
    name: str
    job_title: Optional[str] = None
    company: Optional[str] = None
    bio: Optional[str] = None
    profile_image_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    speaking_topics: Optional[str] = None
    availability_status: Optional[str] = None
    is_published: bool
    created_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SpeakerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    job_title: Optional[str] = Field(None, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    bio: Optional[str] = Field(None, max_length=4000)
    profile_image_url: Optional[str] = Field(None, max_length=512)
    linkedin_url: Optional[str] = Field(None, max_length=512)
    speaking_topics: Optional[str] = Field(None, max_length=1000)
    availability_status: Optional[str] = Field(None, max_length=64)
    is_published: bool = False


class SpeakerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    job_title: Optional[str] = Field(None, max_length=255)
    company: Optional[str] = Field(None, max_length=255)
    bio: Optional[str] = Field(None, max_length=4000)
    profile_image_url: Optional[str] = Field(None, max_length=512)
    linkedin_url: Optional[str] = Field(None, max_length=512)
    speaking_topics: Optional[str] = Field(None, max_length=1000)
    availability_status: Optional[str] = Field(None, max_length=64)
    is_published: Optional[bool] = None

    model_config = {"extra": "forbid"}


class SuperStarOut(BaseModel):
    id: str
    organization_id: str
    alumni_id: str
    headline: str
    description: Optional[str] = None
    featured_image_url: Optional[str] = None
    is_published: bool
    featured_at: Optional[date] = None
    created_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SuperStarCreate(BaseModel):
    alumni_id: str
    headline: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    featured_image_url: Optional[str] = Field(None, max_length=512)
    is_published: bool = False
    featured_at: Optional[date] = None


class SuperStarUpdate(BaseModel):
    headline: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    featured_image_url: Optional[str] = Field(None, max_length=512)
    is_published: Optional[bool] = None
    featured_at: Optional[date] = None

    model_config = {"extra": "forbid"}
