from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, get_organization_by_slug_for_current_user
from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.schemas.alumni import AlumniListMeta, AlumniListResponse, AlumniOut
from app.services.effective_alumni_service import (
    EffectiveColumns,
    confirmed_profile_alias,
    confirmed_profile_join_condition,
)

router = APIRouter(tags=["alumni"])


@router.get("/alumni-data", response_model=AlumniListResponse)
def get_alumni_data(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=5000),
    graduation_year: Optional[int] = None,
    major: Optional[str] = None,
    university: Optional[str] = None,
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

    Every display/filterable field below is an "effective" value: the
    imported Alumni value, overridden by a CONFIRMED self-service
    UserProfile's own field only when the owner has not marked it
    private (see app.services.effective_alumni_service). The imported
    Alumni row itself is never modified - this is a read-only SQL
    CASE/COALESCE join, entirely additive to the CSV import pipeline.
    """
    # Only alumni from the most recently uploaded ("replace mode") CSV
    # import are ever returned - alumni deactivated by a newer upload are
    # excluded so the dashboard always reflects exactly one authoritative,
    # active dataset. Because this filter always applies, a confirmed
    # profile link whose alumni record has since been deactivated never
    # contributes an override here either - it simply isn't in this query
    # at all (see effective_alumni_service module docstring).
    profile_alias = confirmed_profile_alias()
    eff = EffectiveColumns(profile_alias)

    query = (
        db.query(
            Alumni,
            eff.full_name,
            eff.company,
            eff.job_title,
            eff.university,
            eff.city,
            eff.state,
            eff.linkedin_url,
            eff.seniority,
            eff.career_category,
            eff.industry,
            profile_alias.id,
            profile_alias.profile_photo_url,
        )
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .outerjoin(profile_alias, confirmed_profile_join_condition(profile_alias))
        .filter(
            AlumniOrganization.organization_id == organization.id,
            Alumni.is_active.is_(True),
        )
    )

    if graduation_year is not None:
        query = query.filter(Alumni.graduation_year == graduation_year)
    if major:
        query = query.filter(Alumni.major.ilike(f"%{major}%"))
    if university:
        query = query.filter(eff.university.ilike(f"%{university}%"))
    if industry:
        query = query.filter(eff.industry.ilike(f"%{industry}%"))
    if career_category:
        query = query.filter(eff.career_category.ilike(f"%{career_category}%"))
    if seniority:
        query = query.filter(eff.seniority.ilike(f"%{seniority}%"))
    if company:
        query = query.filter(eff.company.ilike(f"%{company}%"))
    if city:
        query = query.filter(eff.city.ilike(f"%{city}%"))
    if state:
        query = query.filter(or_(eff.state.ilike(f"%{state}%"), Alumni.state_code.ilike(f"%{state}%")))
    if metro_area:
        query = query.filter(Alumni.metro_area.ilike(f"%{metro_area}%"))
    if verified is not None:
        query = query.filter(Alumni.verified == verified)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                eff.full_name.ilike(like),
                eff.company.ilike(like),
                eff.job_title.ilike(like),
            )
        )

    total = query.count()
    rows = (
        query.order_by(eff.full_name.asc())
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
            full_name=full_name,
            graduation_year=record.graduation_year,
            major=record.major,
            degree=record.degree,
            university=university_val,
            job_title=job_title,
            company=company_val,
            industry=industry_val,
            career_category=career_category_val,
            seniority=seniority_val,
            location_original=record.location_original,
            city=city_val,
            state=state_val,
            state_code=record.state_code,
            country=record.country,
            metro_area=record.metro_area,
            display_location=record.display_location,
            latitude=record.latitude,
            longitude=record.longitude,
            location_normalization_status=record.location_normalization_status,
            linkedin_url=linkedin_url_val,
            verified=record.verified,
            verification_status=record.verification_status,
            verification_date=record.verification_date,
            profile_completion=record.profile_completion,
            has_public_profile=linked_profile_id is not None,
            public_profile_url=f"/alumni/{record.id}" if linked_profile_id is not None else None,
            profile_photo_url=profile_photo_url,
        )
        for (
            record, full_name, company_val, job_title, university_val, city_val, state_val, linkedin_url_val,
            seniority_val, career_category_val, industry_val, linked_profile_id, profile_photo_url,
        ) in rows
    ]

    total_pages = (total + page_size - 1) // page_size if total else 0
    return AlumniListResponse(
        data=data,
        meta=AlumniListMeta(
            organization=organization.slug, total=total, page=page, page_size=page_size, total_pages=total_pages
        ),
    )
