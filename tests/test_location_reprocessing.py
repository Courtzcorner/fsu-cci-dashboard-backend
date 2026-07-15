from app.models.alumni import Alumni, AlumniOrganization
from app.services.location_reprocess_service import reprocess_locations


def test_dry_run_does_not_modify_records(db_session, organization):
    alumni = Alumni(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        location_original="Brooklyn, NY",
        city=None,
        location_normalization_status="missing",
    )
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()

    result = reprocess_locations(db_session, organization_id=organization.id, dry_run=True)

    assert result["processed"] == 1
    assert result["updated"] == 1

    db_session.refresh(alumni)
    assert alumni.city is None
    assert alumni.location_normalization_status == "missing"


def test_apply_run_updates_records_and_preserves_original(db_session, organization):
    alumni = Alumni(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        location_original="Brooklyn, NY",
        city=None,
        location_normalization_status="missing",
    )
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()

    result = reprocess_locations(db_session, organization_id=organization.id, dry_run=False)
    assert result["updated"] == 1

    db_session.refresh(alumni)
    assert alumni.location_original == "Brooklyn, NY"
    assert alumni.city == "Brooklyn"
    assert alumni.state == "New York"
    assert alumni.location_normalization_status == "normalized"


def test_unchanged_records_are_not_recounted_as_updated(db_session, organization):
    alumni = Alumni(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        location_original="Brooklyn, NY",
        city="Brooklyn",
        state="New York",
        state_code="NY",
        country="United States",
        metro_area="New York City Metropolitan Area",
        display_location="Brooklyn, New York",
        location_normalization_status="normalized",
    )
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()

    result = reprocess_locations(db_session, organization_id=organization.id, dry_run=False)
    assert result["updated"] == 0
    assert result["unchanged"] == 1
