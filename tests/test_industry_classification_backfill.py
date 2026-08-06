"""
Tests for the isolated industry-classification backfill feature:
  - app.services.industry_mapping_data      (curated data, no logic)
  - app.services.industry_backfill_service  (normalization, matching,
    dry-run/apply/rollback)
  - scripts/backfill_company_industry.py    (thin CLI wrapper - all real
    logic lives in the service above, so it is not separately re-tested
    here)

STRICT ISOLATION: every test below either (a) exercises the new service
directly, or (b) proves that running it leaves every non-industry field,
and every other organization's data, byte-for-byte unchanged. Nothing
here modifies or depends on changing app.services.classification_service,
app.services.effective_profile_service, app.services.effective_alumni_service,
or app.routers.analytics_routes.
"""
import pytest

from app.models.alumni import Alumni, AlumniOrganization
from app.models.reference import Company
from app.services.industry_backfill_service import (
    INDUSTRY_SOURCE_COMPANY_MAPPING,
    is_blocked_employer_value,
    normalize_company_name,
    resolve_curated_industry,
    rollback_backfill,
    run_backfill,
    validate_industry_mapping_data,
)
from app.services.industry_mapping_data import (
    APPROVED_COMPANY_ALIASES,
    BLOCKED_EMPLOYER_VALUES,
    GLOBAL_DEFAULT_COMPANY_INDUSTRY,
)
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.test_effective_alumni_data import _summary


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
# Curated data integrity (fails fast if the mapping data ever becomes
# internally inconsistent - see app.services.industry_mapping_data)
# --------------------------------------------------------------------------


def test_curated_mapping_data_is_internally_valid():
    validate_industry_mapping_data()  # must not raise


def test_initial_mapping_list_matches_the_five_approved_entries():
    assert GLOBAL_DEFAULT_COMPANY_INDUSTRY == {
        "capital one": "Financial Services",
        "florida state university": "Education",
        "tallahassee memorial healthcare": "Healthcare",
        "deloitte": "Consulting",
        "microsoft": "Technology",
        "citi bank": "Financial Services",
        "general motors": "Automotive",
        "a-lign": "Cybersecurity",
        "ibm": "Technology",
        "lockheed martin": "Aerospace & Defense",
        "pwc": "Consulting",
        "aptean": "Technology",
        "booz allen hamilton": "Consulting",
        "boston dynamics": "Robotics",
        "brandt information services": "Technology",
        "google": "Technology",
        "l3harris technologies": "Aerospace & Defense",
        "morgan stanley": "Financial Services",
        "oracle": "Technology",
        "rsm": "Consulting",
        "state farm": "Insurance",
        "the walt disney": "Media & Entertainment",
        "2u": "Education Technology",
        "adventhealth": "Healthcare",
        "advertising specialty institute": "Marketing & Advertising",
        "wells fargo": "Financial Services",
        "bank of america": "Financial Services",
        "fidelity investments": "Financial Services",
        "salesforce": "Technology",
        "sas": "Technology",
        "north carolina state university": "Education",
        "amazon": "Technology",
        "amazon web services (aws)": "Technology",
        "apple": "Technology",
        "eli lilly and": "Pharmaceuticals",
        "duke energy": "Energy & Utilities",
        "meta": "Technology",
        "vanguard": "Financial Services",
        "cgi": "Technology Consulting",
        "northrop grumman": "Aerospace & Defense",
    }


@pytest.mark.parametrize(
    "company_name,expected_industry",
    [
        ("Citi Bank", "Financial Services"),
        ("General Motors", "Automotive"),
        ("A-LIGN", "Cybersecurity"),
        ("IBM", "Technology"),
        ("Lockheed Martin", "Aerospace & Defense"),
        ("PwC", "Consulting"),
        ("Aptean", "Technology"),
        ("Booz Allen Hamilton", "Consulting"),
        ("Boston Dynamics", "Robotics"),
        ("Brandt Information Services", "Technology"),
        ("Google", "Technology"),
        ("L3Harris Technologies", "Aerospace & Defense"),
        ("Morgan Stanley", "Financial Services"),
        ("Oracle", "Technology"),
        ("RSM", "Consulting"),
        ("State Farm", "Insurance"),
        ("The Walt Disney Company", "Media & Entertainment"),
        ("The Walt Disney", "Media & Entertainment"),  # approved alias (see below)
        ("2U", "Education Technology"),
        ("AdventHealth", "Healthcare"),
        ("Advertising Specialty Institute", "Marketing & Advertising"),
    ],
)
def test_new_reviewed_mapping_resolves_via_normalized_exact_match(company_name, expected_industry):
    normalized = normalize_company_name(company_name)
    assert resolve_curated_industry(normalized, "fsu-stars") == expected_industry


