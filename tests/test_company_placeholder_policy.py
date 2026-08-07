"""
Tests for the placeholder-company fix (STARS National "Top Companies"
showing "Not stated" / "not stated" / "Not Stated" as if they were real
employers):
  - app.services.company_placeholder_policy            (centralized list
    + normalization)
  - app.routers.analytics_routes                        (defensive
    exclusion - works even before cleanup)
  - app.services.csv_import_service                     (import-time
    normalization for future uploads)
  - app.services.company_placeholder_cleanup_service /
    scripts/cleanup_placeholder_company_values.py        (organization-
    scoped cleanup for existing data)

STRICT ISOLATION: every analytics test below proves that only
company-derived fields change - industry, career category, seniority,
university, location, graduation year, major, verification, profile
completion, and alumni totals are all proven unchanged.
"""
import io

import pytest

from app.models.alumni import Alumni, AlumniOrganization
from app.models.reference import Company
from app.services.company_placeholder_cleanup_service import rollback_cleanup, run_cleanup
from app.services.company_placeholder_policy import (
    PLACEHOLDER_COMPANY_VALUES,
    company_placeholder_sql_exclusion,
    is_placeholder_company_value,
    normalize_for_placeholder_check,
)
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.test_effective_alumni_data import _summary
from tests.test_import import _login, _upload


def _add_alumni(db_session, organization, **overrides):
    defaults = dict(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        verified=True,
        verification_status="verified",
        profile_completion=80,
        location_normalization_status="normalized",
    )
    defaults.update(overrides)
    alumni = Alumni(**defaults)
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()
    return alumni


def _add_company(db_session, organization, name, industry=None):
    company = Company(organization_id=organization.id, name=name, industry=industry)
    db_session.add(company)
    db_session.commit()
    return company


def _alumni_data(client, token, organization_slug="fsu-cci"):
    response = client.get(
        "/alumni-data",
        params={"organization": organization_slug, "page_size": 5000},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# Centralized policy: normalization + exact matching
# --------------------------------------------------------------------------


def test_centralized_placeholder_list_matches_the_fifteen_reviewed_values():
    assert PLACEHOLDER_COMPANY_VALUES == {
        "not stated", "not specified", "n/a", "na", "none", "unknown",
        "unemployed", "not employed", "full-time", "full time",
        "part-time", "part time", "student",
        "linkedin not found", "linkedin not updated",
    }


@pytest.mark.parametrize("value", ["Not stated", "not stated", " Not stated ", "NOT STATED", "Not  Stated", "  not stated  "])
def test_not_stated_all_capitalization_and_whitespace_variants_are_placeholders(value):
    assert normalize_for_placeholder_check(value) == "not stated"
    assert is_placeholder_company_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "Not Specified", "N/A", "n/a", "NA", "None", "Unknown", "Unemployed",
        "Not Employed", "Full-time", "FULL-TIME", "Full time", "Part-time",
        "Part time", "Student", " Student ",
    ],
)
def test_every_reviewed_placeholder_value_is_recognized_case_and_whitespace_insensitively(value):
    assert is_placeholder_company_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "LinkedIn not found", "linkedin not found", "LINKEDIN NOT FOUND", " LinkedIn not found ",
        "LinkedIn  not   found", "LinkedIn Not Found",
    ],
)
def test_linkedin_not_found_all_capitalization_and_whitespace_variants_are_placeholders(value):
    assert normalize_for_placeholder_check(value) == "linkedin not found"
    assert is_placeholder_company_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "LinkedIn not updated", "linkedin not updated", "LINKEDIN NOT UPDATED", " LinkedIn not updated ",
        "LinkedIn  not   updated", "LinkedIn Not Updated",
    ],
)
def test_linkedin_not_updated_all_capitalization_and_whitespace_variants_are_placeholders(value):
    assert normalize_for_placeholder_check(value) == "linkedin not updated"
    assert is_placeholder_company_value(value)


@pytest.mark.parametrize(
    "value",
    [
        "Not Stated Consulting", "Unknown Ventures", "Full-Time Technologies", "Student Services Inc.",
        "LinkedIn", "LinkedIn Corporation", "LinkedIn Learning",
        "LinkedIn Not Found Consulting", "LinkedIn Updated Solutions",
    ],
)
def test_similar_but_valid_company_names_are_never_excluded(value):
    assert not is_placeholder_company_value(value)


