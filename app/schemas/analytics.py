from typing import Optional

from pydantic import BaseModel, Field


class NamedCount(BaseModel):
    name: str
    count: int


class CityCount(BaseModel):
    city: str
    state: Optional[str] = None
    state_code: Optional[str] = None
    count: int


class StateCount(BaseModel):
    state_code: Optional[str] = None
    name: Optional[str] = None
    count: int


class MetroAreaCount(BaseModel):
    name: str
    count: int


class LocationNormalizationCoverage(BaseModel):
    normalized: int = 0
    partially_normalized: int = 0
    remote: int = 0
    international: int = 0
    missing: int = 0
    ambiguous: int = 0
    failed: int = 0


class DatasetInfo(BaseModel):
    """Identifies exactly which CSV import this summary was computed
    from - the same import that GET /admin/current-import reports as the
    active dataset."""

    csv_import_id: Optional[str] = None
    filename: Optional[str] = None
    imported_at: Optional[str] = None
    total_alumni: int = 0


class Totals(BaseModel):
    alumni: int = 0
    universities: int = 0
    cities: int = 0
    verified: int = 0


class DataQuality(BaseModel):
    """Every count here is a direct SQL COUNT over the active dataset -
    never estimated. `unclassified_industry`/`unclassified_seniority`
    count rows where the deterministic rules in
    app.services.classification_service found no match (source="unknown"),
    never a fabricated value."""

    with_company: int = 0
    with_job_title: int = 0
    with_location: int = 0
    with_university: int = 0
    with_linkedin: int = 0
    unclassified_industry: int = 0
    unclassified_seniority: int = 0


class AnalyticsSummary(BaseModel):
    organization: str
    dataset: DatasetInfo
    totals: Totals
    top_companies: list[NamedCount] = []
    industries: list[NamedCount] = []
    seniority: list[NamedCount] = []
    universities: list[NamedCount] = []
    cities: list[CityCount] = []
    states: list[StateCount] = []
    data_quality: DataQuality

    # --- Legacy fields, kept for backward compatibility with earlier
    # frontend builds. New consumers should use the fields above. ---
    total_alumni: int = 0
    verified_alumni: int = 0
    verification_percentage: float = 0.0
    location_normalization: LocationNormalizationCoverage = Field(default_factory=LocationNormalizationCoverage)
    top_industries: list[NamedCount] = []
    top_cities: list[CityCount] = []
    top_states: list[NamedCount] = []
    top_metro_areas: list[MetroAreaCount] = []
    graduation_year_distribution: list[NamedCount] = []
    major_distribution: list[NamedCount] = []
    seniority_distribution: list[NamedCount] = []
    average_profile_completion: float = 0.0


class LocationCount(BaseModel):
    city: str
    state: Optional[str] = None
    state_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    count: int


class LocationsSummary(BaseModel):
    """Aggregate map data: one grouped marker per distinct
    (city, state, coordinates) rather than one entry per alumni row, so
    the frontend map never has to process every record itself."""

    locations: list[LocationCount]
    with_location: int
    without_location: int
