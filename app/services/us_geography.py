"""Static US geography reference data used by the location normalization
service: canonical state names/codes, NYC borough handling, and a small
set of well-known metro-area groupings.

This intentionally does not depend on any external geocoding service.
"""

US_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

US_STATE_CODE_TO_NAME: dict[str, str] = {code: name.title() for name, code in US_STATE_NAME_TO_CODE.items()}
US_STATE_CODE_TO_NAME["DC"] = "District of Columbia"

US_STATE_CODES = set(US_STATE_CODE_TO_NAME.keys())

# NYC boroughs must keep their specific locality as `city` (never collapse
# into "New York City"), but all roll up into the same metro area.
NYC_BOROUGHS = {
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "manhattan": "Manhattan",
    "the bronx": "The Bronx",
    "bronx": "The Bronx",
    "staten island": "Staten Island",
}

NEW_YORK_CITY_METRO = "New York City Metropolitan Area"

# Known city -> metro area groupings (deliberately small and curated; not
# an exhaustive geocoder). City keys are matched case-insensitively against
# the already-resolved `city` value.
CITY_METRO_AREA: dict[str, str] = {
    "new york city": NEW_YORK_CITY_METRO,
    "new york": NEW_YORK_CITY_METRO,
    "brooklyn": NEW_YORK_CITY_METRO,
    "queens": NEW_YORK_CITY_METRO,
    "manhattan": NEW_YORK_CITY_METRO,
    "the bronx": NEW_YORK_CITY_METRO,
    "staten island": NEW_YORK_CITY_METRO,
    "jersey city": NEW_YORK_CITY_METRO,
    "newark": NEW_YORK_CITY_METRO,
    "atlanta": "Atlanta Metropolitan Area",
    "washington": "Washington Metropolitan Area",
    "san francisco": "San Francisco Bay Area",
    "oakland": "San Francisco Bay Area",
    "los angeles": "Greater Los Angeles Area",
    "chicago": "Chicago Metropolitan Area",
    "miami": "Miami Metropolitan Area",
    "boston": "Boston Metropolitan Area",
    "dallas": "Dallas-Fort Worth Metroplex",
    "fort worth": "Dallas-Fort Worth Metroplex",
    "seattle": "Seattle Metropolitan Area",
    "orlando": "Orlando Metropolitan Area",
    "tampa": "Tampa Bay Area",
}

# Free-standing "metro area" phrases that describe a region without a
# specific city. These resolve to state-level + metro-level info only.
METRO_ONLY_PHRASES: dict[str, dict[str, str | None]] = {
    "atlanta metropolitan area": {
        "state": "Georgia", "state_code": "GA", "metro_area": "Atlanta Metropolitan Area",
    },
    "greater atlanta area": {
        "state": "Georgia", "state_code": "GA", "metro_area": "Atlanta Metropolitan Area",
    },
    "greater new york city area": {
        "state": "New York", "state_code": "NY", "metro_area": NEW_YORK_CITY_METRO,
    },
    "new york city metropolitan area": {
        "state": "New York", "state_code": "NY", "metro_area": NEW_YORK_CITY_METRO,
    },
    "greater los angeles area": {
        "state": "California", "state_code": "CA", "metro_area": "Greater Los Angeles Area",
    },
    "san francisco bay area": {
        "state": "California", "state_code": "CA", "metro_area": "San Francisco Bay Area",
    },
    "greater boston area": {
        "state": "Massachusetts", "state_code": "MA", "metro_area": "Boston Metropolitan Area",
    },
    "washington metropolitan area": {
        "state": "District of Columbia", "state_code": "DC", "metro_area": "Washington Metropolitan Area",
    },
    "greater chicago area": {
        "state": "Illinois", "state_code": "IL", "metro_area": "Chicago Metropolitan Area",
    },
}

REMOTE_PHRASES = {"remote", "remote (us)", "remote - us", "fully remote", "work from home", "wfh"}

COUNTRY_ONLY_PHRASES = {"united states", "usa", "u.s.", "u.s.a.", "us"}

# A small set of non-US country names, used only to decide whether a
# "City, X" value should be classified as international rather than
# ambiguous. Not exhaustive by design - anything not recognized here (and
# not a US state) is treated as ambiguous rather than guessed.
KNOWN_NON_US_COUNTRIES = {
    "canada", "mexico", "united kingdom", "uk", "england", "france", "germany",
    "spain", "italy", "india", "china", "japan", "brazil", "australia",
    "ireland", "netherlands", "switzerland", "singapore", "south korea",
}
