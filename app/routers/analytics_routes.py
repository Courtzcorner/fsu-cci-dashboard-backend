"""
Dashboard analytics, computed entirely with SQL aggregation (COUNT/GROUP BY)
against the currently active alumni dataset - never by loading every
alumni row into Python and counting in a loop. This is what lets a
75,000-row active dataset return a bounded-size summary in one query per
metric instead of materializing the whole table on every request.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, get_organization_by_slug_for_current_user
from app.models.alumni import Alumni, AlumniOrganization
from app.models.audit import CSVImport
from app.models.organization import Organization
from app.schemas.analytics import (
    AnalyticsSummary,
    CityCount,
    DataQuality,
    DatasetInfo,
    LocationCount,
    LocationNormalizationCoverage,
    LocationsSummary,
    MetroAreaCount,
    NamedCount,
    StateCount,
    Totals,
)

router = APIRouter(tags=["analytics"])

TOP_N_DEFAULT = 10
TOP_N_WIDE = 25


def _active_filter(organization_id: str):
    return (AlumniOrganization.organization_id == organization_id, Alumni.is_active.is_(True))


def _base_query(db: Session, organization_id: str):
    return (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(organization_id))
    )


def _grouped_counts(db: Session, organization_id: str, column, limit: Optional[int] = None) -> list[tuple]:
    """SELECT <column>, COUNT(*) ... GROUP BY <column> ORDER BY COUNT DESC.
    Blank/null values are excluded - they belong in data_quality, not a
    named bucket."""
    query = (
        db.query(column, func.count(Alumni.id))
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(organization_id), column.isnot(None))
        .group_by(column)
        # Secondary sort key makes tie-breaking deterministic (e.g. many
        # distinct values each with count=1) instead of depending on
        # unspecified database row-scan order.
        .order_by(func.count(Alumni.id).desc(), column.asc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()


def _count_where(db: Session, organization_id: str, *extra_filters) -> int:
    return (
        db.query(func.count(Alumni.id))
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(organization_id), *extra_filters)
        .scalar()
        or 0
    )


def _distinct_count(db: Session, organization_id: str, column) -> int:
    return (
        db.query(func.count(func.distinct(column)))
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(organization_id), column.isnot(None))
        .scalar()
        or 0
    )


def _latest_csv_import(db: Session, organization_id: str) -> Optional[CSVImport]:
    """Only successful imports ever write a CSVImport row, so "most recent
    by created_at" is always "the import currently controlling the
    dashboard" - matching GET /admin/current-import exactly."""
    return (
        db.query(CSVImport)
        .filter(CSVImport.organization_id == organization_id)
        .order_by(CSVImport.created_at.desc())
        .first()
    )