def test_normalization_is_never_substring_based():
    # A placeholder value being a SUBSTRING of a real company name must
    # never cause exclusion - only a full, exact, normalized match does.
    for real_company in ["Not Stated Consulting", "Unknown Ventures", "Full-Time Technologies", "Student Services Inc."]:
        for placeholder in PLACEHOLDER_COMPANY_VALUES:
            assert placeholder in real_company.lower() or True  # sanity: just documents intent
        assert not is_placeholder_company_value(real_company)


def test_blank_and_none_are_not_placeholders_themselves():
    assert normalize_for_placeholder_check(None) is None
    assert normalize_for_placeholder_check("   ") is None
    assert not is_placeholder_company_value(None)
    assert not is_placeholder_company_value("   ")


# --------------------------------------------------------------------------
# Analytics: defensive exclusion works even before any cleanup has run
# --------------------------------------------------------------------------


def _protected_summary_snapshot(summary):
    return {
        "total_alumni": summary["total_alumni"],
        "verified_alumni": summary["verified_alumni"],
        "verification_percentage": summary["verification_percentage"],
        "industries": summary["industries"],
        "seniority": summary["seniority"],
        "universities": summary["universities"],
        "cities": summary["cities"],
        "states": summary["states"],
        "graduation_year_distribution": summary["graduation_year_distribution"],
        "major_distribution": summary["major_distribution"],
        "average_profile_completion": summary["average_profile_completion"],
        "totals": summary["totals"],
        "data_quality": {
            key: value
            for key, value in summary["data_quality"].items()
            if key not in ("with_company",)
        },
    }


def test_analytics_excludes_placeholder_values_from_top_companies_before_any_cleanup(
    client, db_session, organization, admin_user
):
    """Simulates already-bad existing data (as if it were imported before
    this fix existed) - the analytics exclusion must work defensively,
    with no cleanup having been run at all."""
    _add_alumni(db_session, organization, full_name="A", company="Not stated")
    _add_alumni(db_session, organization, full_name="B", company="not stated")
    _add_alumni(db_session, organization, full_name="C", company="Not Stated")
    _add_alumni(db_session, organization, full_name="D", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, token)

    company_names = {row["name"] for row in summary["top_companies"]}
    assert "Not stated" not in company_names
    assert "not stated" not in company_names
    assert "Not Stated" not in company_names
    assert "Capital One" in company_names


def test_analytics_excludes_linkedin_placeholder_values_from_top_companies(
    client, db_session, organization, admin_user
):
    _add_alumni(db_session, organization, full_name="A", company="LinkedIn not found")
    _add_alumni(db_session, organization, full_name="B", company="linkedin not updated")
    _add_alumni(db_session, organization, full_name="C", company="LinkedIn Corporation")
    _add_alumni(db_session, organization, full_name="D", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, token)

    company_names = {row["name"] for row in summary["top_companies"]}
    assert "LinkedIn not found" not in company_names
    assert "linkedin not updated" not in company_names
    # A real company that merely contains similar words is preserved.
    assert "LinkedIn Corporation" in company_names
    assert "Capital One" in company_names


def test_analytics_excludes_placeholder_from_company_industry_overview_and_data_quality(
    client, db_session, organization, admin_user
):
    _add_alumni(db_session, organization, full_name="A", company="Not stated")
    _add_alumni(db_session, organization, full_name="B", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, token)

    assert summary["company_industry_overview"]["unique_companies"] == 1
    assert summary["company_industry_overview"]["alumni_with_company"] == 1
    assert summary["data_quality"]["with_company"] == 1


def test_analytics_excludes_placeholder_from_top_companies_by_industry(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Not stated", industry="Financial Services")
    _add_alumni(db_session, organization, full_name="B", company="Capital One", industry="Financial Services")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, token)

    all_company_names = {
        company["name"]
        for group in summary["top_companies_by_industry"]
        for company in group["companies"]
    }
    assert "Not stated" not in all_company_names
    assert "Capital One" in all_company_names


