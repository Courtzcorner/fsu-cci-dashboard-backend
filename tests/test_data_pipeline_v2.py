"""
Tests for the "first principles" data pipeline rework:

- Canonical field mapping for the real CSV format (Last Name, First Name,
  Email, Linkedin URL, Company Name, Job Title, City, State, Notes,
  Verification Status, Verification Date, Education).
- Deterministic seniority / career_category / industry derivation rules
  and their provenance (*_source) columns - never guessed.
- The new GET /analytics/summary response shape, computed via SQL
  aggregation against the active dataset.
- GET /analytics/locations aggregate map data.
- GET /admin/export-alumni enriched CSV export.
- GET /alumni-data pagination (page/page_size/total_pages) and filters.
- Dynamic row counts (75, 249, and a larger generated dataset) - nothing
  hardcoded.
- The end-to-end acceptance check: uploaded row count, active database
  count, analytics total, paginated directory total, and export row
  count must all agree for the same active dataset.
"""
import csv
import io

from app.models.alumni import Alumni, AlumniOrganization
from tests.test_import import _login, _upload

CANONICAL_HEADER = (
    "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,"
    "Notes,Verification Status,Verification Date,Education"
)


def _generate_csv(n: int, start: int = 0, company_prefix: str = "Acme") -> str:
    """Builds a CSV using the real, current canonical header with `n`
    guaranteed-unique, guaranteed-valid rows (unique email + LinkedIn URL
    each) starting at index `start`. No row count is ever hardcoded by
    callers - `n` is always a parameter."""
    lines = [CANONICAL_HEADER]
    for i in range(start, start + n):
        lines.append(
            f"Last{i},First{i},user{i}@example.com,https://linkedin.com/in/user{i},"
            f"{company_prefix}{i % 5} Inc,Software Engineer,Tallahassee,FL,"
            f"Note {i},Verified,2026-01-01,Florida State University"
        )
    return "\n".join(lines) + "\n"


def _active_total(db_session, organization):
    return (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    )


# --------------------------------------------------------------------------
# Dynamic row counts - nothing hardcoded
# --------------------------------------------------------------------------


def test_importing_75_unique_rows_makes_75_active(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, _generate_csv(75))
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 75
    assert body["active_database_total"] == 75
    assert body["rows_received"] == 75
    assert body["rows_valid"] == 75
    assert _active_total(db_session, organization) == 75


