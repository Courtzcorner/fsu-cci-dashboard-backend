from typing import Optional

from pydantic import BaseModel


class CityCount(BaseModel):
    city: str
    state: Optional[str] = None
    count: int


class MetroAreaCount(BaseModel):
    name: str
    count: int


class NamedCount(BaseModel):
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


class AnalyticsSummary(BaseModel):
    organization: str
    total_alumni: int
    verified_alumni: int
    verification_percentage: float
    location_normalization: LocationNormalizationCoverage
    top_industries: list[NamedCount]
    top_companies: list[NamedCount]
    top_cities: list[CityCount]
    top_states: list[NamedCount]
    top_metro_areas: list[MetroAreaCount]
    graduation_year_distribution: list[NamedCount]
    major_distribution: list[NamedCount]
    seniority_distribution: list[NamedCount]
    average_profile_completion: float
