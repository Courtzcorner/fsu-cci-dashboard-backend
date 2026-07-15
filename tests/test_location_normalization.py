from app.services.location_normalization_service import normalize_location


def test_new_york_aliases_normalize_to_new_york_city():
    for raw in ["New York, New York", "New York City, New York", "NYC, NY", "New York, NY"]:
        result = normalize_location(raw)
        assert result.city == "New York City"
        assert result.state == "New York"
        assert result.state_code == "NY"
        assert result.metro_area == "New York City Metropolitan Area"
        assert result.location_normalization_status == "normalized"
        assert result.location_original == raw


def test_brooklyn_is_preserved_as_its_own_city():
    result = normalize_location("Brooklyn, New York")
    assert result.city == "Brooklyn"
    assert result.city != "New York City"
    assert result.state == "New York"
    assert result.location_normalization_status == "normalized"


def test_brooklyn_abbreviated_state_also_preserved():
    result = normalize_location("Brooklyn, NY")
    assert result.city == "Brooklyn"
    assert result.state_code == "NY"


def test_brooklyn_and_new_york_city_share_metro_area_but_not_city():
    brooklyn = normalize_location("Brooklyn, NY")
    nyc = normalize_location("New York, NY")
    assert brooklyn.metro_area == nyc.metro_area == "New York City Metropolitan Area"
    assert brooklyn.city != nyc.city


def test_state_abbreviation_normalizes_to_canonical_name():
    result = normalize_location("Tallahassee, FL")
    assert result.city == "Tallahassee"
    assert result.state == "Florida"
    assert result.state_code == "FL"
    assert result.country == "United States"


def test_state_full_name_normalizes_with_code():
    result = normalize_location("Tallahassee, Florida")
    assert result.state == "Florida"
    assert result.state_code == "FL"


def test_washington_dc_variants():
    for raw in ["Washington, D.C.", "Washington DC", "Washington, DC"]:
        result = normalize_location(raw)
        assert result.city == "Washington"
        assert result.state == "District of Columbia"
        assert result.state_code == "DC"


def test_remote_does_not_invent_city_or_state():
    result = normalize_location("Remote")
    assert result.city is None
    assert result.state is None
    assert result.state_code is None
    assert result.country is None
    assert result.metro_area is None
    assert result.display_location == "Remote"
    assert result.location_normalization_status == "remote"


def test_ambiguous_location_preserves_original_without_guessing():
    result = normalize_location("Springfield")
    assert result.location_original == "Springfield"
    assert result.location_normalization_status == "ambiguous"
    assert result.city is None
    assert result.state is None


def test_missing_location_returns_missing_status():
    result = normalize_location(None)
    assert result.location_normalization_status == "missing"
    result = normalize_location("   ")
    assert result.location_normalization_status == "missing"


def test_metro_area_only_phrase_is_partially_normalized():
    for raw in ["Atlanta Metropolitan Area", "Greater Atlanta Area"]:
        result = normalize_location(raw)
        assert result.metro_area == "Atlanta Metropolitan Area"
        assert result.city is None
        assert result.state == "Georgia"
        assert result.location_normalization_status == "partially_normalized"


def test_country_only_value_is_partially_normalized():
    result = normalize_location("United States")
    assert result.country == "United States"
    assert result.city is None
    assert result.location_normalization_status == "partially_normalized"


def test_international_city_and_country_detected():
    result = normalize_location("Toronto, Canada")
    assert result.city == "Toronto"
    assert result.country == "Canada"
    assert result.location_normalization_status == "international"
