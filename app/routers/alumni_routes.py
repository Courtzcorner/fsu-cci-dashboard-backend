from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, get_organization_by_slug_for_current_user
from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.schemas.alumni import AlumniListMeta, AlumniListResponse, AlumniOut

router = APIRouter(tags=["alumni"])


@router.get("/alumni-data", response_model=AlumniListResponse)
def get_alumni_data(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=5000),
    graduation_year: Optional[int] = None,
    major: Optional[str] = None,
    industry: Optional[str] = None,
    career_category: Optional[str] = None,
    seniority: Optional[str] = None,
    company: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    metro_area: Optional[str] = None,
    verified: Optional[bool] = None,
    search: Optional[str] = None,
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlumniListResponse:
    """Return alumni network data for a single organization. Requires a
    valid Bearer token, and the caller must have been granted access to
    the requested organization (see app.deps.get_organization_by_slug_for_current_user).
    """
    query = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id)
    )

    if graduation_year is not None:
        query = query.filter(Alumni.graduation_year == graduation_year)
    if major:
        query = query.filter(Alumni.major.ilike(f"%{major}%"))
    if industry:
        query = query.filter(Alumni.industry.ilike(f"%{industry}%"))
    if career_category:
        query = query.filter(Alumni.career_category.ilike(f"%{career_category}%"))
    if seniority:
        query = query.filter(Alumni.seniority.ilike(f"%{seniority}%"))
    if company:
        query = query.filter(Alumni.company.ilike(f"%{company}%"))
    if city:
        query = query.filter(Alumni.city.ilike(f"%{city}%"))
    if state:
        query = query.filter(or_(Alumni.state.ilike(f"%{state}%"), Alumni.state_code.ilike(f"%{state}%")))
    if metro_area:
        query = query.filter(Alumni.metro_area.ilike(f"%{metro_area}%"))
    if verified is not None:
        query = query.filter(Alumni.verified == verified)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Alumni.full_name.ilike(like),
                Alumni.company.ilike(like),
                Alumni.job_title.ilike(like),
            )
        )

    total = query.count()
    records = (
        query.order_by(Alumni.full_name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    data = [
        AlumniOut(
            id=record.id,
            organization=organization.slug,
            first_name=record.first_name,
            last_name=record.last_name,
            full_name=record.full_name,
            graduation_year=record.graduation_year,
            major=record.major,
            degree=record.degree,
            university=record.university,
            job_title=record.job_title,
            company=record.company,
            industry=record.industry,
            career_category=record.career_category,
            seniority=record.seniority,
            location_original=record.location_original,
            city=record.city,
            state=record.state,
            state_code=record.state_code,
            country=record.country,
            metro_area=record.metro_area,
            display_location=record.display_location,
            latitude=record.latitude,
            longitude=record.longitude,
            location_normalization_status=record.location_normalization_status,
            linkedin_url=record.linkedin_url,
            verified=record.verified,
            verification_status=record.verification_status,
            verification_date=record.verification_date,
            profile_completion=record.profile_completion,
        )
        for record in records
    ]

    return AlumniListResponse(
        data=data,
        meta=AlumniListMeta(organization=organization.slug, total=total, page=page, page_size=page_size),
    )
