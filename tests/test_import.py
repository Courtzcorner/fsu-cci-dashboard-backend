import io

from app.models.alumni import Alumni, AlumniOrganization


def _login(client, username, password):
    return client.post("/login", json={"username": username, "password": password}).json()["access_token"]


def _upload(client, token, organization_slug, csv_text):
    return client.post(
        "/admin/import-alumni",
        data={"organization": organization_slug},
        files={"file": ("alumni.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )


CSV_BASIC = """First Name,Last Name,Graduation Year,Location,LinkedIn,Job Title,Company
Jordan,Lee,2022,"Brooklyn, NY",linkedin.com/in/jordanlee,Product Manager,Capital One
Maria,Gomez,2019,"Tallahassee, FL",,Reporter,WCTV
"""


def test_csv_import_creates_records(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, "fsu-cci", CSV_BASIC)
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
    _upload(client, token, "fsu-cci", CSV_BASIC)

    updated_csv = CSV_BASIC.replace("Product Manager", "Senior Product Manager")
    response = _upload(client, token, "fsu-cci", updated_csv)

    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 2

    records = db_session.query(Alumni).all()
    assert len(records) == 2
    jordan = next(r for r in records if r.first_name == "Jordan")
    assert jordan.job_title == "Senior Product Manager"


def test_csv_import_does_not_merge_on_name_alone(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        "First Name,Last Name,Graduation Year,Location\n"
        "Jordan,Lee,2022,\"Brooklyn, NY\"\n"
    )
    _upload(client, token, "fsu-cci", csv_text)

    different_grad_year_csv = (
        "First Name,Last Name,Graduation Year,Location\n"
        "Jordan,Lee,2010,\"Atlanta, GA\"\n"
    )
    response = _upload(client, token, "fsu-cci", different_grad_year_csv)
    body = response.json()
    assert body["created"] == 1

    records = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").all()
    assert len(records) == 2


def test_csv_import_reports_row_errors_for_missing_required_fields(client, organization, admin_user):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = "First Name,Last Name\n,\nJordan,Lee\n"
    response = _upload(client, token, "fsu-cci", csv_text)
    body = response.json()
    assert body["failed"] == 1
    assert body["created"] == 1
    assert len(body["row_errors"]) == 1


def test_csv_import_requires_admin_role(client, organization, alumni_user):
    token = _login(client, "jdoe", "AlumniPass123!")
    response = _upload(client, token, "fsu-cci", CSV_BASIC)
    assert response.status_code == 403


def test_csv_import_defaults_to_fsu_cci_when_organization_field_is_omitted(client, organization, admin_user):
    """organization: str = Form(default="fsu-cci") - omitting the form
    field entirely must still import against fsu-cci, not fail/400."""
    token = _login(client, "admin", "AdminPass123!")
    response = client.post(
        "/admin/import-alumni",
        # No "organization" key in the form data at all.
        files={"file": ("alumni.csv", io.BytesIO(CSV_BASIC.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["organization"] == "fsu-cci"
    assert response.json()["created"] == 2


def test_csv_import_uses_submitted_organization_when_provided(client, admin_user, db_session):
    from app.models.organization import Organization

    other_org = Organization(name="STARS National", slug="stars-national")
    db_session.add(other_org)
    db_session.commit()

    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, "stars-national", CSV_BASIC)
    assert response.status_code == 200
    assert response.json()["organization"] == "stars-national"


def test_csv_import_rejects_unknown_organization_even_with_valid_admin(client, admin_user):
    """The submitted form field alone never grants access - an admin
    cannot import into an organization that doesn't exist in the database."""
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, "does-not-exist", CSV_BASIC)
    assert response.status_code == 404


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
    response = _upload(client, token, "fsu-cci", CSV_REAL_HEADERS)
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
    _upload(client, token, "fsu-cci", CSV_REAL_HEADERS)

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
    _upload(client, token, "fsu-cci", full_csv)

    record = db_session.query(Alumni).filter(Alumni.first_name == "Morgan").one()
    assert record.job_title == "Data Analyst"
    assert record.company == "Acme Corp"
    assert record.location_original == "Atlanta, GA"

    blank_followup_csv = (
        "First Name,Last Name,Graduation Year,Current Job Title,Current Employer,Location\n"
        "Morgan,Blake,2020,,,\n"
    )
    response = _upload(client, token, "fsu-cci", blank_followup_csv)
    assert response.status_code == 200
    assert response.json()["updated"] == 1

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
    response = _upload(client, token, "fsu-cci", unmapped_csv)
    assert response.json()["created"] == 1
    db_session.expire_all()
    record = db_session.query(Alumni).filter(Alumni.first_name == "Casey").one()
    assert record.job_title is None
    assert record.company is None

    real_csv = (
        "First Name,Last Name,Graduation Year,Current Job Title,Current Employer,Location\n"
        'Casey,Nguyen,2018,Software Engineer,Globex,"Miami, FL"\n'
    )
    response = _upload(client, token, "fsu-cci", real_csv)
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

    # Reimporting the exact same file again must not duplicate or regress.
    response = _upload(client, token, "fsu-cci", real_csv)
    assert response.json()["updated"] == 1
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
    response = _upload(client, token, "fsu-cci", csv_text)
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
    response = _upload(client, token, "fsu-cci", CSV_STUDENT_HEADERS)
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