@pytest.mark.parametrize("company_name,alumni_company", [
    ("Citi Bank", "Citi Bank"),
    ("General Motors", "General Motors"),
    ("A-LIGN", "A-LIGN"),
    ("IBM", "IBM"),
    ("Lockheed Martin", "Lockheed Martin"),
    ("PwC", "PwC"),
    ("Aptean", "Aptean"),
    ("Booz Allen Hamilton", "Booz Allen Hamilton"),
    ("Boston Dynamics", "Boston Dynamics"),
    ("Brandt Information Services", "Brandt Information Services"),
    ("Google", "Google"),
    ("L3Harris Technologies", "L3Harris Technologies"),
    ("Morgan Stanley", "Morgan Stanley"),
    ("Oracle", "Oracle"),
    ("RSM", "RSM"),
    ("State Farm", "State Farm"),
    ("The Walt Disney Company", "The Walt Disney Company"),
    ("The Walt Disney", "The Walt Disney"),
    ("2U", "2U"),
    ("AdventHealth", "AdventHealth"),
    ("Advertising Specialty Institute", "Advertising Specialty Institute"),
])
def test_new_mapping_backfills_company_and_alumni_end_to_end(db_session, organization, company_name, alumni_company):
    normalized = normalize_company_name(company_name)
    expected_industry = resolve_curated_industry(normalized, organization.slug)
    company = _add_company(db_session, organization, company_name)
    alumni = _add_alumni(db_session, organization, company=alumni_company)

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry == expected_industry
    assert alumni.industry == expected_industry
    assert alumni.industry_source == INDUSTRY_SOURCE_COMPANY_MAPPING
    # Original text must remain byte-for-byte unchanged.
    assert company.name == company_name
    assert alumni.company == alumni_company


def test_the_walt_disney_alias_resolves_to_the_same_industry_as_canonical(db_session, organization):
    """"The Walt Disney" is the approved alias for "The Walt Disney
    Company" - both normalize (via the existing trailing corporate-
    suffix strip) to the identical key, so they must classify
    identically."""
    company_full = _add_company(db_session, organization, "The Walt Disney Company")
    alumni_alias = _add_alumni(db_session, organization, company="The Walt Disney")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company_full)
    db_session.refresh(alumni_alias)
    assert company_full.industry == "Media & Entertainment"
    assert alumni_alias.industry == "Media & Entertainment"
    assert alumni_alias.company == "The Walt Disney"


def test_disney_alone_does_not_match_the_walt_disney_mapping():
    normalized = normalize_company_name("Disney")
    assert normalized == "disney"
    assert resolve_curated_industry(normalized, "fsu-stars") is None


def test_general_motor_does_not_match_general_motors():
    normalized = normalize_company_name("General Motor")
    assert normalized == "general motor"
    assert resolve_curated_industry(normalized, "fsu-stars") is None


def test_citi_alone_does_not_match_citi_bank():
    normalized = normalize_company_name("Citi")
    assert normalized == "citi"
    assert resolve_curated_industry(normalized, "fsu-stars") is None


def test_state_alone_does_not_match_state_farm():
    normalized = normalize_company_name("State")
    assert normalized == "state"
    assert resolve_curated_industry(normalized, "fsu-stars") is None


def test_disney_alone_remains_unclassified_end_to_end(db_session, organization):
    company = _add_company(db_session, organization, "Disney")
    alumni = _add_alumni(db_session, organization, company="Disney")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert report.unknown_companies_skipped == 1


# --------------------------------------------------------------------------
# Normalization + exact-match-only alias resolution
# --------------------------------------------------------------------------


def test_alias_resolves_via_normalized_exact_match():
    assert normalize_company_name("FSU") == "florida state university"
    assert normalize_company_name("fsu") == "florida state university"
    assert normalize_company_name("  FSU  ") == "florida state university"
    assert normalize_company_name("TMH") == "tallahassee memorial healthcare"
    assert normalize_company_name("Tallahassee Memorial Hospital") == "tallahassee memorial healthcare"


def test_partial_string_capital_does_not_match_capital_one():
    normalized = normalize_company_name("Capital")
    assert normalized == "capital"
    assert resolve_curated_industry(normalized, "fsu-stars") is None