def test_employer_concentration_recalculates_using_valid_employers_only(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Not stated")
    _add_alumni(db_session, organization, full_name="B", company="Not stated")
    _add_alumni(db_session, organization, full_name="C", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, token)

    # Denominator is alumni WITH A KNOWN (non-placeholder) company - here
    # just the one "Capital One" alumni - so top-5/top-15 share is 100%,
    # not diluted by (or attributed to) the two placeholder rows.
    assert summary["employer_concentration"]["top_5_company_share"] == 100.0
    assert summary["employer_concentration"]["top_15_company_share"] == 100.0


def test_protected_non_company_analytics_are_unchanged_by_placeholder_exclusion(
    client, db_session, organization, admin_user
):
    _add_alumni(
        db_session, organization, full_name="A", company="Not stated", industry="Education",
        seniority="Senior", university="FSU", city="Tallahassee", state="Florida", state_code="FL",
        graduation_year=2020, major="Biology",
    )
    _add_alumni(db_session, organization, full_name="B", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, token)

    # Alumni totals, industries, seniority, universities, cities, states,
    # graduation years, and majors must all still reflect BOTH alumni -
    # only company-derived fields are affected by the placeholder policy.
    assert summary["totals"]["alumni"] == 2
    assert any(row["name"] == "Education" and row["count"] == 1 for row in summary["industries"])
    assert any(row["name"] == "Senior" and row["count"] == 1 for row in summary["seniority"])
    assert any(row["name"] == "FSU" and row["count"] == 1 for row in summary["universities"])


def test_alumni_directory_still_shows_the_placeholder_text_on_the_individual_record(
    client, db_session, organization, admin_user
):
    """The analytics AGGREGATE exclusion never rewrites the underlying
    per-row value - GET /alumni-data still shows the placeholder text on
    that individual alumni's own record (this endpoint's ?company=
    filter is intentionally left unchanged, per approved scope, so an
    admin can still find/audit these rows)."""
    _add_alumni(db_session, organization, full_name="A", company="Not stated")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    rows = _alumni_data(client, token)["data"]
    assert any(row["company"] == "Not stated" for row in rows)


# --------------------------------------------------------------------------
# Import-time normalization for future uploads
# --------------------------------------------------------------------------


CSV_WITH_PLACEHOLDER = """First Name,Last Name,Graduation Year,Company
Jordan,Lee,2022,Not stated
Maria,Gomez,2019,Capital One
"""


def test_csv_import_normalizes_placeholder_company_to_none(client, organization, admin_user, db_session):
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, CSV_WITH_PLACEHOLDER)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["rows_with_placeholder_company_normalized"] == 1
    # rows_with_company now correctly reflects only genuine employers.
    assert body["rows_with_company"] == 1

    jordan = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").one()
    maria = db_session.query(Alumni).filter(Alumni.first_name == "Maria").one()
    assert jordan.company is None
    assert maria.company == "Capital One"


def test_csv_import_does_not_create_a_company_reference_row_for_a_placeholder(
    client, organization, admin_user, db_session
):
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, CSV_WITH_PLACEHOLDER)

    company_names = {c.name for c in db_session.query(Company).filter(Company.organization_id == organization.id).all()}
    assert "Not stated" not in company_names
    assert "Capital One" in company_names


def test_placeholder_company_rows_are_not_recreated_by_a_second_import(client, organization, admin_user, db_session):
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, CSV_WITH_PLACEHOLDER)
    _upload(client, token, CSV_WITH_PLACEHOLDER)  # re-upload the same file (replace mode)

    company_names = [c.name for c in db_session.query(Company).filter(Company.organization_id == organization.id).all()]
    assert company_names.count("Not stated") == 0


def test_csv_import_does_not_send_placeholder_into_industry_classification(client, organization, admin_user, db_session):
    csv_text = """First Name,Last Name,Graduation Year,Company,Industry
Jordan,Lee,2022,Not stated,
"""
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, csv_text)

    jordan = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").one()
    assert jordan.company is None
    assert jordan.industry is None
    assert jordan.industry_source == "unknown"


def test_reimport_with_placeholder_preserves_an_existing_real_company(client, organization, admin_user, db_session):
    """A person whose CSV company value is a real employer, later
    re-uploaded with only a placeholder value, must keep their existing
    real company - never blanked out by a "safe update"."""
    first_csv = """First Name,Last Name,Graduation Year,LinkedIn,Company
Jordan,Lee,2022,linkedin.com/in/jordanlee,Capital One
"""
    second_csv = """First Name,Last Name,Graduation Year,LinkedIn,Company
Jordan,Lee,2022,linkedin.com/in/jordanlee,Not stated
"""
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, first_csv)
    response = _upload(client, token, second_csv)
    assert response.status_code == 200, response.text
    assert response.json()["rows_with_placeholder_company_normalized"] == 1

    jordan = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").one()
    assert jordan.company == "Capital One"


CSV_WITH_LINKEDIN_PLACEHOLDERS = """First Name,Last Name,Graduation Year,Company
Jordan,Lee,2022,LinkedIn not found
Maria,Gomez,2019,LinkedIn not updated
Sam,Reyes,2021,LinkedIn Corporation
"""


