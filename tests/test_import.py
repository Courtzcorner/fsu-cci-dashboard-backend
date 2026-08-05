import csv
import io

from app.models.alumni import Alumni, AlumniOrganization


def _login(client, username, password):
    return client.post("/login", json={"username": username, "password": password}).json()["access_token"]


def _upload(client, token, csv_text):
    """POST /admin/import-alumni no longer accepts (or requires) an
    organization field at all - there is only one dashboard/dataset, and
    the admin cannot select or specify an organization."""
    return client.post(
        "/admin/import-alumni",
        files={"file": ("alumni.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )


CSV_BASIC = """First Name,Last Name,Graduation Year,Location,LinkedIn,Job Title,Company
Jordan,Lee,2022,"Brooklyn, NY",linkedin.com/in/jordanlee,Product Manager,Capital One
Maria,Gomez,2019,"Tallahassee, FL",,Reporter,WCTV
"""


def test_csv_import_creates_records(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, CSV_BASIC)
    assert response.status_code == 200

    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["failed"] == 0

    records = db_session.query(Alumni).all()
    assert len(records) == 2
    brooklyn = next(r for r in records if r.first_name == "Jordan")
    assert brooklyn.location_original == "Brooklyn, NY"
    assert brooklyn.city == "Brooklyn"
    assert brooklyn.state == "New York"
    assert brooklyn.linkedin_url == "https://linkedin.com/in/jordanlee"


def test_csv_import_prevents_duplicates_via_linkedin_url(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, CSV_BASIC)

    updated_csv = CSV_BASIC.replace("Product Manager", "Senior Product Manager")
    response = _upload(client, token, updated_csv)

    body = response.json()
    assert body["created"] == 0
    # Only Jordan's job title actually changed; Maria's row is identical
    # to the first upload, so she counts as "unchanged", not "updated".
    assert body["updated"] == 1
    assert body["unchanged"] == 1

    records = db_session.query(Alumni).all()
    assert len(records) == 2
    jordan = next(r for r in records if r.first_name == "Jordan")
    assert jordan.job_title == "Senior Product Manager"


def test_csv_import_matches_on_name_alone_when_no_stronger_identifier_exists(
    client, organization, admin_user, db_session
):
    """Under single-dataset "replace mode", the dedupe/match priority is
    LinkedIn URL > email > first+last name - there is no longer a
    graduation-year tiebreaker. The same name (no LinkedIn/email in either
    upload) is treated as the same person and updated in place, and since
    each upload is the complete authoritative dataset, only 1 row exists
    (not 2) after the second upload."""
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        "First Name,Last Name,Graduation Year,Location\n"
        "Jordan,Lee,2022,\"Brooklyn, NY\"\n"
    )
    _upload(client, token, csv_text)

    second_csv = (
        "First Name,Last Name,Graduation Year,Location\n"
        "Jordan,Lee,2010,\"Atlanta, GA\"\n"
    )
    response = _upload(client, token, second_csv)
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["active_database_total"] == 1

    records = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").all()
    assert len(records) == 1
    assert records[0].graduation_year == 2010
    assert records[0].city == "Atlanta"


def test_csv_import_reports_row_errors_for_missing_required_fields(client, organization, admin_user):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = "First Name,Last Name\n,\nJordan,Lee\n"
    response = _upload(client, token, csv_text)
    body = response.json()
    assert body["failed"] == 1
    assert body["created"] == 1
    assert len(body["row_errors"]) == 1


def test_csv_import_requires_admin_role(client, organization, alumni_user):
    token = _login(client, "jdoe", "AlumniPass123!")
    response = _upload(client, token, CSV_BASIC)
    assert response.status_code == 403


def test_csv_import_works_without_an_organization_parameter(client, organization, admin_user):
    """There is only one dashboard/dataset: the endpoint no longer accepts
    an organization field at all, and always imports into the backend's
    single configured default organization (fsu-cci)."""
    token = _login(client, "admin", "AdminPass123!")
    response = client.post(
        "/admin/import-alumni",
        # No "organization" key in the form data at all - and there is no
        # way to send one that has any effect.
        files={"file": ("alumni.csv", io.BytesIO(CSV_BASIC.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["organization"] == "fsu-cci"
    assert response.json()["created"] == 2


def test_csv_import_ignores_client_submitted_organization_field(client, organization, admin_user, db_session):
    """The organization value must not be exposed as an admin choice: even
    if a legacy/malicious client sends an "organization" form field, it is
    silently ignored and the import always targets the single default
    organization's dataset."""
    from app.models.organization import Organization

    other_org = Organization(name="STARS National", slug="stars-national")
    db_session.add(other_org)
    db_session.commit()

    token = _login(client, "admin", "AdminPass123!")
    response = client.post(
        "/admin/import-alumni",
        data={"organization": "stars-national"},
        files={"file": ("alumni.csv", io.BytesIO(CSV_BASIC.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["organization"] == "fsu-cci"

    records = db_session.query(Alumni).all()
    assert len(records) == 2


# --- Real-world header mapping (School Name, Current Job Title, etc.) ---

# Mirrors the headers actually seen in the real fsu-cci alumni spreadsheet.
# Includes duplicate-concept columns (Current/Existing/LinkedIn variants)
# to exercise the priority-ordered alias resolution.
CSV_REAL_HEADERS = (
    "First Name,Last Name,Graduation Year,School Name,Degree,Major,"
    "Current Job Title,Current Employer,Location,Industry,LinkedIn URL,"
    "Existing Job Title,Existing Company,Existing Location,"
    "LinkedIn Job Title,LinkedIn Company,LinkedIn Location\n"
    'Taylor,Reed,2021,,B.A.,Communications,'
    "Fallback Title,Fallback Co,\"Fallback City, GA\",Media,"
    "linkedin.com/in/taylorreed,"
    "Existing Title,Existing Co,\"Existing City, TX\","
    "Senior Editor,Warner Media,\"Atlanta, GA\"\n"
)


def test_csv_import_maps_real_world_headers_and_prefers_linkedin_columns(
    client, organization, admin_user, db_session
):
    """School Name/Current Job Title/etc. must map to the correct Alumni
    columns, and when multiple synonymous columns are present, the
    LinkedIn-sourced value must win per the documented priority order."""
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, CSV_REAL_HEADERS)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["created"] == 1
    assert body["failed"] == 0
    # "School Name" was blank in this row, so the fsu-cci default applies.
    assert body["rows_with_university"] == 1
    assert body["rows_with_job_title"] == 1
    assert body["rows_with_company"] == 1
    assert body["rows_with_location"] == 1
    assert body["unrecognized_headers"] == []

    record = db_session.query(Alumni).filter(Alumni.first_name == "Taylor").one()
    assert record.university == "Florida State University"
    assert record.degree == "B.A."
    assert record.major == "Communications"
    # LinkedIn columns take priority over Current/Existing/plain columns.
    assert record.job_title == "Senior Editor"
    assert record.company == "Warner Media"
    assert record.location_original == "Atlanta, GA"
    assert record.city == "Atlanta"
    assert record.state == "Georgia"
    # "Industry" was explicitly provided in the CSV, so the imported value
    # wins over any keyword-based inference.
    assert record.industry == "Media"
    assert record.linkedin_url == "https://linkedin.com/in/taylorreed"


def test_csv_import_get_alumni_data_returns_nonnull_fields(client, organization, admin_user, db_session):
    """Regression guard for the reported bug: GET /alumni-data must not
    come back with nulls for fields the CSV clearly provided."""
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, CSV_REAL_HEADERS)

    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    records = response.json()["data"]
    assert len(records) == 1
    record = records[0]
    for key in (
        "graduation_year", "major", "degree", "university", "job_title", "company",
        "industry", "location_original", "city", "state", "state_code", "country",
        "display_location",
    ):
        assert record[key] is not None, f"{key} unexpectedly null: {record}"


def test_csv_import_does_not_erase_existing_values_with_blank_reimport(
    client, organization, admin_user, db_session
):
    """Regression: reimporting a row whose CSV values are now blank must
    never wipe out previously-populated nonnull database values."""
    token = _login(client, "admin", "AdminPass123!")
    full_csv = (
        "First Name,Last Name,Graduation Year,Current Job Title,Current Employer,Location\n"
        'Morgan,Blake,2020,Data Analyst,Acme Corp,"Atlanta, GA"\n'
    )
    _upload(client, token, full_csv)

    record = db_session.query(Alumni).filter(Alumni.first_name == "Morgan").one()
    assert record.job_title == "Data Analyst"
    assert record.company == "Acme Corp"
    assert record.location_original == "Atlanta, GA"

    blank_followup_csv = (
        "First Name,Last Name,Graduation Year,Current Job Title,Current Employer,Location\n"
        "Morgan,Blake,2020,,,\n"
    )
    response = _upload(client, token, blank_followup_csv)
    assert response.status_code == 200
    # Blank CSV values never overwrite the existing nonblank values, so
    # nothing actually changes on this row - it counts as "unchanged".
    assert response.json()["unchanged"] == 1

    db_session.expire_all()
    record = db_session.query(Alumni).filter(Alumni.first_name == "Morgan").one()
    assert record.job_title == "Data Analyst"
    assert record.company == "Acme Corp"
    assert record.location_original == "Atlanta, GA"
    assert record.city == "Atlanta"


def test_reimporting_same_file_fills_previously_null_fields_without_duplicating(
    client, organization, admin_user, db_session
):
    """Simulates the "header-mapping bug fixed, now backfill" flow: the
    same physical row, first imported with headers the old importer could
    not map (so most fields end up null), then reimported unchanged once
    mapping works - must fill the nulls and must not create a duplicate."""
    token = _login(client, "admin", "AdminPass123!")

    unmapped_csv = (
        "First Name,Last Name,Graduation Year,Some Unmapped Column\n"
        "Casey,Nguyen,2018,ignore-me\n"
    )
    response = _upload(client, token, unmapped_csv)
    assert response.json()["created"] == 1
    db_session.expire_all()
    record = db_session.query(Alumni).filter(Alumni.first_name == "Casey").one()
    assert record.job_title is None
    assert record.company is None

    real_csv = (
        "First Name,Last Name,Graduation Year,Current Job Title,Current Employer,Location\n"
        'Casey,Nguyen,2018,Software Engineer,Globex,"Miami, FL"\n'
    )
    response = _upload(client, token, real_csv)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1

    db_session.expire_all()
    records = db_session.query(Alumni).filter(Alumni.first_name == "Casey").all()
    assert len(records) == 1
    record = records[0]
    assert record.job_title == "Software Engineer"
    assert record.company == "Globex"
    assert record.city == "Miami"

    # Reimporting the exact same file again must not duplicate or regress -
    # since nothing actually changed, it counts as "unchanged", not
    # "updated".
    response = _upload(client, token, real_csv)
    assert response.json()["unchanged"] == 1
    db_session.expire_all()
    records = db_session.query(Alumni).filter(Alumni.first_name == "Casey").all()
    assert len(records) == 1


def test_reimport_fills_null_company_and_location_via_linkedin_columns(
    client, organization, admin_user, db_session
):
    """Starts from an existing alumni record with null company/location
    (as if it were imported before header mapping was fixed), then
    reimports a row carrying LinkedIn Company/LinkedIn Location values and
    verifies both fields get populated - proving update logic actually
    writes nonblank resolved values, not just row creation."""
    token = _login(client, "admin", "AdminPass123!")

    existing = Alumni(
        first_name="Riley", last_name="Chen", full_name="Riley Chen", graduation_year=2019,
        company=None, location_original=None,
    )
    db_session.add(existing)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=existing.id, organization_id=organization.id))
    db_session.commit()
    assert existing.company is None
    assert existing.location_original is None

    csv_text = (
        "First Name,Last Name,Graduation Year,LinkedIn Company,LinkedIn Location\n"
        'Riley,Chen,2019,Delta Analytics,"Denver, CO"\n'
    )
    response = _upload(client, token, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["selected_company_column"] == "LinkedIn Company"
    assert body["selected_location_column"] == "LinkedIn Location"

    db_session.expire_all()
    record = db_session.query(Alumni).filter(Alumni.first_name == "Riley").one()
    assert record.company == "Delta Analytics"
    assert record.location_original == "Denver, CO"
    assert record.city == "Denver"
    assert record.state == "Colorado"


CSV_STUDENT_HEADERS = (
    "Student Firstname,Student Lastname,School Name,Degree,Major,Graduation Year,"
    "Existing Job Title,Existing Company,Existing Location,"
    "LinkedIn Job Title,LinkedIn Company,LinkedIn Location,Industry,LinkedIn URL\n"
    'Jamie,Ortiz,,M.S.,Data Science,2022,'
    "Analyst I,OldCo,\"Old City, NV\","
    "Senior Data Scientist,Insight Labs,\"Denver, CO\",Technology,"
    "linkedin.com/in/jamieortiz\n"
)


def test_csv_import_maps_student_prefixed_and_existing_linkedin_headers(
    client, organization, admin_user, db_session
):
    """Real dataset variant using 'Student Firstname/Lastname' plus the
    Existing/LinkedIn compound columns."""
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, CSV_STUDENT_HEADERS)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["created"] == 1
    assert body["failed"] == 0
    assert body["unrecognized_headers"] == []
    assert body["selected_company_column"] == "LinkedIn Company"
    assert body["selected_location_column"] == "LinkedIn Location"
    assert body["selected_university_column"] is None  # School Name was blank -> fsu-cci default applied
    assert body["selected_degree_column"] == "Degree"
    assert body["selected_major_column"] == "Major"
    assert body["selected_graduation_year_column"] == "Graduation Year"

    record = db_session.query(Alumni).filter(Alumni.first_name == "Jamie").one()
    assert record.last_name == "Ortiz"
    assert record.university == "Florida State University"
    assert record.degree == "M.S."
    assert record.major == "Data Science"
    assert record.graduation_year == 2022
    assert record.job_title == "Senior Data Scientist"
    assert record.company == "Insight Labs"
    assert record.location_original == "Denver, CO"
    assert record.city == "Denver"


# --- Separate City/State columns + ambiguous "Education" column ---

CSV_CITY_STATE_AND_EDUCATION = (
    "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,"
    "Verification Status,Education\n"
    "Bonney,Emma,emmabonney8@gmail.com,linkedin.com/in/emmabonney,"
    "Blue Cross and Blue Shield of Alabama,Strategy Consultant II,Indianapolis,IN,"
    "Updated,Florida State University\n"
)


def test_csv_import_uses_separate_city_state_columns_and_classifies_education_as_university(
    client, organization, admin_user, db_session
):
    """Regression for the exact reported header set: no combined "Location"
    column exists (only separate City/State), and "Education" holds an
    institution name (not a degree) - both must be handled correctly."""
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, CSV_CITY_STATE_AND_EDUCATION)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["created"] == 1
    assert body["failed"] == 0
    assert body["unrecognized_headers"] == []
    # No combined "location" column exists in this CSV.
    assert body["selected_location_column"] is None
    assert body["selected_city_column"] == "City"
    assert body["selected_state_column"] == "State"
    assert body["rows_with_city"] > 0
    assert body["rows_with_state"] > 0
    assert body["rows_with_location"] > 0
    assert body["rows_with_raw_city"] > 0
    assert body["rows_with_raw_state"] > 0
    assert body["rows_with_constructed_location"] > 0
    # "Education" was dynamically classified as a university value here.
    assert body["selected_university_column"] == "Education"
    assert body["selected_degree_column"] is None

    record = db_session.query(Alumni).filter(Alumni.first_name == "Emma").one()
    assert record.company == "Blue Cross and Blue Shield of Alabama"
    assert record.job_title == "Strategy Consultant II"
    assert record.university == "Florida State University"
    assert record.degree is None
    assert record.city == "Indianapolis"
    assert record.state == "Indiana"
    assert record.state_code == "IN"
    assert record.location_original == "Indianapolis, IN"
    assert record.display_location == "Indianapolis, IN"
    assert record.location_normalization_status != "missing"


def test_csv_import_persists_notes_in_the_database(client, organization, admin_user, db_session):
    """The CSV's free-text "Notes" column must be carried through to
    alumni.notes in the database."""
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        "First Name,Last Name,Notes\n"
        "Jamie,Fox,\"Great mentor, always responsive\"\n"
    )
    response = _upload(client, token, csv_text)
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1

    record = db_session.query(Alumni).filter(Alumni.first_name == "Jamie").one()
    assert record.notes == "Great mentor, always responsive"


def test_csv_import_notes_appear_in_admin_export(client, organization, admin_user, db_session):
    """Imported notes must be present in the admin CSV export - this is
    the only endpoint that surfaces alumni.notes."""
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        "First Name,Last Name,Notes\n"
        "Jamie,Fox,\"Great mentor, always responsive\"\n"
    )
    _upload(client, token, csv_text)

    export = client.get("/admin/export-alumni", headers={"Authorization": f"Bearer {token}"})
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 1
    assert rows[0]["Notes"] == "Great mentor, always responsive"


def test_csv_import_notes_are_not_exposed_by_alumni_data_api(client, organization, admin_user, db_session):
    """alumni.notes must never be returned by GET /alumni-data - it is
    only ever surfaced through the admin CSV export."""
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        "First Name,Last Name,Notes\n"
        "Jamie,Fox,\"Great mentor, always responsive\"\n"
    )
    _upload(client, token, csv_text)

    directory = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert directory.status_code == 200
    data = directory.json()["data"]
    assert len(data) == 1
    assert "notes" not in data[0]


def test_csv_missing_optional_columns_still_imports_successfully(client, organization, admin_user, db_session):
    """A CSV lacking every optional column (Notes, Industry, City, State,
    Education) - only First Name/Last Name present - must still import
    cleanly with those genuinely-omitted fields simply left null, never
    failing the row."""
    token = _login(client, "admin", "AdminPass123!")
    csv_text = "First Name,Last Name\nAlex,Rivera\nSam,Patel\n"
    response = _upload(client, token, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 2
    assert body["failed"] == 0
    assert body["active_database_total"] == 2

    records = db_session.query(Alumni).filter(Alumni.first_name.in_(["Alex", "Sam"])).all()
    assert len(records) == 2
    for record in records:
        assert record.notes is None
        assert record.industry is None
        assert record.city is None
        assert record.state is None
        assert record.degree is None

    directory = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert directory.status_code == 200
    assert directory.json()["meta"]["total"] == 2


def test_reimport_fills_previously_null_city_state_and_location(client, organization, admin_user, db_session):
    """An existing record imported before city/state support existed (so
    city/state/location_original are null) must get backfilled by a
    reimport of the same row using the City/State columns - without
    creating a duplicate."""
    token = _login(client, "admin", "AdminPass123!")

    existing = Alumni(
        first_name="Devon", last_name="Park", full_name="Devon Park", graduation_year=2021,
        city=None, state=None, state_code=None, location_original=None,
    )
    db_session.add(existing)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=existing.id, organization_id=organization.id))
    db_session.commit()

    csv_text = (
        "First Name,Last Name,Graduation Year,City,State\n"
        "Devon,Park,2021,Austin,TX\n"
    )
    response = _upload(client, token, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1

    db_session.expire_all()
    record = db_session.query(Alumni).filter(Alumni.first_name == "Devon").one()
    assert record.city == "Austin"
    assert record.state == "Texas"
    assert record.state_code == "TX"
    assert record.location_original == "Austin, TX"

    # A follow-up import with blank city/state must not erase these values,
    # and since nothing actually changed, it counts as "unchanged".
    blank_csv = "First Name,Last Name,Graduation Year,City,State\nDevon,Park,2021,,\n"
    response = _upload(client, token, blank_csv)
    assert response.json()["unchanged"] == 1

    db_session.expire_all()
    records = db_session.query(Alumni).filter(Alumni.first_name == "Devon").all()
    assert len(records) == 1
    record = records[0]
    assert record.city == "Austin"
    assert record.state == "Texas"
    assert record.state_code == "TX"
    assert record.location_original == "Austin, TX"