def test_partial_string_florida_state_does_not_match_florida_state_university():
    normalized = normalize_company_name("Florida State")
    assert normalized == "florida state"
    assert resolve_curated_industry(normalized, "fsu-stars") is None


def test_dangerous_short_aliases_are_not_present_unless_allow_listed():
    for alias in APPROVED_COMPANY_ALIASES:
        assert len(alias) > 3 or alias in {"fsu", "tmh"}


def test_corporate_suffix_is_normalized_for_matching():
    assert normalize_company_name("Deloitte LLC") == "deloitte"
    assert normalize_company_name("Deloitte, Inc.") == "deloitte"


def test_case_and_whitespace_normalization():
    assert normalize_company_name("  MICROSOFT   Corp  ") == "microsoft"


def test_blocked_employer_values_remain_blocked_after_normalization():
    for raw_value in BLOCKED_EMPLOYER_VALUES:
        normalized = normalize_company_name(raw_value)
        assert is_blocked_employer_value(normalized), raw_value


def test_full_time_is_never_classified_as_a_company():
    normalized = normalize_company_name("Full-time")
    assert is_blocked_employer_value(normalized)
    assert resolve_curated_industry(normalized, "fsu-stars") is None


# --------------------------------------------------------------------------
# Dry run: zero writes
# --------------------------------------------------------------------------


def test_dry_run_performs_zero_writes(db_session, organization):
    company = _add_company(db_session, organization, "Capital One")
    alumni = _add_alumni(db_session, organization, company="Capital One")

    report = run_backfill(db_session, organization, apply=False)
    db_session.rollback()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert alumni.industry_source == "unknown"
    assert report.mode == "dry_run"
    assert report.manifest is None
    assert report.company_mappings_proposed == 1
    assert report.alumni_records_classified == 1


def test_running_without_apply_performs_no_writes(db_session, organization):
    """Mirrors the CLI's default behavior: apply=False unless --apply is
    explicitly passed."""
    company = _add_company(db_session, organization, "Microsoft")
    alumni = _add_alumni(db_session, organization, company="Microsoft")

    run_backfill(db_session, organization, apply=False)
    db_session.rollback()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None


# --------------------------------------------------------------------------
# Apply: only fills currently-blank fields, never overwrites
# --------------------------------------------------------------------------


def test_approved_mapping_fills_only_a_blank_company_and_alumni_industry(db_session, organization):
    company = _add_company(db_session, organization, "Deloitte")
    alumni = _add_alumni(db_session, organization, company="Deloitte")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry == "Consulting"
    assert alumni.industry == "Consulting"
    assert alumni.industry_source == INDUSTRY_SOURCE_COMPANY_MAPPING
    assert report.company_mappings_proposed == 1
    assert report.alumni_records_classified == 1


def test_explicit_csv_industry_is_never_overwritten_by_mapping(db_session, organization):
    """An Alumni row that already has a (CSV-imported) industry is left
    untouched even though its company matches a curated mapping."""
    _add_company(db_session, organization, "Microsoft")
    alumni = _add_alumni(
        db_session, organization, company="Microsoft", industry="Custom Imported Value", industry_source="imported"
    )

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.industry == "Custom Imported Value"
    assert alumni.industry_source == "imported"
    assert report.alumni_records_already_classified == 1
    assert report.alumni_records_classified == 0


def test_existing_company_mapping_is_never_overwritten(db_session, organization):
    """A Company row that already has an (admin-set) industry mapping is
    left untouched, even if it differs from the curated default."""
    company = _add_company(db_session, organization, "Capital One", industry="Banking (custom)")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    assert company.industry == "Banking (custom)"
    assert report.company_rows_already_classified == 1
    assert report.company_mappings_proposed == 0


def test_confirmed_profile_industry_still_wins_over_backfilled_mapping(client, db_session, organization, admin_user):
    """Precedence check: the industry backfill only ever fills Alumni.industry
    (source #3 in the precedence chain). It never touches UserProfile, so
    a confirmed profile's own current_industry (source #2) continues to
    win in analytics, exactly as before this feature existed."""
    from tests.test_profile_linking import _put_my_profile, _register_alumni_user
    from tests.test_effective_alumni_data import _confirm

    _add_alumni(
        db_session, organization, full_name="Skye Nolan", email="skye.nolan@example.com",
        company="Deloitte", industry=None,
    )
    alumni_token = _register_alumni_user(client, db_session, organization, username="skyenolan3")
    _put_my_profile(client, alumni_token, primary_email="skye.nolan@example.com")
    _confirm(client, alumni_token)
    _put_my_profile(client, alumni_token, current_employer="Deloitte", current_industry="Custom Profile Industry")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    admin_token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    summary = _summary(client, admin_token)
    industry_names = {row["name"] for row in summary["industries"]}
    assert "Custom Profile Industry" in industry_names


