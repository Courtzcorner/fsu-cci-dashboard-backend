"""Single-dashboard / single-active-dataset ("replace mode") behavior.

The most recently uploaded CSV must become the complete, exclusive,
authoritative alumni dataset: GET /alumni-data, GET /analytics/summary,
and the active-alumni count must all agree, and none of these endpoints
require (or accept) an organization parameter from the frontend.
"""
import io

from app.models.alumni import Alumni, AlumniOrganization

from tests.test_import import _login, _upload


def _csv_of(rows: list[tuple[str, str]], linkedin: bool = True) -> str:
    header = "First Name,Last Name,LinkedIn URL" if linkedin else "First Name,Last Name"
    lines = [header]
    for i, (first, last) in enumerate(rows):
        if linkedin:
            lines.append(f"{first},{last},linkedin.com/in/{first.lower()}{last.lower()}{i}")
        else:
            lines.append(f"{first},{last}")
    return "\n".join(lines) + "\n"


def test_importing_n_unique_rows_results_in_n_active_alumni(client, organization, admin_user, db_session):
    rows = [(f"First{i}", f"Last{i}") for i in range(6)]
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, _csv_of(rows))
    body = response.json()
    assert body["created"] == 6
    assert body["active_database_total"] == 6

    active_count = (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    )
    assert active_count == 6


def test_reimporting_same_file_keeps_n_active_alumni(client, organization, admin_user, db_session):
    rows = [(f"First{i}", f"Last{i}") for i in range(6)]
    csv_text = _csv_of(rows)
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, csv_text)

    response = _upload(client, token, csv_text)
    body = response.json()
    assert body["created"] == 0
    assert body["unchanged"] == 6
    assert body["active_database_total"] == 6
    assert body["archived"] == 0

    assert db_session.query(Alumni).count() == 6


def test_importing_new_smaller_file_replaces_the_active_dataset(client, organization, admin_user, db_session):
    """Uploading a new M-row file makes M the active count and excludes
    every alumni that was only present in the older file."""
    token = _login(client, "admin", "AdminPass123!")
    first_rows = [(f"First{i}", f"Last{i}") for i in range(6)]
    _upload(client, token, _csv_of(first_rows))

    second_rows = [(f"Second{i}", f"Person{i}") for i in range(3)]
    response = _upload(client, token, _csv_of(second_rows))
    body = response.json()
    assert body["created"] == 3
    assert body["active_database_total"] == 3
    assert body["archived"] == 6

    # Old alumni are archived, not physically deleted.
    assert db_session.query(Alumni).count() == 9
    active_count = (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    )
    assert active_count == 3


def test_alumni_missing_from_latest_file_are_excluded_from_get_alumni_data(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _csv_of([("Old", "Alumnus")]))
    _upload(client, token, _csv_of([("New", "Alumnus")]))

    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    names = {row["full_name"] for row in response.json()["data"]}
    assert names == {"New Alumnus"}
    assert response.json()["meta"]["total"] == 1


def test_analytics_exclude_alumni_from_older_imports(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _csv_of([("Old", "Alumnus")]))
    _upload(client, token, _csv_of([("New", "One")]))

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["total_alumni"] == 1


def test_duplicate_linkedin_urls_in_one_file_do_not_create_duplicates(client, organization, admin_user, db_session):
    csv_text = (
        "First Name,Last Name,LinkedIn URL\n"
        "Jamie,Fox,linkedin.com/in/jamiefox\n"
        "Jamie,Fox,linkedin.com/in/jamiefox\n"
        "Jamie,Fox,LINKEDIN.COM/in/jamiefox\n"
    )
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, csv_text)
    body = response.json()
    assert body["created"] == 1
    assert body["csv_duplicate_rows"] == 2
    assert body["active_database_total"] == 1
    assert db_session.query(Alumni).count() == 1