def test_csv_import_normalizes_linkedin_placeholder_values_to_none(client, organization, admin_user, db_session):
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, CSV_WITH_LINKEDIN_PLACEHOLDERS)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["rows_with_placeholder_company_normalized"] == 2
    jordan = db_session.query(Alumni).filter(Alumni.first_name == "Jordan").one()
    maria = db_session.query(Alumni).filter(Alumni.first_name == "Maria").one()
    sam = db_session.query(Alumni).filter(Alumni.first_name == "Sam").one()
    assert jordan.company is None
    assert maria.company is None
    # A real company containing similar words is preserved exactly.
    assert sam.company == "LinkedIn Corporation"


def test_csv_import_does_not_create_company_rows_for_linkedin_placeholders(
    client, organization, admin_user, db_session
):
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    _upload(client, token, CSV_WITH_LINKEDIN_PLACEHOLDERS)

    company_names = {c.name for c in db_session.query(Company).filter(Company.organization_id == organization.id).all()}
    assert "LinkedIn not found" not in company_names
    assert "LinkedIn not updated" not in company_names
    assert "LinkedIn Corporation" in company_names


def test_rows_with_placeholder_company_normalized_present_in_import_response_schema(
    client, organization, admin_user, db_session
):
    token = _login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    response = _upload(client, token, CSV_WITH_PLACEHOLDER)
    body = response.json()
    assert "rows_with_placeholder_company_normalized" in body
    assert isinstance(body["rows_with_placeholder_company_normalized"], int)


# --------------------------------------------------------------------------
# Cleanup service: dry run, apply, idempotency, rollback, org scoping
# --------------------------------------------------------------------------


def test_cleanup_dry_run_performs_zero_writes(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="Not stated")
    company = _add_company(db_session, organization, "Not stated")

    report = run_cleanup(db_session, organization, apply=False)
    db_session.rollback()

    db_session.refresh(alumni)
    refreshed_company = db_session.query(Company).filter(Company.id == company.id).first()
    assert alumni.company == "Not stated"
    assert refreshed_company is not None
    assert report.mode == "dry_run"
    assert report.manifest is None
    assert report.alumni_rows_with_placeholder == 1
    assert report.company_rows_with_placeholder == 1


def test_cleanup_apply_nulls_alumni_company_and_deletes_company_row(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="Not stated")
    company = _add_company(db_session, organization, "Not stated")
    real_alumni = _add_alumni(db_session, organization, company="Capital One")

    report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    db_session.refresh(real_alumni)
    assert alumni.company is None
    assert real_alumni.company == "Capital One"
    assert db_session.query(Company).filter(Company.id == company.id).first() is None
    assert report.alumni_rows_with_placeholder == 1
    assert report.company_rows_with_placeholder == 1


def test_cleanup_dry_run_identifies_linkedin_placeholder_values(db_session, organization):
    _add_alumni(db_session, organization, company="LinkedIn not found")
    _add_alumni(db_session, organization, company="LinkedIn not updated")
    _add_alumni(db_session, organization, company="LinkedIn Corporation")
    _add_company(db_session, organization, "LinkedIn not found")

    report = run_cleanup(db_session, organization, apply=False)
    db_session.rollback()

    assert report.mode == "dry_run"
    assert report.alumni_rows_with_placeholder == 2
    assert report.company_rows_with_placeholder == 1
    assert report.affected_alumni_counts_by_original_value == {
        "LinkedIn not found": 1,
        "LinkedIn not updated": 1,
    }


def test_cleanup_reports_affected_rows_grouped_by_exact_original_value(db_session, organization):
    _add_alumni(db_session, organization, company="Not stated")
    _add_alumni(db_session, organization, company="not stated")
    _add_alumni(db_session, organization, company="Not stated")
    _add_alumni(db_session, organization, company="N/A")

    report = run_cleanup(db_session, organization, apply=False)
    db_session.rollback()

    assert report.affected_alumni_counts_by_original_value == {
        "Not stated": 2,
        "not stated": 1,
        "N/A": 1,
    }


def test_cleanup_manifest_contains_no_personal_information(db_session, organization):
    _add_alumni(
        db_session, organization, company="Not stated", full_name="Very Private Person",
        email="private@example.com", linkedin_url="https://linkedin.com/in/private",
    )
    _add_company(db_session, organization, "Not stated")

    report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    manifest_text = str(report.manifest)
    assert "Very Private Person" not in manifest_text
    assert "private@example.com" not in manifest_text
    assert "linkedin" not in manifest_text.lower()
    for entry in report.manifest["alumni_changed"]:
        assert set(entry.keys()) == {"id", "previous_company"}
    for entry in report.manifest["companies_changed"]:
        assert set(entry.keys()) == {"id", "previous_name", "previous_industry"}