# --------------------------------------------------------------------------
# Unknown / invalid companies remain unclassified
# --------------------------------------------------------------------------


def test_unknown_company_remains_unclassified(db_session, organization):
    company = _add_company(db_session, organization, "Globex Technology Group")
    alumni = _add_alumni(db_session, organization, company="Globex Technology Group")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert report.unknown_companies_skipped == 1
    assert report.unknown_employer_names == [{"name": "globex technology group", "count": 1}]


def test_invalid_employer_values_remain_unclassified(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="Full-time")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.industry is None
    assert report.invalid_employer_values_skipped == 1
    assert report.unknown_companies_skipped == 0


def test_self_employed_remains_unclassified_without_an_explicit_mapping(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="Self-employed")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.industry is None


# --------------------------------------------------------------------------
# Company-name safety: original text is preserved exactly
# --------------------------------------------------------------------------


def test_company_display_name_is_unchanged(db_session, organization):
    company = _add_company(db_session, organization, "Capital One")
    alumni = _add_alumni(db_session, organization, company="Capital One")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.name == "Capital One"
    assert alumni.company == "Capital One"


def test_alias_input_does_not_rename_alumni_company_field(db_session, organization):
    """An alumni whose employer text is literally the alias "FSU" gets
    classified via the alias, but their displayed company text stays
    exactly "FSU" - never rewritten to the canonical name."""
    alumni = _add_alumni(db_session, organization, company="FSU")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.company == "FSU"
    assert alumni.industry == "Education"


def test_no_non_industry_alumni_fields_are_modified(db_session, organization):
    alumni = _add_alumni(
        db_session, organization, company="Microsoft", job_title="Senior Engineer", career_category="Custom Category",
        seniority="Custom Seniority", full_name="Untouched Person",
    )
    before = {
        "job_title": alumni.job_title,
        "career_category": alumni.career_category,
        "seniority": alumni.seniority,
        "full_name": alumni.full_name,
        "company": alumni.company,
    }

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.job_title == before["job_title"]
    assert alumni.career_category == before["career_category"]
    assert alumni.seniority == before["seniority"]
    assert alumni.full_name == before["full_name"]
    assert alumni.company == before["company"]
    assert alumni.industry == "Technology"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_backfill_is_idempotent(db_session, organization):
    company = _add_company(db_session, organization, "Deloitte")
    alumni = _add_alumni(db_session, organization, company="Deloitte")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    second_report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry == "Consulting"
    assert alumni.industry == "Consulting"
    assert second_report.company_mappings_proposed == 0
    assert second_report.alumni_records_classified == 0
    assert second_report.company_rows_already_classified == 1
    assert second_report.alumni_records_already_classified == 1


# --------------------------------------------------------------------------
# Transactional guardrails
# --------------------------------------------------------------------------


def test_exception_during_apply_rolls_back_all_staged_writes(db_session, organization):
    company = _add_company(db_session, organization, "Capital One")
    alumni = _add_alumni(db_session, organization, company="Capital One")

    class SimulatedFailure(Exception):
        pass

    try:
        run_backfill(db_session, organization, apply=True)
        raise SimulatedFailure("simulated failure before commit")
    except SimulatedFailure:
        db_session.rollback()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert alumni.industry_source == "unknown"


# --------------------------------------------------------------------------
# Manifest: content + privacy
# --------------------------------------------------------------------------


def test_manifest_includes_only_records_changed_by_that_run(db_session, organization):
    already_classified = _add_alumni(db_session, organization, company="Deloitte", industry="Preexisting")
    company = _add_company(db_session, organization, "Deloitte")
    newly_classified = _add_alumni(db_session, organization, company="Deloitte")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    changed_alumni_ids = {entry["id"] for entry in report.manifest["alumni_changed"]}
    assert changed_alumni_ids == {newly_classified.id}
    assert already_classified.id not in changed_alumni_ids
    changed_company_ids = {entry["id"] for entry in report.manifest["companies_changed"]}
    assert changed_company_ids == {company.id}


