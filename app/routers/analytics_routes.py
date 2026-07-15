from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser, get_current_user, get_organization_by_slug_for_current_user
from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.schemas.analytics import (
    AnalyticsSummary,
    CityCount,
    LocationNormalizationCoverage,
    MetroAreaCount,
    NamedCount,
)

router = APIRouter(tags=["analytics"])


def _top_n(counter: Counter, n: int = 10) -> list[tuple[str, int]]:
    return counter.most_common(n)


@router.get("/analytics/summary", response_model=AnalyticsSummary)
def get_analytics_summary(
    organization: Organization = Depends(get_organization_by_slug_for_current_user),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalyticsSummary:
    records = (
        db.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id)
        .all()
    )

    total_alumni = len(records)
    verified_alumni = sum(1 for r in records if r.verified)
    verification_percentage = round((verified_alumni / total_alumni) * 100, 2) if total_alumni else 0.0

    normalization_counts = Counter(r.location_normalization_status for r in records)

    industry_counter = Counter(r.industry for r in records if r.industry)
    company_counter = Counter(r.company for r in records if r.company)

    # Cities are grouped by (city, state) so distinct localities like
    # Brooklyn and New York City are never merged, even though they share
    # a metro area.
    city_counter: Counter[tuple[str, str | None]] = Counter(
        (r.city, r.state) for r in records if r.city
    )
    state_counter = Counter(r.state for r in records if r.state)
    metro_counter = Counter(r.metro_area for r in records if r.metro_area)

    grad_year_counter = Counter(str(r.graduation_year) for r in records if r.graduation_year)
    major_counter = Counter(r.major for r in records if r.major)
    seniority_counter = Counter(r.seniority for r in records if r.seniority)

    average_profile_completion = (
        round(sum(r.profile_completion for r in records) / total_alumni, 2) if total_alumni else 0.0
    )

    return AnalyticsSummary(
        organization=organization.slug,
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
        top_industries=[NamedCount(name=name, count=count) for name, count in _top_n(industry_counter)],
        top_companies=[NamedCount(name=name, count=count) for name, count in _top_n(company_counter)],
        top_cities=[
            CityCount(city=city, state=state, count=count)
            for (city, state), count in _top_n(city_counter)
        ],
        top_states=[NamedCount(name=name, count=count) for name, count in _top_n(state_counter)],
        top_metro_areas=[MetroAreaCount(name=name, count=count) for name, count in _top_n(metro_counter)],
        graduation_year_distribution=[
            NamedCount(name=name, count=count) for name, count in sorted(grad_year_counter.items())
        ],
        major_distribution=[NamedCount(name=name, count=count) for name, count in _top_n(major_counter, 25)],
        seniority_distribution=[NamedCount(name=name, count=count) for name, count in _top_n(seniority_counter, 25)],
        average_profile_completion=average_profile_completion,
    )