def test_failed_import_leaves_previous_active_dataset_unchanged(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _csv_of([("Existing", "Alumnus")]))

    # Every row is missing required first/last name -> 0 valid rows -> the
    # import is treated as failed and rolled back entirely.
    broken_csv = "First Name,Last Name\n,\n,\n"
    response = client.post(
        "/admin/import-alumni",
        files={"file": ("broken.csv", io.BytesIO(broken_csv.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 500

    # The previous active dataset must be completely unchanged.
    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    names = {row["full_name"] for row in response.json()["data"]}
    assert names == {"Existing Alumnus"}
    assert response.json()["meta"]["total"] == 1


def test_admin_import_endpoint_works_without_an_organization_parameter(client, organization, admin_user):
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, _csv_of([("Solo", "Person")]))
    assert response.status_code == 200
    assert response.json()["created"] == 1


def test_get_alumni_data_works_without_an_organization_parameter(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _csv_of([("Solo", "Person")]))

    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 1


def test_get_analytics_summary_works_without_an_organization_parameter(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _csv_of([("Solo", "Person")]))

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["total_alumni"] == 1


def test_admin_current_import_endpoint_reflects_the_active_dataset(client, organization, admin_user):
    token = _login(client, "admin", "AdminPass123!")
    upload_response = _upload(client, token, _csv_of([(f"P{i}", f"L{i}") for i in range(4)]))
    csv_import_id = upload_response.json()["csv_import_id"]

    response = client.get("/admin/current-import", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["csv_import_id"] == csv_import_id
    assert body["active_database_total"] == 4
    assert body["status"] == "complete"
    assert body["filename"] == "alumni.csv"


# --- Exact reported header set (Last Name, First Name, Email, Linkedin URL,
# Company Name, Job Title, City, State, Notes, Verification Status,
# Verification Date, Education) + physical-line/data-row accounting ---


def _real_header_csv(n: int) -> str:
    header = (
        "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,"
        "Notes,Verification Status,Verification Date,Education"
    )
    lines = [header]
    for i in range(n):
        lines.append(
            f"Last{i},First{i},first{i}.last{i}@example.com,linkedin.com/in/first{i}last{i},"
            f"Company{i} Inc,Engineer,Indianapolis,IN,Some note,Updated,2026-06-15,"
            "Florida State University"
        )
    return "\n".join(lines) + "\n"


def test_250_physical_line_csv_with_header_produces_249_active_alumni(
    client, organization, admin_user, db_session
):
    csv_text = _real_header_csv(249)
    assert len(csv_text.splitlines()) == 250  # 1 header + 249 data lines

    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, csv_text)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["csv_physical_lines"] == 250
    assert body["csv_header_rows"] == 1
    assert body["csv_data_rows"] == 249
    assert body["csv_valid_rows"] == 249
    assert body["csv_invalid_rows"] == 0
    assert body["csv_duplicate_rows"] == 0
    assert body["created"] == 249
    assert body["active_database_total"] == 249

    alumni_response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert alumni_response.json()["meta"]["total"] == 249

    record = db_session.query(Alumni).filter(Alumni.first_name == "First0").one()
    assert record.company == "Company0 Inc"
    assert record.job_title == "Engineer"
    assert record.city == "Indianapolis"
    assert record.state == "Indiana"
    assert record.state_code == "IN"
    assert record.country == "United States"
    assert record.location_original == "Indianapolis, IN"
    assert record.display_location == "Indianapolis, IN"
    assert record.university == "Florida State University"
    assert record.email == "first0.last0@example.com"
    assert record.verification_status == "Updated"
    assert record.verification_date is not None
    assert record.verification_date.isoformat() == "2026-06-15"


def test_reimporting_the_same_250_line_csv_still_produces_249_active_alumni(
    client, organization, admin_user, db_session
):
    csv_text = _real_header_csv(249)
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, csv_text)

    response = _upload(client, token, csv_text)
    body = response.json()
    assert body["csv_data_rows"] == 249
    assert body["active_database_total"] == 249
    assert db_session.query(Alumni).count() == 249


def test_importing_a_new_200_row_csv_produces_exactly_200_active_alumni(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _real_header_csv(249))

    response = _upload(client, token, _real_header_csv(200))
    body = response.json()
    assert body["csv_data_rows"] == 200
    assert body["active_database_total"] == 200
    assert body["deactivated"] == 49

    active_count = (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    )
    assert active_count == 200


def test_old_companies_disappear_from_analytics_after_replacement(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    old_csv = (
        "First Name,Last Name,Company Name\n"
        "Old,Alumnus,Old Legacy Corp\n"
    )
    _upload(client, token, old_csv)

    new_csv = (
        "First Name,Last Name,Company Name\n"
        "New,Alumnus,New Fresh Inc\n"
    )
    _upload(client, token, new_csv)

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    body = response.json()
    company_names = {c["name"] for c in body["top_companies"]}
    assert "New Fresh Inc" in company_names
    assert "Old Legacy Corp" not in company_names


def test_get_alumni_data_count_matches_active_database_total(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    upload_response = _upload(client, token, _real_header_csv(37))
    active_total = upload_response.json()["active_database_total"]

    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["meta"]["total"] == active_total == 37


def test_get_analytics_summary_total_matches_active_database_total(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    upload_response = _upload(client, token, _real_header_csv(23))
    active_total = upload_response.json()["active_database_total"]

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["total_alumni"] == active_total == 23


def test_375_historical_replaced_by_249_active_dataset(client, organization, admin_user, db_session):
    """Exact production regression: a cumulative historical table of 375
    alumni must shrink to an ACTIVE dashboard total of 249 when a 249-row
    CSV is uploaded in replace mode. Historical rows remain stored but
    inactive; old companies disappear from analytics.
    """
    token = _login(client, "admin", "AdminPass123!")

    # Seed 375 historical active alumni across two prior imports so the
    # starting state mirrors production's cumulative merge table.
    _upload(client, token, _real_header_csv(200))
    _upload(client, token, _csv_of([(f"Legacy{i}", f"Person{i}") for i in range(175)]))
    assert (
        db_session.query(AlumniOrganization)
        .filter(AlumniOrganization.organization_id == organization.id)
        .count()
    ) == 375
    org_alumni_ids = [
        row[0]
        for row in db_session.query(AlumniOrganization.alumni_id)
        .filter(AlumniOrganization.organization_id == organization.id)
        .all()
    ]
    assert (
        db_session.query(Alumni).filter(Alumni.id.in_(org_alumni_ids), Alumni.is_active.is_(True)).count()
    ) == 175  # second import already deactivated the first 200

    # Re-activate everything to simulate the pre-replace-mode production
    # state where every historical row was still "active".
    db_session.query(Alumni).filter(Alumni.id.in_(org_alumni_ids)).update(
        {"is_active": True}, synchronize_session=False
    )
    db_session.commit()
    assert (
        db_session.query(Alumni).filter(Alumni.id.in_(org_alumni_ids), Alumni.is_active.is_(True)).count()
    ) == 375

    response = _upload(client, token, _real_header_csv(249))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["created"] + body["updated"] + body["unchanged"] == 249
    assert body["active_database_total"] == 249
    assert body["database_total"] == 249
    assert body["historical_database_total"] >= 375
    assert body["deactivated"] == 375 - body["updated"] - body["unchanged"]

    alumni_response = client.get(
        "/alumni-data",
        params={"page_size": 200},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert alumni_response.json()["meta"]["total"] == 249

    analytics = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert analytics.json()["total_alumni"] == 249
    company_names = {c["name"] for c in analytics.json()["top_companies"]}
    assert "Company0 Inc" in company_names
    # Legacy people from the 175-row seed used no Company Name column, so
    # they contributed no company - but any leftover active legacy company
    # from a prior larger import must also be gone. Spot-check that active
    # count itself is authoritative.
    assert (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(False))
        .count()
    ) == body["historical_database_total"] - 249


def test_reimporting_same_249_file_creates_zero_new_rows(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = _real_header_csv(249)
    _upload(client, token, csv_text)

    response = _upload(client, token, csv_text)
    body = response.json()
    assert body["created"] == 0
    assert body["newly_created_identifiers"] == []
    assert body["active_database_total"] == 249
    assert body["historical_database_total"] == 249
    assert db_session.query(Alumni).count() == 249


def test_linkedin_url_formatting_variants_do_not_recreate_alumni(
    client, organization, admin_user, db_session
):
    """Historical rows often store LinkedIn URLs with www., trailing
    slashes, or query strings (from older imports that did not normalize).
    Reimporting the same person with a cleaned URL must UPDATE, never CREATE.
    """
    historical = [
        Alumni(
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            email="ada@example.com",
            linkedin_url="https://www.linkedin.com/in/ada-lovelace/?trk=xyz",
        ),
        Alumni(
            first_name="Alan",
            last_name="Turing",
            full_name="Alan Turing",
            email="alan@example.com",
            linkedin_url="https://linkedin.com/in/alan-turing/",
        ),
        Alumni(
            first_name="Grace",
            last_name="Hopper",
            full_name="Grace Hopper",
            email="grace@example.com",
            linkedin_url="HTTP://WWW.LinkedIn.com/in/Grace-Hopper#about",
        ),
        Alumni(
            first_name="Country",
            last_name="Subdomain",
            full_name="Country Subdomain",
            email="country@example.com",
            linkedin_url="https://uk.linkedin.com/in/country-subdomain/?trk=xyz",
        ),
        Alumni(
            first_name="No",
            last_name="LinkedIn",
            full_name="No LinkedIn",
            email="nolink@example.com",
        ),
        Alumni(first_name="Name", last_name="Only", full_name="Name Only"),
        Alumni(
            first_name="Email",
            last_name="Match",
            full_name="Email Match",
            email="emailmatch@example.com",
        ),
    ]
    for alumni in historical:
        db_session.add(alumni)
        db_session.flush()
        db_session.add(
            AlumniOrganization(
                alumni_id=alumni.id,
                organization_id=organization.id,
            )
        )
    db_session.commit()
    assert db_session.query(Alumni).count() == 7

    token = _login(client, "admin", "AdminPass123!")
    second_csv = (
        "First Name,Last Name,Email,Linkedin URL\n"
        "Ada,Lovelace,ada@example.com,linkedin.com/in/ada-lovelace\n"
        "Alan,Turing,alan@example.com,https://www.linkedin.com/in/alan-turing\n"
        "Grace,Hopper,grace@example.com,https://linkedin.com/in/grace-hopper\n"
        "Country,Subdomain,country@example.com,https://www.linkedin.com/in/country-subdomain\n"
        "No,LinkedIn,nolink@example.com,\n"
        "Name,Only,,\n"
        "Email,Match,emailmatch@example.com,\n"
    )
    response = _upload(client, token, second_csv)
    body = response.json()
    assert body["created"] == 0, body.get("newly_created_identifiers")
    assert body["import_logic_version"] == "replace-v2"
    assert body["active_database_total"] == 7
    assert body["historical_database_total"] == 7
    assert db_session.query(Alumni).count() == 7
    assert body["newly_created_identifiers"] == []


def test_failed_import_preserves_previous_active_dataset_and_historical_counts(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    first = _upload(client, token, _real_header_csv(12))
    assert first.json()["active_database_total"] == 12

    broken_csv = "First Name,Last Name\n,\n,\n"
    response = client.post(
        "/admin/import-alumni",
        files={"file": ("broken.csv", io.BytesIO(broken_csv.encode("utf-8")), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 500

    alumni_response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    assert alumni_response.json()["meta"]["total"] == 12
    assert (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    ) == 12


def test_import_response_includes_replace_v2_logic_version(client, organization, admin_user):
    """Deploy marker: if this assertion fails on Render, the live service is
    still running a stale merge/upsert build that never reached replace-v2.
    """
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, _csv_of([("Version", "Marker")]))
    assert response.status_code == 200
    body = response.json()
    assert body["import_logic_version"] == "replace-v2"
    assert body["active_database_total"] == 1
    assert "historical_database_total" in body


def test_live_import_route_calls_replacement_service_not_legacy_merge(monkeypatch, client, organization, admin_user):
    """Wire-up guard: POST /admin/import-alumni must call import_alumni_csv
    (the replace-mode service), not some older merge/upsert helper.
    """
    from app.routers import admin_routes
    from app.services.csv_import_service import ImportSummary

    calls: list[str] = []

    def _fake_import(db, organization_record, contents, imported_by_user_id=None, filename=None):
        calls.append("import_alumni_csv")
        summary = ImportSummary(
            created=1,
            active_database_total=1,
            historical_database_total=1,
            database_total=1,
            import_logic_version="replace-v2",
            csv_import_id="test-import-id",
            filename=filename,
        )
        return summary

    monkeypatch.setattr(admin_routes, "import_alumni_csv", _fake_import)

    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, _csv_of([("Wire", "Check")]))
    assert response.status_code == 200
    assert calls == ["import_alumni_csv"]
    assert response.json()["import_logic_version"] == "replace-v2"


def test_names_with_parentheses_and_punctuation_still_match_on_reimport(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    first_csv = (
        "First Name,Last Name,Email\n"
        "John (Johnny),O'Neil,john.oneil@example.com\n"
    )
    _upload(client, token, first_csv)

    second_csv = (
        "First Name,Last Name,Email\n"
        "John,ONeil,john.oneil@example.com\n"
    )
    # Email is the strong match here; also verify parenthetical name forms
    # would not create a second row if email were absent.
    response = _upload(client, token, second_csv)
    body = response.json()
    assert body["created"] == 0
    assert body["active_database_total"] == 1
    assert db_session.query(Alumni).count() == 1


def test_parenthetical_name_only_match_without_email_or_linkedin(
    client, organization, admin_user, db_session
):
    historical = Alumni(
        first_name="John (Johnny)",
        last_name="Smith",
        full_name="John (Johnny) Smith",
    )
    db_session.add(historical)
    db_session.flush()
    db_session.add(
        AlumniOrganization(
            alumni_id=historical.id,
            organization_id=organization.id,
        )
    )
    db_session.commit()

    token = _login(client, "admin", "AdminPass123!")
    response = _upload(
        client,
        token,
        "First Name,Last Name\nJohn,Smith\n",
    )
    body = response.json()
    assert body["created"] == 0, body.get("newly_created_identifiers")
    assert body["active_database_total"] == 1
    assert db_session.query(Alumni).count() == 1