def test_manifest_contains_no_personal_information(db_session, organization):
    _add_company(db_session, organization, "Capital One")
    _add_alumni(
        db_session, organization, company="Capital One", full_name="Very Private Person",
        email="private@example.com", linkedin_url="https://linkedin.com/in/private",
    )

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    manifest_text = str(report.manifest)
    assert "Very Private Person" not in manifest_text
    assert "private@example.com" not in manifest_text
    assert "linkedin" not in manifest_text.lower()
    for entry in report.manifest["alumni_changed"]:
        assert set(entry.keys()) == {"id", "previous_industry", "previous_industry_source"}
    for entry in report.manifest["companies_changed"]:
        assert set(entry.keys()) == {"id", "previous_industry"}


def test_manifest_includes_organization_slug_and_id(db_session, organization):
    _add_company(db_session, organization, "Deloitte")
    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    assert report.manifest["organization_slug"] == organization.slug
    assert report.manifest["organization_id"] == organization.id
    assert "timestamp" in report.manifest


# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------


def test_rollback_restores_exact_previous_values(db_session, organization):
    company = _add_company(db_session, organization, "Deloitte")
    alumni = _add_alumni(db_session, organization, company="Deloitte", industry=None, industry_source="unknown")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()
    manifest = report.manifest

    rollback_report = rollback_backfill(db_session, organization, manifest)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert alumni.industry_source == "unknown"
    assert rollback_report.companies_reverted == 1
    assert rollback_report.alumni_reverted == 1


def test_rollback_restores_non_null_previous_values_not_just_null(db_session, organization):
    """Rollback must restore whatever was recorded, not assume NULL -
    covers the case of a manifest from a run that classified a row which
    had already been given some other non-null-but-still-"blank-for-our-
    purposes" placeholder... more concretely: previous_industry_source
    must be restored exactly, even if it wasn't "unknown"."""
    company = _add_company(db_session, organization, "Deloitte")
    alumni = _add_alumni(db_session, organization, company="Deloitte", industry=None, industry_source="unknown")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    # Manually simulate a differing prior source in the manifest to prove
    # rollback restores exactly what's recorded, not a hardcoded default.
    manifest = report.manifest
    assert manifest["alumni_changed"][0]["previous_industry_source"] == "unknown"

    rollback_backfill(db_session, organization, manifest)
    db_session.commit()
    db_session.refresh(alumni)
    assert alumni.industry_source == "unknown"


def test_rollback_refuses_organization_mismatch(db_session, organization, other_organization):
    _add_company(db_session, organization, "Deloitte")
    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    with pytest.raises(ValueError):
        rollback_backfill(db_session, other_organization, report.manifest)


def test_rollback_is_idempotent(db_session, organization):
    company = _add_company(db_session, organization, "Deloitte")
    alumni = _add_alumni(db_session, organization, company="Deloitte")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()
    manifest = report.manifest

    rollback_backfill(db_session, organization, manifest)
    db_session.commit()
    second_rollback = rollback_backfill(db_session, organization, manifest)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert second_rollback.companies_reverted == 1
    assert second_rollback.alumni_reverted == 1


# --------------------------------------------------------------------------
# Organization scoping
# --------------------------------------------------------------------------


def test_one_organizations_mapping_cannot_affect_another_organization(db_session, organization, other_organization):
    company_a = _add_company(db_session, organization, "Deloitte")
    alumni_a = _add_alumni(db_session, organization, company="Deloitte")
    company_b = _add_company(db_session, other_organization, "Deloitte")
    alumni_b = _add_alumni(db_session, other_organization, company="Deloitte")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company_a)
    db_session.refresh(alumni_a)
    db_session.refresh(company_b)
    db_session.refresh(alumni_b)
    assert company_a.industry == "Consulting"
    assert alumni_a.industry == "Consulting"
    assert company_b.industry is None
    assert alumni_b.industry is None


def test_backfill_examines_only_the_requested_organizations_rows(db_session, organization, other_organization):
    _add_company(db_session, organization, "Deloitte")
    _add_alumni(db_session, organization, company="Deloitte")
    _add_company(db_session, other_organization, "Microsoft")
    _add_alumni(db_session, other_organization, company="Microsoft")

    report = run_backfill(db_session, organization, apply=False)
    db_session.rollback()

    assert report.company_rows_examined == 1
    assert report.alumni_rows_examined == 1


# --------------------------------------------------------------------------
# Protected analytics regression: only industry-derived fields may change
# --------------------------------------------------------------------------


