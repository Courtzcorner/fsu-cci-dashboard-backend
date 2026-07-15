"""Seed data for the `location_aliases` table (and the in-process fallback
alias map used by the location normalization service when no database
session is available, e.g. in isolated unit tests).

Each entry is keyed by the *lowercased, trimmed* raw alias text and maps to
the canonical fields that should be applied verbatim (i.e. `location_original`
is intentionally left out - the caller always preserves the raw input).
"""
from app.services.us_geography import NEW_YORK_CITY_METRO

LOCATION_ALIAS_SEED_DATA: list[dict] = [
    {
        "alias": "nyc",
        "canonical_city": "New York City",
        "canonical_state": "New York",
        "state_code": "NY",
        "canonical_country": "United States",
        "metro_area": NEW_YORK_CITY_METRO,
    },
    {
        "alias": "new york, ny",
        "canonical_city": "New York City",
        "canonical_state": "New York",
        "state_code": "NY",
        "canonical_country": "United States",
        "metro_area": NEW_YORK_CITY_METRO,
    },
    {
        "alias": "new york city, ny",
        "canonical_city": "New York City",
        "canonical_state": "New York",
        "state_code": "NY",
        "canonical_country": "United States",
        "metro_area": NEW_YORK_CITY_METRO,
    },
    {
        "alias": "new york, new york",
        "canonical_city": "New York City",
        "canonical_state": "New York",
        "state_code": "NY",
        "canonical_country": "United States",
        "metro_area": NEW_YORK_CITY_METRO,
    },
    {
        "alias": "brooklyn, ny",
        "canonical_city": "Brooklyn",
        "canonical_state": "New York",
        "state_code": "NY",
        "canonical_country": "United States",
        "metro_area": NEW_YORK_CITY_METRO,
    },
    {
        "alias": "brooklyn, new york",
        "canonical_city": "Brooklyn",
        "canonical_state": "New York",
        "state_code": "NY",
        "canonical_country": "United States",
        "metro_area": NEW_YORK_CITY_METRO,
    },
    {
        "alias": "tallahassee, fl",
        "canonical_city": "Tallahassee",
        "canonical_state": "Florida",
        "state_code": "FL",
        "canonical_country": "United States",
        "metro_area": None,
    },
    {
        "alias": "washington, dc",
        "canonical_city": "Washington",
        "canonical_state": "District of Columbia",
        "state_code": "DC",
        "canonical_country": "United States",
        "metro_area": "Washington Metropolitan Area",
    },
    {
        "alias": "washington d.c.",
        "canonical_city": "Washington",
        "canonical_state": "District of Columbia",
        "state_code": "DC",
        "canonical_country": "United States",
        "metro_area": "Washington Metropolitan Area",
    },
    {
        "alias": "washington, d.c.",
        "canonical_city": "Washington",
        "canonical_state": "District of Columbia",
        "state_code": "DC",
        "canonical_country": "United States",
        "metro_area": "Washington Metropolitan Area",
    },
    {
        "alias": "washington dc",
        "canonical_city": "Washington",
        "canonical_state": "District of Columbia",
        "state_code": "DC",
        "canonical_country": "United States",
        "metro_area": "Washington Metropolitan Area",
    },
    {
        "alias": "d.c.",
        "canonical_city": "Washington",
        "canonical_state": "District of Columbia",
        "state_code": "DC",
        "canonical_country": "United States",
        "metro_area": "Washington Metropolitan Area",
    },
    {
        "alias": "greater atlanta area",
        "canonical_city": None,
        "canonical_state": "Georgia",
        "state_code": "GA",
        "canonical_country": "United States",
        "metro_area": "Atlanta Metropolitan Area",
    },
    {
        "alias": "atlanta metropolitan area",
        "canonical_city": None,
        "canonical_state": "Georgia",
        "state_code": "GA",
        "canonical_country": "United States",
        "metro_area": "Atlanta Metropolitan Area",
    },
]
