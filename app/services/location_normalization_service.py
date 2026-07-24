"""
Location normalization service.

This is the single, backend-owned place where raw spreadsheet/location
text is turned into structured, canonical fields. It must be used during
CSV import, direct record creation, and the explicit reprocessing
command/endpoint - never re-implemented in the frontend.

Design principles (see task spec):
- `location_original` is always preserved verbatim; normalization never
  discards or overwrites the raw imported text.
- The `location_aliases` DB table (if a session is supplied) is checked
  before any generic parsing rule. A small in-process fallback alias map
  is used when no session is available (e.g. isolated unit tests).
- Geographic specificity is preserved: NYC boroughs (Brooklyn, Queens,
  Manhattan, The Bronx, Staten Island) are never collapsed into the city
  value "New York City" - they keep their own `city`, but roll up into the
  shared `metro_area`.
- When a value is materially ambiguous, we do not guess: we preserve the
  original text, populate only fields we are confident about, and set an
  appropriate non-"normalized" status.
- No paid geocoding dependency is required for the app to function.
  Optional geocoding (lat/lng enrichment) is stubbed behind a feature flag
  and a cache; see `_maybe_geocode`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.roles import LocationNormalizationStatus
from app.services.location_aliases_seed_data import LOCATION_ALIAS_SEED_DATA
from app.services.us_geography import (
    CITY_METRO_AREA,
    COUNTRY_ONLY_PHRASES,
    KNOWN_NON_US_COUNTRIES,
    METRO_ONLY_PHRASES,
    NYC_BOROUGHS,
    REMOTE_PHRASES,
    US_STATE_CODE_TO_NAME,
    US_STATE_NAME_TO_CODE,
)

# In-memory cache for optional geocoding results, keyed by the normalized
# display_location. Populated only if GEOCODING_ENABLED=true.
_geocode_cache: dict[str, tuple[Optional[float], Optional[float]]] = {}

_FALLBACK_ALIAS_MAP: dict[str, dict] = {entry["alias"]: entry for entry in LOCATION_ALIAS_SEED_DATA}


@dataclass
class LocationNormalizationResult:
    location_original: Optional[str]
    city: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    country: Optional[str] = None
    metro_area: Optional[str] = None
    display_location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_normalization_status: str = LocationNormalizationStatus.MISSING.value

    def as_dict(self) -> dict:
        return asdict(self)


def _load_alias_map(db: Optional[Session]) -> dict[str, dict]:
    """Merge the DB-backed alias table (if available) over the built-in
    fallback map, so new aliases can be added via seed data / admin tooling
    without a code change.
    """
    if db is None:
        return _FALLBACK_ALIAS_MAP

    from app.models.location_alias import LocationAlias  # local import avoids cycles

    merged = dict(_FALLBACK_ALIAS_MAP)
    for row in db.query(LocationAlias).all():
        merged[row.alias.strip().lower()] = {
            "alias": row.alias,
            "canonical_city": row.canonical_city,
            "canonical_state": row.canonical_state,
            "state_code": row.state_code,
            "canonical_country": row.canonical_country,
            "metro_area": row.metro_area,
            "latitude": row.latitude,
            "longitude": row.longitude,
        }
    return merged


def _title_case_place(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:] if word else word for word in value.strip().split(" "))


def _resolve_state(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a state fragment to (canonical_name, usps_code), handling
    both full names and abbreviations, plus DC variants.
    """
    cleaned = raw.strip().strip(".").lower()
    if cleaned in {"dc", "d.c", "washington d.c", "district of columbia"}:
        return "District of Columbia", "DC"
    if cleaned in US_STATE_NAME_TO_CODE:
        code = US_STATE_NAME_TO_CODE[cleaned]
        return US_STATE_CODE_TO_NAME[code], code
    upper = raw.strip().upper()
    if upper in US_STATE_CODE_TO_NAME:
        return US_STATE_CODE_TO_NAME[upper], upper
    return None, None


def _alias_result(raw_value: str, entry: dict) -> LocationNormalizationResult:
    city = entry.get("canonical_city")
    state = entry.get("canonical_state")
    state_code = entry.get("state_code")
    country = entry.get("canonical_country")
    metro_area = entry.get("metro_area")

    if city and state:
        display_location = f"{city}, {state}"
        status = LocationNormalizationStatus.NORMALIZED.value
    elif state or metro_area:
        display_location = metro_area or state
        status = LocationNormalizationStatus.PARTIALLY_NORMALIZED.value
    else:
        display_location = city or country
        status = LocationNormalizationStatus.PARTIALLY_NORMALIZED.value

    return LocationNormalizationResult(
        location_original=raw_value,
        city=city,
        state=state,
        state_code=state_code,
        country=country,
        metro_area=metro_area,
        display_location=display_location,
        latitude=entry.get("latitude"),
        longitude=entry.get("longitude"),
        location_normalization_status=status,
    )