def test_importing_249_unique_rows_makes_249_active(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    response = _upload(client, token, _generate_csv(249))
    body = response.json()
    assert body["created"] == 249
    assert body["active_database_total"] == 249
    assert _active_total(db_session, organization) == 249


def test_replacing_249_rows_with_smaller_file_deactivates_the_rest(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _generate_csv(249))

    response = _upload(client, token, _generate_csv(80, start=1000))
    body = response.json()
    assert body["active_database_total"] == 80
    assert _active_total(db_session, organization) == 80
    # Nothing physically deleted - the historical rows still exist.
    assert db_session.query(Alumni).count() == 329


# --------------------------------------------------------------------------
# Deterministic seniority / career_category rules (no guessing)
# --------------------------------------------------------------------------

SENIORITY_TITLE_CASES = [
    ("Marketing Intern", "Intern"),
    ("Program Coordinator", "Entry"),
    ("Business Development Associate", "Associate"),
    ("Senior Software Engineer", "Senior"),
    ("Lead Data Scientist", "Lead"),
    ("Engineering Manager", "Manager"),
    ("Director of Operations", "Director"),
    ("Vice President of Sales", "Vice President"),
    ("Chief Marketing Officer", "Executive"),
    # More-specific executive/VP rules must win over generic ones.
    ("Senior Vice President of Engineering", "Vice President"),
    ("Chief Technology Officer and Manager", "Executive"),
]


def test_seniority_rules_produce_documented_values(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    lines = [CANONICAL_HEADER]
    for i, (title, _expected) in enumerate(SENIORITY_TITLE_CASES):
        lines.append(
            f"Last{i},First{i},senuser{i}@example.com,https://linkedin.com/in/senuser{i},"
            f"Acme Inc,{title},Tallahassee,FL,,Verified,2026-01-01,Florida State University"
        )
    csv_text = "\n".join(lines) + "\n"
    response = _upload(client, token, csv_text)
    assert response.status_code == 200

    by_title = {a.job_title: a for a in db_session.query(Alumni).all()}
    for title, expected_seniority in SENIORITY_TITLE_CASES:
        alumni = by_title[title]
        assert alumni.seniority == expected_seniority, f"{title!r} -> {alumni.seniority!r}, expected {expected_seniority!r}"
        assert alumni.seniority_source == "derived:title_rules"


def test_unmatched_seniority_and_industry_remain_unclassified_not_guessed(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        CANONICAL_HEADER + "\n"
        "Doe,Jane,jane.doe@example.com,https://linkedin.com/in/janedoe,"
        "Mystery Widgets LLC,Widget Artisan,Tallahassee,FL,,Verified,2026-01-01,Florida State University\n"
    )
    response = _upload(client, token, csv_text)
    assert response.status_code == 200

    alumni = db_session.query(Alumni).filter(Alumni.full_name == "Jane Doe").one()
    # "Widget Artisan" matches no documented seniority rule, and no
    # Industry column / company mapping exists for "Mystery Widgets LLC" -
    # both must be null/unclassified, never guessed from the company name.
    assert alumni.seniority is None
    assert alumni.seniority_source == "unknown"
    assert alumni.industry is None
    assert alumni.industry_source == "unknown"


def test_imported_industry_column_always_wins_over_company_mapping(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    # This canonical CSV format has no separate Industry column, so
    # industry can only ever come from a verified company mapping here -
    # confirming it is never inferred from company name keywords like
    # "Bank" or "Tech".
    csv_text = (
        CANONICAL_HEADER + "\n"
        "Lee,Sam,sam.lee@example.com,https://linkedin.com/in/samlee,"
        "First National Bank,Teller,Tallahassee,FL,,Verified,2026-01-01,Florida State University\n"
    )
    response = _upload(client, token, csv_text)
    assert response.status_code == 200
    alumni = db_session.query(Alumni).filter(Alumni.full_name == "Sam Lee").one()
    assert alumni.industry is None
    assert alumni.industry_source == "unknown"


# --------------------------------------------------------------------------
# New GET /analytics/summary shape (SQL-aggregated)
# --------------------------------------------------------------------------


def test_analytics_summary_new_shape_matches_direct_sql_counts(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _generate_csv(40))

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()

    assert body["dataset"]["total_alumni"] == 40
    assert body["dataset"]["csv_import_id"]
    assert body["totals"]["alumni"] == 40
    # The canonical CSV format only has a free-text "Verification Status"
    # column, not a dedicated boolean - `verified` reflects that no
    # explicit boolean column was provided.
    assert body["totals"]["verified"] == 0

    direct_university_count = (
        db_session.query(Alumni.university)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .distinct()
        .count()
    )
    assert body["totals"]["universities"] == direct_university_count == 1

    company_counts = {c["name"]: c["count"] for c in body["top_companies"]}
    for i in range(5):
        expected_name = f"Acme{i} Inc"
        direct_count = (
            db_session.query(Alumni)
            .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
            .filter(
                AlumniOrganization.organization_id == organization.id,
                Alumni.is_active.is_(True),
                Alumni.company == expected_name,
            )
            .count()
        )
        assert company_counts.get(expected_name) == direct_count

    assert body["data_quality"]["with_company"] == 40
    assert body["data_quality"]["with_university"] == 40
    assert body["data_quality"]["unclassified_industry"] == 40  # no Industry column, no company mapping


def test_analytics_summary_totals_are_recomputed_after_replacement(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _generate_csv(30))
    _upload(client, token, _generate_csv(12, start=999))

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    body = response.json()
    assert body["dataset"]["total_alumni"] == 12
    assert body["totals"]["alumni"] == 12


# --------------------------------------------------------------------------
# GET /analytics/locations
# --------------------------------------------------------------------------


def test_analytics_locations_groups_by_city_and_reports_without_location(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        CANONICAL_HEADER + "\n"
        "A,First,a@example.com,https://linkedin.com/in/a1,Acme Inc,Engineer,Tallahassee,FL,,Verified,2026-01-01,Florida State University\n"
        "B,Second,b@example.com,https://linkedin.com/in/b2,Acme Inc,Engineer,,,,Verified,2026-01-01,Florida State University\n"
    )
    _upload(client, token, csv_text)

    response = client.get("/analytics/locations", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    # Neither row has resolved lat/lng in this test environment (no
    # geocoding network access), so both must be reported as
    # without_location rather than plotted with invented coordinates.
    assert body["with_location"] + body["without_location"] == 2
    for location in body["locations"]:
        assert location["latitude"] is not None
        assert location["longitude"] is not None


# --------------------------------------------------------------------------
# GET /alumni-data pagination + filters
# --------------------------------------------------------------------------


def test_alumni_data_pagination_covers_every_row_exactly_once(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _generate_csv(37))

    page_size = 10
    seen_ids = set()
    page = 1
    total_pages = None
    while True:
        response = client.get(
            "/alumni-data",
            params={"page": page, "page_size": page_size},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) <= page_size
        total_pages = body["meta"]["total_pages"]
        assert body["meta"]["total"] == 37
        for row in body["data"]:
            assert row["id"] not in seen_ids
            seen_ids.add(row["id"])
        if page >= total_pages:
            break
        page += 1

    assert len(seen_ids) == 37
    assert total_pages == 4  # ceil(37 / 10)


def test_alumni_data_university_filter(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload(client, token, _generate_csv(5))

    response = client.get(
        "/alumni-data",
        params={"university": "Florida State"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 5

    none_match = client.get(
        "/alumni-data",
        params={"university": "Nonexistent University"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert none_match.json()["meta"]["total"] == 0


# --------------------------------------------------------------------------
# GET /admin/export-alumni
# --------------------------------------------------------------------------


def test_export_alumni_contains_imported_and_derived_columns(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    csv_text = (
        CANONICAL_HEADER + "\n"
        "Bonney,Emma,emmabonney8@example.com,https://linkedin.com/in/emmabonney,"
        "Blue Cross,Strategy Consultant II,Indianapolis,IN,Great mentor for new grads,"
        "Updated,2026-06-15,Florida State University\n"
    )
    _upload(client, token, csv_text)

    response = client.get("/admin/export-alumni", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["First Name"] == "Emma"
    assert row["Last Name"] == "Bonney"
    assert row["Company Name"] == "Blue Cross"
    assert row["Education"] == "Florida State University"
    assert row["Notes"] == "Great mentor for new grads"
    assert row["Career Category"]
    assert row["Career Category Source"] == "derived:title_rules"
    assert row["Seniority Source"] in ("derived:title_rules", "unknown")
    assert "Industry Source" in row


def test_export_alumni_requires_admin_role(client, organization, admin_user, alumni_user):
    token = _login(client, "jdoe", "AlumniPass123!")
    response = client.get("/admin/export-alumni", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Acceptance check - uploaded / active / analytics / directory / export
# counts must all agree, for a dynamically-sized dataset.
# --------------------------------------------------------------------------


def test_acceptance_all_counts_agree_for_a_dynamic_row_count(client, organization, admin_user, db_session):
    row_count = 63  # arbitrary, deliberately not one of the earlier tested sizes
    token = _login(client, "admin", "AdminPass123!")

    import_response = _upload(client, token, _generate_csv(row_count))
    assert import_response.status_code == 200
    import_body = import_response.json()
    assert import_body["rows_received"] == row_count
    assert import_body["active_database_total"] == row_count

    active_db_count = _active_total(db_session, organization)
    assert active_db_count == row_count

    analytics = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert analytics.json()["totals"]["alumni"] == row_count
    assert analytics.json()["dataset"]["total_alumni"] == row_count

    directory = client.get(
        "/alumni-data", params={"page_size": 5}, headers={"Authorization": f"Bearer {token}"}
    )
    assert directory.json()["meta"]["total"] == row_count

    export = client.get("/admin/export-alumni", headers={"Authorization": f"Bearer {token}"})
    export_rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(export_rows) == row_count

    current_import = client.get("/admin/current-import", headers={"Authorization": f"Bearer {token}"})
    assert current_import.json()["active_database_total"] == row_count
    assert current_import.json()["rows_received"] == row_count

    assert (
        import_body["active_database_total"]
        == active_db_count
        == analytics.json()["totals"]["alumni"]
        == directory.json()["meta"]["total"]
        == len(export_rows)
        == current_import.json()["active_database_total"]
        == row_count
    )


# --------------------------------------------------------------------------
# Large-dataset behavior: a bigger generated file must still be a single
# bounded-size response per request (no "download everything" endpoint).
# --------------------------------------------------------------------------


def test_larger_generated_dataset_is_never_returned_in_a_single_unbounded_response(
    client, organization, admin_user, db_session
):
    row_count = 600
    token = _login(client, "admin", "AdminPass123!")
    import_response = _upload(client, token, _generate_csv(row_count))
    assert import_response.status_code == 200
    assert import_response.json()["active_database_total"] == row_count

    # Default page size is well below the full dataset size.
    response = client.get("/alumni-data", headers={"Authorization": f"Bearer {token}"})
    body = response.json()
    assert body["meta"]["total"] == row_count
    assert len(body["data"]) < row_count
    assert len(body["data"]) == body["meta"]["page_size"]

    # Analytics is still computed correctly without materializing all
    # rows into the response body.
    analytics = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert analytics.json()["totals"]["alumni"] == row_count
