"""
Dashboard analytics, computed entirely with SQL aggregation (COUNT/GROUP BY)
against the currently active alumni dataset - never by loading every
alumni row into Python and counting in a loop. This is what lets a
75,000-row active dataset return a bounded-size summary in one query per
metric instead of materializing the whole table on every request.

Every metric below is computed from "effective" values (see
app.services.effective_alumni_service): the imported Alumni value,
overridden by a CONFIRMED self-service UserProfile's own field only when
the owner has not marked it private. A profile edit therefore shows up
here immediately, with no new CSV upload required - and an
unmatched/candidate/rejected/conflict profile, or a private field,
never affects analytics at all.
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
    CompanyGroup,
    CompanyIndustryOverview,
    DataQuality,
    DatasetInfo,
    EmployerConcentration,
    LocationCount,
    LocationNormalizationCoverage,
    LocationsSummary,
    MetroAreaCount,
    NamedCount,
    SeniorityIndustryCount,
    StateCount,
    Totals,
)
from app.services.company_placeholder_policy import company_placeholder_sql_exclusion
from app.services.effective_alumni_service import (
    EffectiveColumns,
    confirmed_profile_alias,
    confirmed_profile_join_condition,
)

router = APIRouter(tags=["analytics"])

TOP_N_DEFAULT = 10
TOP_N_WIDE = 25
# Combined Companies + Industries page.
TOP_COMPANIES_LIMIT = 15
TOP_INDUSTRIES_FOR_BREAKDOWN = 8
TOP_COMPANIES_PER_INDUSTRY = 5
# Safety cap only - the number of distinct (industry, seniority) pairs is
# inherently small and bounded regardless of active dataset size, but a
# LIMIT keeps this deterministic and defensive against a future industry
# taxonomy blowing up in size.
SENIORITY_BY_INDUSTRY_LIMIT = 200


def _active_filter(organization_id: str):
    return (AlumniOrganization.organization_id == organization_id, Alumni.is_active.is_(True))


def _with_profile_join(query, profile_alias):
    return query.outerjoin(profile_alias, confirmed_profile_join_condition(profile_alias))


def _grouped_counts(
    db: Session,
    organization_id: str,
    column,
    profile_alias,
    limit: Optional[int] = None,
    extra_filters: tuple = (),
) -> list[tuple]:
    """SELECT <column>, COUNT(*) ... GROUP BY <column> ORDER BY COUNT DESC.
    Blank/null values are excluded - they belong in data_quality, not a
    named bucket. `column` may be a raw Alumni column or an "effective"
    CASE expression from app.services.effective_alumni_service.

    `extra_filters` is an additive, opt-in hook (empty by default, so
    every existing call site is unaffected) - used ONLY to exclude
    placeholder values from company-derived aggregations (see
    app.services.company_placeholder_policy). Never used for any other
    field."""
    query = (
        _with_profile_join(
            db.query(column, func.count(Alumni.id))
            .select_from(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
            profile_alias,
        )
        .filter(*_active_filter(organization_id), column.isnot(None), *extra_filters)
        .group_by(column)
    )
    # Secondary sort key makes tie-breaking deterministic (e.g. many
    # distinct values each with count=1) instead of depending on
    # unspecified database row-scan order.
    query = query.order_by(func.count(Alumni.id).desc(), column.asc())
    if limit:
        query = query.limit(limit)
    return query.all()


def _count_where(db: Session, organization_id: str, profile_alias, *extra_filters) -> int:
    query = _with_profile_join(
        db.query(func.count(Alumni.id))
        .select_from(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
        profile_alias,
    )
    return query.filter(*_active_filter(organization_id), *extra_filters).scalar() or 0


def _distinct_count(
    db: Session, organization_id: str, column, profile_alias, extra_filters: tuple = ()
) -> int:
    """`extra_filters` is the same additive, opt-in hook as in
    _grouped_counts above - empty by default, used only to exclude
    placeholder values from a company-derived distinct count."""
    query = _with_profile_join(
        db.query(func.count(func.distinct(column)))
        .select_from(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
        profile_alias,
    )
    return query.filter(*_active_filter(organization_id), column.isnot(None), *extra_filters).scalar() or 0


def _top_companies_by_industry(db: Session, organization_id: str, eff, profile_alias) -> list[CompanyGroup]:
    """One aggregated GROUP BY (industry, company) query - bounded by the
    number of distinct (industry, company) pairs in the active dataset,
    never by alumni row count - then the "largest industries first" /
    "top N companies per industry" trimming happens in Python over that
    already-small, already-sorted result set (never over raw alumni
    rows). `eff.industry` only ever holds an imported or
    admin-verified-company-mapping value (see
    app.services.classification_service.resolve_industry) - never a
    keyword guess from the company name - so no extra filtering is
    needed here to satisfy "verified or imported industry values only".
    A placeholder employer value (see
    app.services.company_placeholder_policy) is excluded here too - it
    is never a real company, regardless of whether it happens to also
    have an industry value attached.
    """
    rows = (
        _with_profile_join(
            db.query(eff.industry, eff.company, func.count(Alumni.id))
            .select_from(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
            profile_alias,
        )
        .filter(
            *_active_filter(organization_id),
            eff.industry.isnot(None),
            eff.company.isnot(None),
            company_placeholder_sql_exclusion(eff.company),
        )
        .group_by(eff.industry, eff.company)
        .order_by(eff.industry.asc(), func.count(Alumni.id).desc(), eff.company.asc())
        .all()
    )

    companies_by_industry: dict[str, list[NamedCount]] = {}
    totals_by_industry: dict[str, int] = {}
    for industry, company, count in rows:
        companies_by_industry.setdefault(industry, []).append(NamedCount(name=company, count=count))
        totals_by_industry[industry] = totals_by_industry.get(industry, 0) + count

    largest_industries_first = sorted(totals_by_industry, key=lambda name: (-totals_by_industry[name], name))
    return [
        CompanyGroup(industry=industry, companies=companies_by_industry[industry][:TOP_COMPANIES_PER_INDUSTRY])
        for industry in largest_industries_first[:TOP_INDUSTRIES_FOR_BREAKDOWN]
    ]


def _seniority_by_industry(db: Session, organization_id: str, eff, profile_alias) -> list[SeniorityIndustryCount]:
    """One aggregated GROUP BY (industry, seniority) query. `eff.seniority`
    is always one of the existing deterministic title-rule/imported
    values (see app.services.classification_service) - nothing new is
    guessed here."""
    rows = (
        _with_profile_join(
            db.query(eff.industry, eff.seniority, func.count(Alumni.id))
            .select_from(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
            profile_alias,
        )
        .filter(*_active_filter(organization_id), eff.industry.isnot(None), eff.seniority.isnot(None))
        .group_by(eff.industry, eff.seniority)
        .order_by(eff.industry.asc(), func.count(Alumni.id).desc(), eff.seniority.asc())
        .limit(SENIORITY_BY_INDUSTRY_LIMIT)
        .all()
    )
    return [SeniorityIndustryCount(industry=industry, seniority=seniority, count=count) for industry, seniority, count in rows]


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
    profile_alias = confirmed_profile_alias()
    eff = EffectiveColumns(profile_alias)

    total_alumni = _count_where(db, org_id, profile_alias)
    verified_alumni = _count_where(db, org_id, profile_alias, Alumni.verified.is_(True))
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
        universities=_distinct_count(db, org_id, eff.university, profile_alias),
        cities=_distinct_count(db, org_id, eff.city, profile_alias),
        verified=verified_alumni,
    )

    data_quality = DataQuality(
        with_company=_count_where(
            db, org_id, profile_alias, eff.company.isnot(None), company_placeholder_sql_exclusion(eff.company)
        ),
        with_job_title=_count_where(db, org_id, profile_alias, eff.job_title.isnot(None)),
        with_location=_count_where(db, org_id, profile_alias, Alumni.location_original.isnot(None)),
        with_university=_count_where(db, org_id, profile_alias, eff.university.isnot(None)),
        with_linkedin=_count_where(db, org_id, profile_alias, eff.linkedin_url.isnot(None)),
        unclassified_industry=_count_where(db, org_id, profile_alias, eff.industry.is_(None)),
        unclassified_seniority=_count_where(db, org_id, profile_alias, eff.seniority.is_(None)),
    )

    # --- Combined Companies + Industries page ---
    unique_companies = _distinct_count(
        db, org_id, eff.company, profile_alias, extra_filters=(company_placeholder_sql_exclusion(eff.company),)
    )
    classified_industries = _distinct_count(db, org_id, eff.industry, profile_alias)
    alumni_with_company = data_quality.with_company
    alumni_with_industry = _count_where(db, org_id, profile_alias, eff.industry.isnot(None))
    company_industry_overview = CompanyIndustryOverview(
        unique_companies=unique_companies,
        classified_industries=classified_industries,
        alumni_with_company=alumni_with_company,
        alumni_with_industry=alumni_with_industry,
        company_coverage_percentage=(
            round((alumni_with_company / total_alumni) * 100, 1) if total_alumni else 0.0
        ),
        industry_coverage_percentage=(
            round((alumni_with_industry / total_alumni) * 100, 1) if total_alumni else 0.0
        ),
    )

    top_companies = [
        NamedCount(name=name, count=count)
        for name, count in _grouped_counts(
            db,
            org_id,
            eff.company,
            profile_alias,
            TOP_COMPANIES_LIMIT,
            extra_filters=(company_placeholder_sql_exclusion(eff.company),),
        )
    ]

    # Denominator is alumni WITH A KNOWN COMPANY, per spec - never the
    # full active dataset - so this reflects concentration among alumni
    # whose employer is actually known, not diluted by missing data.
    top_5_sum = sum(c.count for c in top_companies[:5])
    top_15_sum = sum(c.count for c in top_companies[:TOP_COMPANIES_LIMIT])
    employer_concentration = EmployerConcentration(
        top_5_company_share=(
            round((top_5_sum / alumni_with_company) * 100, 1) if alumni_with_company else 0.0
        ),
        top_15_company_share=(
            round((top_15_sum / alumni_with_company) * 100, 1) if alumni_with_company else 0.0
        ),
    )

    top_companies_by_industry = _top_companies_by_industry(db, org_id, eff, profile_alias)
    seniority_by_industry = _seniority_by_industry(db, org_id, eff, profile_alias)
    industries = [
        NamedCount(name=name, count=count)
        for name, count in _grouped_counts(db, org_id, eff.industry, profile_alias, TOP_N_DEFAULT)
    ]
    seniority = [
        NamedCount(name=name, count=count)
        for name, count in _grouped_counts(db, org_id, eff.seniority, profile_alias, TOP_N_WIDE)
    ]
    universities = [
        NamedCount(name=name, count=count)
        for name, count in _grouped_counts(db, org_id, eff.university, profile_alias, TOP_N_WIDE)
    ]

    city_rows = (
        _with_profile_join(
            db.query(eff.city, eff.state, Alumni.state_code, func.count(Alumni.id))
            .select_from(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
            profile_alias,
        )
        .filter(*_active_filter(org_id), eff.city.isnot(None))
        .group_by(eff.city, eff.state, Alumni.state_code)
        .order_by(func.count(Alumni.id).desc(), eff.city.asc())
        .limit(TOP_N_WIDE)
        .all()
    )
    cities = [
        CityCount(city=city, state=state, state_code=state_code, count=count)
        for city, state, state_code, count in city_rows
    ]

    state_rows = _grouped_counts(db, org_id, eff.state, profile_alias, TOP_N_WIDE)
    states = [StateCount(state_code=state_code, count=count) for state_code, count in state_rows]

    # --- Legacy fields (kept for backward compatibility) ---
    metro_rows = _grouped_counts(db, org_id, Alumni.metro_area, profile_alias, TOP_N_DEFAULT)
    top_metro_areas = [MetroAreaCount(name=name, count=count) for name, count in metro_rows]

    grad_year_rows = _grouped_counts(db, org_id, Alumni.graduation_year, profile_alias)
    graduation_year_distribution = [
        NamedCount(name=str(year), count=count) for year, count in sorted(grad_year_rows, key=lambda r: r[0])
    ]

    major_rows = _grouped_counts(db, org_id, Alumni.major, profile_alias, TOP_N_WIDE)
    major_distribution = [NamedCount(name=name, count=count) for name, count in major_rows]

    normalization_rows = _grouped_counts(db, org_id, Alumni.location_normalization_status, profile_alias)
    normalization_counts = dict(normalization_rows)

    avg_completion = (
        _with_profile_join(
            db.query(func.avg(Alumni.profile_completion))
            .select_from(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
            profile_alias,
        )
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
        company_industry_overview=company_industry_overview,
        employer_concentration=employer_concentration,
        top_companies_by_industry=top_companies_by_industry,
        seniority_by_industry=seniority_by_industry,
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
    (effective city, effective state, coordinates) group with a count -
    never one entry per alumni record - so the frontend map never has to
    fetch or process every alumni row itself.

    Coordinates come ONLY from this alumni's own imported
    `latitude`/`longitude` columns (populated exclusively by
    app.services.location_normalization_service's controlled geography
    lookup during import) - never invented here, and never geocoded from
    a profile-supplied city/state. A confirmed profile's city/state
    override changes which bucket a person is grouped into and counted
    under, but never manufactures new coordinates for that bucket.
    Alumni without resolved coordinates are excluded from `locations` and
    counted in `without_location` instead of being plotted at a guessed
    point.
    """
    org_id = organization.id
    profile_alias = confirmed_profile_alias()
    eff = EffectiveColumns(profile_alias)

    total_active = _count_where(db, org_id, profile_alias)
    with_location = _count_where(
        db, org_id, profile_alias, Alumni.latitude.isnot(None), Alumni.longitude.isnot(None)
    )
    without_location = total_active - with_location

    rows = (
        _with_profile_join(
            db.query(eff.city, eff.state, Alumni.state_code, Alumni.latitude, Alumni.longitude, func.count(Alumni.id))
            .select_from(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id),
            profile_alias,
        )
        .filter(*_active_filter(org_id), Alumni.latitude.isnot(None), Alumni.longitude.isnot(None))
        .group_by(eff.city, eff.state, Alumni.state_code, Alumni.latitude, Alumni.longitude)
        .order_by(func.count(Alumni.id).desc())
        .all()
    )
    locations = [
        LocationCount(city=city, state=state, state_code=state_code, latitude=lat, longitude=lng, count=count)
        for city, state, state_code, lat, lng, count in rows
    ]

    return LocationsSummary(locations=locations, with_location=with_location, without_location=without_location)