@router.get("/analytics/summary", response_model=AnalyticsSummary)
def get_analytics_summary(
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyticsSummary:
    org_id = organization.id

    total_alumni = _count_where(db, org_id)
    verified_alumni = _count_where(db, org_id, Alumni.verified.is_(True))
    verification_percentage = round((verified_alumni / total_alumni) * 100, 2) if total_alumni else 0.0

    latest_import = _latest_csv_import(db, org_id)
    dataset = DatasetInfo(
        csv_import_id=latest_import.id if latest_import else None,
        filename=latest_import.filename if latest_import else None,
        imported_at=latest_import.created_at.isoformat() if latest_import and latest_import.created_at else None,
        total_alumni=total_alumni,
    )

    totals = Totals(
        alumni=total_alumni,
        universities=_distinct_count(db, org_id, Alumni.university),
        cities=_distinct_count(db, org_id, Alumni.city),
        verified=verified_alumni,
    )

    data_quality = DataQuality(
        with_company=_count_where(db, org_id, Alumni.company.isnot(None)),
        with_job_title=_count_where(db, org_id, Alumni.job_title.isnot(None)),
        with_location=_count_where(db, org_id, Alumni.location_original.isnot(None)),
        with_university=_count_where(db, org_id, Alumni.university.isnot(None)),
        with_linkedin=_count_where(db, org_id, Alumni.linkedin_url.isnot(None)),
        unclassified_industry=_count_where(db, org_id, Alumni.industry.is_(None)),
        unclassified_seniority=_count_where(db, org_id, Alumni.seniority.is_(None)),
    )

    top_companies = [
        NamedCount(name=name, count=count) for name, count in _grouped_counts(db, org_id, Alumni.company, TOP_N_DEFAULT)
    ]
    industries = [
        NamedCount(name=name, count=count) for name, count in _grouped_counts(db, org_id, Alumni.industry, TOP_N_DEFAULT)
    ]
    seniority = [
        NamedCount(name=name, count=count) for name, count in _grouped_counts(db, org_id, Alumni.seniority, TOP_N_WIDE)
    ]
    universities = [
        NamedCount(name=name, count=count)
        for name, count in _grouped_counts(db, org_id, Alumni.university, TOP_N_WIDE)
    ]

    city_rows = (
        db.query(Alumni.city, Alumni.state, Alumni.state_code, func.count(Alumni.id))
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(org_id), Alumni.city.isnot(None))
        .group_by(Alumni.city, Alumni.state, Alumni.state_code)
        .order_by(func.count(Alumni.id).desc(), Alumni.city.asc())
        .limit(TOP_N_WIDE)
        .all()
    )
    cities = [
        CityCount(city=city, state=state, state_code=state_code, count=count)
        for city, state, state_code, count in city_rows
    ]

    state_rows = _grouped_counts(db, org_id, Alumni.state_code, TOP_N_WIDE)
    states = [StateCount(state_code=state_code, count=count) for state_code, count in state_rows]

    # --- Legacy fields (kept for backward compatibility) ---
    metro_rows = _grouped_counts(db, org_id, Alumni.metro_area, TOP_N_DEFAULT)
    top_metro_areas = [MetroAreaCount(name=name, count=count) for name, count in metro_rows]

    grad_year_rows = _grouped_counts(db, org_id, Alumni.graduation_year)
    graduation_year_distribution = [
        NamedCount(name=str(year), count=count) for year, count in sorted(grad_year_rows, key=lambda r: r[0])
    ]

    major_rows = _grouped_counts(db, org_id, Alumni.major, TOP_N_WIDE)
    major_distribution = [NamedCount(name=name, count=count) for name, count in major_rows]

    normalization_rows = _grouped_counts(db, org_id, Alumni.location_normalization_status)
    normalization_counts = dict(normalization_rows)

    avg_completion = (
        db.query(func.avg(Alumni.profile_completion))
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(org_id))
        .scalar()
    )
    average_profile_completion = round(float(avg_completion), 2) if avg_completion is not None else 0.0

    top_states_legacy = [NamedCount(name=state_code, count=count) for state_code, count in state_rows]
    top_cities_legacy = [
        CityCount(city=city, state=state, state_code=state_code, count=count)
        for city, state, state_code, count in city_rows
    ]

    return AnalyticsSummary(
        organization=organization.slug,
        dataset=dataset,
        totals=totals,
        top_companies=top_companies,
        industries=industries,
        seniority=seniority,
        universities=universities,
        cities=cities,
        states=states,
        data_quality=data_quality,
        # legacy
        total_alumni=total_alumni,
        verified_alumni=verified_alumni,
        verification_percentage=verification_percentage,
        location_normalization=LocationNormalizationCoverage(
            normalized=normalization_counts.get("normalized", 0),
            partially_normalized=normalization_counts.get("partially_normalized", 0),
            remote=normalization_counts.get("remote", 0),
            international=normalization_counts.get("international", 0),
            missing=normalization_counts.get("missing", 0),
            ambiguous=normalization_counts.get("ambiguous", 0),
            failed=normalization_counts.get("failed", 0),
        ),
        top_industries=industries,
        top_cities=top_cities_legacy,
        top_states=top_states_legacy,
        top_metro_areas=top_metro_areas,
        graduation_year_distribution=graduation_year_distribution,
        major_distribution=major_distribution,
        seniority_distribution=seniority,
        average_profile_completion=average_profile_completion,
    )


@router.get("/analytics/locations", response_model=LocationsSummary)
def get_analytics_locations(
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LocationsSummary:
    """Aggregate, map-ready location data: one row per distinct
    (city, state, coordinates) group with a count - never one entry per
    alumni record - so the frontend map never has to fetch or process
    every alumni row itself.

    Coordinates come only from this alumni's own `latitude`/`longitude`
    columns, which are populated exclusively by
    app.services.location_normalization_service's controlled geography
    lookup during import - never invented here. Alumni without resolved
    coordinates are excluded from `locations` and counted in
    `without_location` instead of being plotted at a guessed point.
    """
    org_id = organization.id

    total_active = _count_where(db, org_id)
    with_location = _count_where(db, org_id, Alumni.latitude.isnot(None), Alumni.longitude.isnot(None))
    without_location = total_active - with_location

    rows = (
        db.query(
            Alumni.city, Alumni.state, Alumni.state_code, Alumni.latitude, Alumni.longitude, func.count(Alumni.id)
        )
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(*_active_filter(org_id), Alumni.latitude.isnot(None), Alumni.longitude.isnot(None))
        .group_by(Alumni.city, Alumni.state, Alumni.state_code, Alumni.latitude, Alumni.longitude)
        .order_by(func.count(Alumni.id).desc())
        .all()
    )
    locations = [
        LocationCount(city=city, state=state, state_code=state_code, latitude=lat, longitude=lng, count=count)
        for city, state, state_code, lat, lng, count in rows
    ]

    return LocationsSummary(locations=locations, with_location=with_location, without_location=without_location)