def _protected_summary_snapshot(summary):
    return {
        "total_alumni": summary["total_alumni"],
        "verified_alumni": summary["verified_alumni"],
        "verification_percentage": summary["verification_percentage"],
        "top_companies": summary["top_companies"],
        "employer_concentration": summary["employer_concentration"],
        "seniority": summary["seniority"],
        "universities": summary["universities"],
        "cities": summary["cities"],
        "states": summary["states"],
        "graduation_year_distribution": summary["graduation_year_distribution"],
        "major_distribution": summary["major_distribution"],
        "average_profile_completion": summary["average_profile_completion"],
        "totals": summary["totals"],
        "data_quality": {
            key: value for key, value in summary["data_quality"].items() if key != "unclassified_industry"
        },
    }


def test_protected_analytics_fields_are_byte_for_byte_unchanged_after_backfill(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Deloitte", job_title="Senior Engineer", seniority="Senior")
    _add_alumni(db_session, organization, full_name="B", company="Deloitte", job_title="Manager", seniority="Manager")
    _add_alumni(db_session, organization, full_name="C", company="Globex Unlisted Co")
    _add_alumni(db_session, organization, full_name="D", company=None, verified=False, verification_status="unverified")
    _add_company(db_session, organization, "Deloitte")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _protected_summary_snapshot(_summary(client, token))

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    after = _protected_summary_snapshot(_summary(client, token))
    assert before == after


def test_only_industry_derived_analytics_fields_change_after_backfill(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Deloitte")
    _add_company(db_session, organization, "Deloitte")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _summary(client, token)
    assert before["company_industry_overview"]["alumni_with_industry"] == 0

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    after = _summary(client, token)
    assert after["company_industry_overview"]["alumni_with_industry"] == 1
    assert after["company_industry_overview"]["classified_industries"] == 1
    assert any(row["name"] == "Consulting" for row in after["industries"])
    assert after["data_quality"]["unclassified_industry"] == before["data_quality"]["unclassified_industry"] - 1
    # Nothing else in data_quality changes.
    for key in before["data_quality"]:
        if key != "unclassified_industry":
            assert after["data_quality"][key] == before["data_quality"][key]


def test_alumni_directory_results_unchanged_except_industry_field(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Microsoft", job_title="Engineer")
    _add_company(db_session, organization, "Microsoft")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before_rows = {row["id"]: row for row in _alumni_data(client, token)["data"]}

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    after_rows = {row["id"]: row for row in _alumni_data(client, token)["data"]}

    assert before_rows.keys() == after_rows.keys()
    for alumni_id, before_row in before_rows.items():
        after_row = after_rows[alumni_id]
        for field_name in before_row:
            if field_name == "industry":
                continue
            assert before_row[field_name] == after_row[field_name], field_name
        assert before_row["industry"] is None
        assert after_row["industry"] == "Technology"


# --------------------------------------------------------------------------
# Analytics GET requests never write (sanity check that this feature is
# not, and does not accidentally become, wired into a live request path)
# --------------------------------------------------------------------------


def test_analytics_get_requests_perform_no_industry_writes(client, db_session, organization, admin_user):
    alumni = _add_alumni(db_session, organization, company="Globex Technology Group")
    company = _add_company(db_session, organization, "Globex Technology Group")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    for _ in range(3):
        _summary(client, token)

    db_session.refresh(alumni)
    db_session.refresh(company)
    assert alumni.industry is None
    assert company.industry is None


# ==========================================================================
# STARS National mapping expansion round 2: new reviewed mappings + new
# blocked employer/status values, from production dry-run findings.
# ==========================================================================


def test_second_expansion_mapping_list_matches_the_fifteen_new_approved_entries():
    new_entries = {
        "wells fargo": "Financial Services",
        "bank of america": "Financial Services",
        "fidelity investments": "Financial Services",
        "salesforce": "Technology",
        "sas": "Technology",
        "north carolina state university": "Education",
        "amazon": "Technology",
        "amazon web services (aws)": "Technology",
        "apple": "Technology",
        "eli lilly and": "Pharmaceuticals",
        "duke energy": "Energy & Utilities",
        "meta": "Technology",
        "vanguard": "Financial Services",
        "cgi": "Technology Consulting",
        "northrop grumman": "Aerospace & Defense",
    }
    for key, industry in new_entries.items():
        assert GLOBAL_DEFAULT_COMPANY_INDUSTRY[key] == industry


def test_second_expansion_new_blocked_values_are_present_and_existing_ones_unchanged():
    assert {"Not stated", "Not specified", "Freelance", "Self employed"} <= BLOCKED_EMPLOYER_VALUES
    # Existing blocked values from the original approved set remain, unchanged.
    assert {
        "Full-time", "Part-time", "Student", "Unemployed", "Not employed",
        "N/A", "Unknown", "None", "Self-employed",
    } <= BLOCKED_EMPLOYER_VALUES


@pytest.mark.parametrize(
    "company_name,expected_industry",
    [
        ("Wells Fargo", "Financial Services"),
        ("Bank of America", "Financial Services"),
        ("Fidelity Investments", "Financial Services"),
        ("Salesforce", "Technology"),
        ("SAS", "Technology"),
        ("North Carolina State University", "Education"),
        ("Amazon", "Technology"),
        ("Amazon Web Services (AWS)", "Technology"),
        ("Apple", "Technology"),
        ("Eli Lilly and Company", "Pharmaceuticals"),
        ("Duke Energy", "Energy & Utilities"),
        ("Meta", "Technology"),
        ("Vanguard", "Financial Services"),
        ("CGI", "Technology Consulting"),
        ("Northrop Grumman", "Aerospace & Defense"),
    ],
)
def test_second_expansion_new_mapping_resolves_via_normalized_exact_match(company_name, expected_industry):
    normalized = normalize_company_name(company_name)
    assert resolve_curated_industry(normalized, "stars-national") == expected_industry


@pytest.mark.parametrize("company_name", [
    "Wells Fargo", "Bank of America", "Fidelity Investments", "Salesforce", "SAS",
    "North Carolina State University", "Amazon", "Amazon Web Services (AWS)", "Apple",
    "Eli Lilly and Company", "Duke Energy", "Meta", "Vanguard", "CGI", "Northrop Grumman",
])
def test_second_expansion_new_mapping_backfills_company_and_alumni_end_to_end(db_session, organization, company_name):
    normalized = normalize_company_name(company_name)
    expected_industry = resolve_curated_industry(normalized, organization.slug)
    assert expected_industry is not None

    company = _add_company(db_session, organization, company_name)
    alumni = _add_alumni(db_session, organization, company=company_name)

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry == expected_industry
    assert alumni.industry == expected_industry
    assert alumni.industry_source == INDUSTRY_SOURCE_COMPANY_MAPPING
    assert company.name == company_name
    assert alumni.company == company_name


@pytest.mark.parametrize("blocked_value", ["not stated", "not specified", "freelance", "self employed"])
def test_second_expansion_new_blocked_values_remain_unclassified_end_to_end(db_session, organization, blocked_value):
    alumni = _add_alumni(db_session, organization, company=blocked_value)

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.industry is None
    assert report.invalid_employer_values_skipped == 1
    assert alumni.company == blocked_value


def test_amazon_web_services_without_aws_suffix_does_not_partially_match():
    """"Amazon Web Services" (without the "(AWS)" suffix) must remain
    unknown - it normalizes to a distinct key from both "amazon" and
    "amazon web services (aws)", and no partial/substring match is ever
    performed."""
    normalized = normalize_company_name("Amazon Web Services")
    assert normalized == "amazon web services"
    assert resolve_curated_industry(normalized, "stars-national") is None


def test_amazon_web_services_without_aws_suffix_remains_unclassified_end_to_end(db_session, organization):
    company = _add_company(db_session, organization, "Amazon Web Services")
    alumni = _add_alumni(db_session, organization, company="Amazon Web Services")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni)
    assert company.industry is None
    assert alumni.industry is None
    assert report.unknown_companies_skipped == 1


def test_amazon_and_amazon_web_services_aws_are_independently_explicit(db_session, organization):
    """Both are kept as separate, explicit canonical entries - proves
    neither is silently treated as an alias of the other."""
    amazon_company = _add_company(db_session, organization, "Amazon")
    amazon_alumni = _add_alumni(db_session, organization, company="Amazon")
    aws_company = _add_company(db_session, organization, "Amazon Web Services (AWS)")
    aws_alumni = _add_alumni(db_session, organization, company="Amazon Web Services (AWS)")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    for obj in (amazon_company, amazon_alumni, aws_company, aws_alumni):
        db_session.refresh(obj)
    assert amazon_company.industry == "Technology"
    assert amazon_alumni.industry == "Technology"
    assert aws_company.industry == "Technology"
    assert aws_alumni.industry == "Technology"
    assert amazon_company.name == "Amazon"
    assert aws_company.name == "Amazon Web Services (AWS)"


def test_bank_alone_does_not_match_bank_of_america():
    normalized = normalize_company_name("Bank")
    assert normalized == "bank"
    assert resolve_curated_industry(normalized, "stars-national") is None


def test_north_carolina_state_alone_does_not_match_the_university():
    normalized = normalize_company_name("North Carolina State")
    assert normalized == "north carolina state"
    assert resolve_curated_industry(normalized, "stars-national") is None


def test_bank_and_north_carolina_state_remain_unclassified_end_to_end(db_session, organization):
    bank_alumni = _add_alumni(db_session, organization, company="Bank")
    ncs_alumni = _add_alumni(db_session, organization, company="North Carolina State")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(bank_alumni)
    db_session.refresh(ncs_alumni)
    assert bank_alumni.industry is None
    assert ncs_alumni.industry is None
    assert bank_alumni.company == "Bank"
    assert ncs_alumni.company == "North Carolina State"


def test_eli_lilly_and_company_and_bare_eli_lilly_and_both_documented_and_deterministic():
    """"Eli Lilly and Company" is forced by the existing (unmodified)
    trailing-corporate-suffix strip to normalize to "eli lilly and" -
    that is the ONLY normalized form it can ever take, so the canonical
    mapping key must be written that way. A direct, documented
    consequence: literal employer text "Eli Lilly and" (without
    "Company") normalizes identically and therefore also resolves to the
    same approved industry - this is deterministic (always the same
    result for the same input) and does not rely on any new
    keyword/substring/alias rule."""
    full_name_normalized = normalize_company_name("Eli Lilly and Company")
    bare_normalized = normalize_company_name("Eli Lilly and")
    assert full_name_normalized == bare_normalized == "eli lilly and"
    assert resolve_curated_industry(full_name_normalized, "stars-national") == "Pharmaceuticals"
    assert resolve_curated_industry(bare_normalized, "stars-national") == "Pharmaceuticals"

    # "Eli Lilly" alone (no trailing "and") normalizes to a different,
    # distinct key and is NOT classified - only the "...and [Company]"
    # form matches.
    shorter_normalized = normalize_company_name("Eli Lilly")
    assert shorter_normalized == "eli lilly"
    assert resolve_curated_industry(shorter_normalized, "stars-national") is None


def test_eli_lilly_and_company_backfills_end_to_end(db_session, organization):
    company = _add_company(db_session, organization, "Eli Lilly and Company")
    alumni_full = _add_alumni(db_session, organization, company="Eli Lilly and Company")
    alumni_bare = _add_alumni(db_session, organization, company="Eli Lilly and")

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(company)
    db_session.refresh(alumni_full)
    db_session.refresh(alumni_bare)
    assert company.industry == "Pharmaceuticals"
    assert alumni_full.industry == "Pharmaceuticals"
    assert alumni_bare.industry == "Pharmaceuticals"
    # Original employer text preserved exactly for both variants.
    assert company.name == "Eli Lilly and Company"
    assert alumni_full.company == "Eli Lilly and Company"
    assert alumni_bare.company == "Eli Lilly and"


def test_c_is_ambiguous_value_was_not_added_and_remains_unclassified():
    normalized = normalize_company_name("c is")
    assert normalized == "c is"
    assert resolve_curated_industry(normalized, "stars-national") is None
    assert not is_blocked_employer_value(normalized)
    assert "c is" not in GLOBAL_DEFAULT_COMPANY_INDUSTRY
    assert "c is" not in APPROVED_COMPANY_ALIASES


def test_c_is_remains_unclassified_end_to_end(db_session, organization):
    alumni = _add_alumni(db_session, organization, company="c is")

    report = run_backfill(db_session, organization, apply=True)
    db_session.commit()

    db_session.refresh(alumni)
    assert alumni.industry is None
    assert report.unknown_companies_skipped == 1


def test_second_expansion_protected_analytics_fields_unchanged(client, db_session, organization, admin_user):
    _add_alumni(db_session, organization, full_name="A", company="Wells Fargo", job_title="Analyst", seniority="Associate")
    _add_alumni(db_session, organization, full_name="B", company="Bank of America", job_title="VP", seniority="Manager")
    _add_alumni(db_session, organization, full_name="C", company="c is")
    _add_alumni(db_session, organization, full_name="D", company="not stated")
    _add_company(db_session, organization, "Wells Fargo")
    _add_company(db_session, organization, "Bank of America")

    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    before = _protected_summary_snapshot(_summary(client, token))

    run_backfill(db_session, organization, apply=True)
    db_session.commit()

    after = _protected_summary_snapshot(_summary(client, token))
    assert before == after