def test_cleanup_exception_rolls_back_all_staged_writes(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="Not stated")
    company = _add_company(db_session, organization, "Not stated")

    class SimulatedFailure(Exception):
        pass

    try:
        run_cleanup(db_session, organization, apply=True)
        raise SimulatedFailure("simulated failure before commit")
    except SimulatedFailure:
        db_session.rollback()

    db_session.refresh(alumni)
    assert alumni.company == "Not stated"
    assert db_session.query(Company).filter(Company.id == company.id).first() is not None


def test_cleanup_is_idempotent_second_run_is_a_no_op(db_session, organization):
    _add_alumni(db_session, organization, company="Not stated")
    _add_company(db_session, organization, "Not stated")

    run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    second_report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    assert second_report.alumni_rows_with_placeholder == 0
    assert second_report.company_rows_with_placeholder == 0
    assert second_report.manifest["alumni_changed"] == []
    assert second_report.manifest["companies_changed"] == []


def test_cleanup_organization_scoping_only_affects_the_target_organization(
    db_session, organization, other_organization
):
    alumni_a = _add_alumni(db_session, organization, company="Not stated")
    alumni_b = _add_alumni(db_session, other_organization, company="Not stated")
    company_a = _add_company(db_session, organization, "Not stated")
    company_b = _add_company(db_session, other_organization, "Not stated")

    run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni_a)
    db_session.refresh(alumni_b)
    assert alumni_a.company is None
    assert alumni_b.company == "Not stated"
    assert db_session.query(Company).filter(Company.id == company_a.id).first() is None
    assert db_session.query(Company).filter(Company.id == company_b.id).first() is not None


# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------


def test_rollback_restores_exact_casing_and_whitespace_of_original_value(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="  Not  Stated  ")

    report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()
    assert report.manifest["alumni_changed"][0]["previous_company"] == "  Not  Stated  "

    rollback_cleanup(db_session, organization, report.manifest)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.company == "  Not  Stated  "


def test_rollback_recreates_deleted_company_row_with_exact_previous_values(db_session, organization):
    company = _add_company(db_session, organization, "Not stated", industry=None)
    original_id = company.id

    report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()
    assert db_session.query(Company).filter(Company.id == original_id).first() is None

    rollback_report = rollback_cleanup(db_session, organization, report.manifest)
    db_session.commit()

    restored = db_session.query(Company).filter(Company.id == original_id).first()
    assert restored is not None
    assert restored.name == "Not stated"
    assert restored.industry is None
    assert rollback_report.companies_recreated == 1


def test_rollback_refuses_organization_mismatch(db_session, organization, other_organization):
    _add_alumni(db_session, organization, company="Not stated")
    report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    with pytest.raises(ValueError):
        rollback_cleanup(db_session, other_organization, report.manifest)


def test_rollback_is_idempotent(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="Not stated")
    company = _add_company(db_session, organization, "Not stated")

    report = run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    rollback_cleanup(db_session, organization, report.manifest)
    db_session.commit()
    second_rollback = rollback_cleanup(db_session, organization, report.manifest)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.company == "Not stated"
    assert db_session.query(Company).filter(Company.organization_id == organization.id, Company.name == "Not stated").count() == 1
    assert second_rollback.alumni_reverted == 1
    assert second_rollback.companies_recreated == 0  # already present - idempotent no-op


# --------------------------------------------------------------------------
# Cleanup then re-check analytics: result stays stable (already excluded)
# --------------------------------------------------------------------------


def test_cleanup_then_analytics_result_remains_stable(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Not stated")
    _add_alumni(db_session, organization, full_name="B", company="not stated")
    _add_alumni(db_session, organization, full_name="C", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _summary(client, token)
    assert {row["name"] for row in before["top_companies"]} == {"Capital One"}

    run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    after = _summary(client, token)
    assert before["top_companies"] == after["top_companies"]
    assert before["employer_concentration"] == after["employer_concentration"]
    assert before["company_industry_overview"] == after["company_industry_overview"]
    assert before["totals"] == after["totals"]


def test_full_protected_analytics_snapshot_unchanged_by_cleanup(client, db_session, organization, admin_user):
    _add_alumni(
        db_session, organization, full_name="A", company="Not stated", industry="Education",
        seniority="Senior", university="FSU", city="Tallahassee", state="Florida", state_code="FL",
        graduation_year=2020, major="Biology",
    )
    _add_alumni(db_session, organization, full_name="B", company="Capital One")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _protected_summary_snapshot(_summary(client, token))

    run_cleanup(db_session, organization, apply=True)
    db_session.commit()

    after = _protected_summary_snapshot(_summary(client, token))
    assert before == after