def _parse_city_state(city_part: str, state_part: str, raw_value: str) -> LocationNormalizationResult:
    state_name, state_code = _resolve_state(state_part)

    if state_name is None:
        # State fragment didn't resolve to a US state - check whether it
        # looks like a foreign country instead of guessing.
        country_candidate = city_part_lower = state_part.strip().lower()
        if country_candidate in KNOWN_NON_US_COUNTRIES:
            return LocationNormalizationResult(
                location_original=raw_value,
                city=_title_case_place(city_part),
                country=_title_case_place(state_part),
                display_location=f"{_title_case_place(city_part)}, {_title_case_place(state_part)}",
                location_normalization_status=LocationNormalizationStatus.INTERNATIONAL.value,
            )
        return LocationNormalizationResult(
            location_original=raw_value,
            location_normalization_status=LocationNormalizationStatus.AMBIGUOUS.value,
        )

    city_lower = city_part.strip().lower()
    if city_lower in NYC_BOROUGHS:
        city = NYC_BOROUGHS[city_lower]
    elif city_lower in {"new york", "new york city", "nyc"}:
        city = "New York City"
    else:
        city = _title_case_place(city_part)

    metro_area = CITY_METRO_AREA.get(city.lower())

    return LocationNormalizationResult(
        location_original=raw_value,
        city=city,
        state=state_name,
        state_code=state_code,
        country="United States",
        metro_area=metro_area,
        display_location=f"{city}, {state_name}",
        location_normalization_status=LocationNormalizationStatus.NORMALIZED.value,
    )


def _parse_single_token(value: str, raw_value: str) -> LocationNormalizationResult:
    lowered = value.strip().lower()

    if lowered in NYC_BOROUGHS:
        city = NYC_BOROUGHS[lowered]
        return LocationNormalizationResult(
            location_original=raw_value,
            city=city,
            state="New York",
            state_code="NY",
            country="United States",
            metro_area=CITY_METRO_AREA.get(city.lower()),
            display_location=f"{city}, New York",
            location_normalization_status=LocationNormalizationStatus.NORMALIZED.value,
        )

    if lowered in US_STATE_NAME_TO_CODE:
        code = US_STATE_NAME_TO_CODE[lowered]
        state_name = US_STATE_CODE_TO_NAME[code]
        return LocationNormalizationResult(
            location_original=raw_value,
            state=state_name,
            state_code=code,
            country="United States",
            display_location=state_name,
            location_normalization_status=LocationNormalizationStatus.PARTIALLY_NORMALIZED.value,
        )

    # A small set of unambiguous single-word US cities (curated, not a
    # general geocoder). Anything else is treated as ambiguous rather than
    # guessed.
    single_city_state: dict[str, tuple[str, str, str]] = {
        "tallahassee": ("Tallahassee", "Florida", "FL"),
        "atlanta": ("Atlanta", "Georgia", "GA"),
        "brooklyn": ("Brooklyn", "New York", "NY"),
    }
    if lowered in single_city_state:
        city, state_name, code = single_city_state[lowered]
        return LocationNormalizationResult(
            location_original=raw_value,
            city=city,
            state=state_name,
            state_code=code,
            country="United States",
            metro_area=CITY_METRO_AREA.get(city.lower()),
            display_location=f"{city}, {state_name}",
            location_normalization_status=LocationNormalizationStatus.NORMALIZED.value,
        )

    return LocationNormalizationResult(
        location_original=raw_value,
        location_normalization_status=LocationNormalizationStatus.AMBIGUOUS.value,
    )


def _maybe_geocode(result: LocationNormalizationResult) -> LocationNormalizationResult:
    """Optionally enrich a normalized result with lat/lng.

    Disabled by default (GEOCODING_ENABLED=false). When enabled, results
    are cached in-process by display_location so repeated imports don't
    re-hit the provider. No external call is implemented here by default -
    integrate a real provider client in this function if/when needed.
    """
    settings = get_settings()
    if not settings.geocoding_enabled or not result.display_location:
        return result

    cache_key = result.display_location
    if cache_key in _geocode_cache:
        result.latitude, result.longitude = _geocode_cache[cache_key]
        return result

    # No default provider wired up - the app must function fully without
    # one. Plug a real client call in here behind the same cache.
    _geocode_cache[cache_key] = (None, None)
    return result


def normalize_city_state(
    city: Optional[str], state: Optional[str], state_code_hint: Optional[str] = None
) -> LocationNormalizationResult:
    """Build a location result directly from separate, already-structured
    city/state (or state_code) spreadsheet columns.

    Unlike `normalize_location`, this never has to guess how to split a
    free-text string apart - the columns are already structured - so a CSV
    import should always prefer this over parsing a combined "location"
    column when both are present (reliable structured data beats weaker
    parsed data). Never invents a value: an unresolved state is preserved
    verbatim rather than dropped, and country/state_code are only set when
    genuinely resolved.
    """
    city_clean = city.strip() if city and city.strip() else None
    state_clean = state.strip() if state and state.strip() else None
    state_code_hint_clean = state_code_hint.strip() if state_code_hint and state_code_hint.strip() else None

    if not city_clean and not state_clean:
        return LocationNormalizationResult(
            location_original=None, location_normalization_status=LocationNormalizationStatus.MISSING.value
        )

    display_city = _title_case_place(city_clean) if city_clean else None

    state_name, state_code = (None, None)
    if state_clean:
        state_name, state_code = _resolve_state(state_clean)
    if not state_code and state_code_hint_clean:
        _, hinted_code = _resolve_state(state_code_hint_clean)
        state_code = state_code or hinted_code

    # Preserve the raw imported state text even when it doesn't resolve to
    # a recognized US state - never silently drop a real value.
    final_state = state_name or state_clean

    # location_original/display_location are built from the RAW imported
    # text (e.g. "Indianapolis, IN"), not the normalized full state name,
    # since that's exactly what was in the source spreadsheet.
    parts = [part for part in (display_city, state_clean) if part]
    location_original = ", ".join(parts) if parts else None

    metro_area = CITY_METRO_AREA.get(display_city.lower()) if display_city else None

    if display_city and state_name:
        status = LocationNormalizationStatus.NORMALIZED.value
    elif display_city or final_state:
        status = LocationNormalizationStatus.PARTIALLY_NORMALIZED.value
    else:
        status = LocationNormalizationStatus.AMBIGUOUS.value

    result = LocationNormalizationResult(
        location_original=location_original,
        city=display_city,
        state=final_state,
        state_code=state_code,
        country="United States" if state_name else None,
        metro_area=metro_area,
        display_location=location_original,
        location_normalization_status=status,
    )
    return _maybe_geocode(result)


def normalize_location(raw_value: Optional[str], db: Optional[Session] = None) -> LocationNormalizationResult:
    """Normalize a single raw location string into structured fields.

    Safe to call with `db=None` (uses the built-in fallback alias map only).
    """
    if raw_value is None or not raw_value.strip():
        return LocationNormalizationResult(
            location_original=raw_value,
            location_normalization_status=LocationNormalizationStatus.MISSING.value,
        )

    value = raw_value.strip()
    lowered = value.lower().strip(".")

    if lowered in REMOTE_PHRASES or lowered.rstrip(".") in REMOTE_PHRASES:
        return LocationNormalizationResult(
            location_original=raw_value,
            display_location="Remote",
            location_normalization_status=LocationNormalizationStatus.REMOTE.value,
        )

    alias_map = _load_alias_map(db)
    alias_key = value.lower().strip()
    if alias_key in alias_map:
        return _maybe_geocode(_alias_result(raw_value, alias_map[alias_key]))

    if lowered in COUNTRY_ONLY_PHRASES:
        return LocationNormalizationResult(
            location_original=raw_value,
            country="United States",
            display_location="United States",
            location_normalization_status=LocationNormalizationStatus.PARTIALLY_NORMALIZED.value,
        )

    if lowered in METRO_ONLY_PHRASES:
        meta = METRO_ONLY_PHRASES[lowered]
        return LocationNormalizationResult(
            location_original=raw_value,
            state=meta.get("state"),
            state_code=meta.get("state_code"),
            country="United States",
            metro_area=meta.get("metro_area"),
            display_location=meta.get("metro_area"),
            location_normalization_status=LocationNormalizationStatus.PARTIALLY_NORMALIZED.value,
        )

    parts = [p.strip() for p in value.split(",") if p.strip()]

    if len(parts) >= 2:
        result = _parse_city_state(parts[0], parts[1], raw_value)
        return _maybe_geocode(result)

    result = _parse_single_token(value, raw_value)
    return _maybe_geocode(result)


def normalize_location_dict(raw_value: Optional[str], db: Optional[Session] = None) -> dict:
    return normalize_location(raw_value, db).as_dict()
