"""
Tests for the combined Companies + Industries analytics page
(GET /analytics/summary): top_companies (now up to 15), the new
company_industry_overview / employer_concentration /
top_companies_by_industry / seniority_by_industry fields.

Additive-only: nothing here touches or depends on changing
authentication, the CSV import pipeline, profile-linking rules, or the
shared content-synchronization system - it only proves the analytics
computation is correct and still entirely SQL-aggregated.
"""
from app.models.alumni import Alumni, AlumniOrganization
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.test_effective_alumni_data import _confirm, _names, _summary
from tests.test_profile_linking import _put_my_profile, _register_alumni_user


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


def _admin_token(client):
    return login(client, ADMIN_USERNAME, ADMIN_PASSWORD)


# --------------------------------------------------------------------------
# 1-2. top_companies: up to 15, ordered by count descending
# --------------------------------------------------------------------------


def test_top_companies_returns_up_to_15_records(client, organization, admin_user, db_session):
    for i in range(20):
        _add_alumni(db_session, organization, full_name=f"Person {i}", company=f"Company {i}")

    token = _admin_token(client)
    summary = _summary(client, token)
    assert len(summary["top_companies"]) == 15


def test_top_companies_are_ordered_by_count_descending(client, organization, admin_user, db_session):
    for i in range(3):
        _add_alumni(db_session, organization, full_name=f"Big {i}", company="Big Corp")
    for i in range(2):
        _add_alumni(db_session, organization, full_name=f"Mid {i}", company="Mid Corp")
    _add_alumni(db_session, organization, full_name="Small", company="Small Corp")

    token = _admin_token(client)
    summary = _summary(client, token)
    counts_by_name = {row["name"]: row["count"] for row in summary["top_companies"]}
    ordered_names = [row["name"] for row in summary["top_companies"]]
    assert ordered_names.index("Big Corp") < ordered_names.index("Mid Corp") < ordered_names.index("Small Corp")
    assert counts_by_name == {"Big Corp": 3, "Mid Corp": 2, "Small Corp": 1}


def test_top_companies_never_returns_more_than_15_even_with_many_distinct_companies(
    client, organization, admin_user, db_session
):
    for i in range(40):
        _add_alumni(db_session, organization, full_name=f"Person {i}", company=f"Unique Co {i}")

    token = _admin_token(client)
    summary = _summary(client, token)
    assert len(summary["top_companies"]) <= 15


# --------------------------------------------------------------------------
# 3. Blank company values excluded
# --------------------------------------------------------------------------


def test_blank_and_null_company_values_are_excluded_from_top_companies(client, organization, admin_user, db_session):
    _add_alumni(db_session, organization, full_name="No Company", company=None)
    _add_alumni(db_session, organization, full_name="Has Company", company="Real Co")

    token = _admin_token(client)
    summary = _summary(client, token)
    names = _names(summary["top_companies"])
    assert "Real Co" in names
    assert None not in names
    assert "" not in names


# --------------------------------------------------------------------------
# 4. Active alumni only
# --------------------------------------------------------------------------


def test_archived_alumni_are_excluded_from_top_companies(client, organization, admin_user, db_session):
    _add_alumni(db_session, organization, full_name="Active Person", company="Active Co")
    _add_alumni(db_session, organization, full_name="Archived Person", company="Archived Co", is_active=False)

    token = _admin_token(client)
    summary = _summary(client, token)
    names = _names(summary["top_companies"])
    assert "Active Co" in names
    assert "Archived Co" not in names


# --------------------------------------------------------------------------
# 5. Effective linked-profile company values included
# --------------------------------------------------------------------------


def test_confirmed_linked_profile_company_appears_in_top_companies(client, organization, admin_user, db_session):
    admin_token = _admin_token(client)
    _add_alumni(db_session, organization, full_name="Skye Nolan", email="skye.nolan@example.com", company="Old Co")

    token = _register_alumni_user(client, db_session, organization, username="skyenolan2")
    _put_my_profile(client, token, primary_email="skye.nolan@example.com")
    _confirm(client, token)
    _put_my_profile(client, token, current_employer="New Effective Co")

    summary = _summary(client, admin_token)
    names = _names(summary["top_companies"])
    assert "New Effective Co" in names
    assert "Old Co" not in names


# --------------------------------------------------------------------------
# 6. Employer concentration
# --------------------------------------------------------------------------


def test_employer_concentration_is_calculated_correctly(client, organization, admin_user, db_session):
    # 10 alumni total with a known company: 5 at "Mega Corp" (top employer),
    # 1 each at 5 other distinct companies => top_5_company_share should be
    # (5 + 1 + 1 + 1 + 1) / 10 * 100 = 90.0, and top_15_company_share is the
    # same 100% since there are fewer than 15 distinct companies total.
    for i in range(5):
        _add_alumni(db_session, organization, full_name=f"Mega {i}", company="Mega Corp")
    for i in range(5):
        _add_alumni(db_session, organization, full_name=f"Solo {i}", company=f"Solo Co {i}")

    token = _admin_token(client)
    summary = _summary(client, token)
    concentration = summary["employer_concentration"]
    assert concentration["top_5_company_share"] == 90.0
    assert concentration["top_15_company_share"] == 100.0


def test_employer_concentration_denominator_is_alumni_with_known_company_only(
    client, organization, admin_user, db_session
):
    # 4 alumni at the same company, plus 6 with no company at all. The
    # denominator must be 4 (alumni WITH a known company), not 10, so
    # top_5_company_share is 100%, not 40%.
    for i in range(4):
        _add_alumni(db_session, organization, full_name=f"Known {i}", company="Known Co")
    for i in range(6):
        _add_alumni(db_session, organization, full_name=f"Unknown {i}", company=None)

    token = _admin_token(client)
    summary = _summary(client, token)
    assert summary["company_industry_overview"]["alumni_with_company"] == 4
    assert summary["employer_concentration"]["top_5_company_share"] == 100.0


def test_employer_concentration_is_zero_when_no_alumni_have_a_company(client, organization, admin_user, db_session):
    _add_alumni(db_session, organization, full_name="No Company", company=None)

    token = _admin_token(client)
    summary = _summary(client, token)
    assert summary["employer_concentration"]["top_5_company_share"] == 0.0
    assert summary["employer_concentration"]["top_15_company_share"] == 0.0


# --------------------------------------------------------------------------
# 1 (overview). Company/industry overview
# --------------------------------------------------------------------------


def test_company_industry_overview_counts_are_correct(client, organization, admin_user, db_session):
    _add_alumni(db_session, organization, full_name="A", company="Acme", industry="Technology")
    _add_alumni(db_session, organization, full_name="B", company="Acme", industry="Technology")
    _add_alumni(db_session, organization, full_name="C", company="Globex", industry=None)
    _add_alumni(db_session, organization, full_name="D", company=None, industry=None)

    token = _admin_token(client)
    summary = _summary(client, token)
    overview = summary["company_industry_overview"]
    assert overview["unique_companies"] == 2
    assert overview["classified_industries"] == 1
    assert overview["alumni_with_company"] == 3
    assert overview["alumni_with_industry"] == 2
    assert overview["company_coverage_percentage"] == 75.0
    assert overview["industry_coverage_percentage"] == 50.0


# --------------------------------------------------------------------------
# 7. Unknown industries are never guessed
# --------------------------------------------------------------------------


def test_unknown_industry_is_never_guessed_from_company_name(client, organization, admin_user, db_session):
    # "Globex Technology Group" strongly suggests "Technology" by name
    # alone, but with no imported Industry value and no verified
    # Company.industry mapping, it must remain unclassified.
    _add_alumni(
        db_session, organization, full_name="No Guess", company="Globex Technology Group", industry=None,
        job_title="Analyst",
    )

    token = _admin_token(client)
    summary = _summary(client, token)
    assert summary["company_industry_overview"]["alumni_with_industry"] == 0
    assert summary["company_industry_overview"]["classified_industries"] == 0
    industry_names = _names(summary["industries"])
    assert "Technology" not in industry_names


# --------------------------------------------------------------------------
# 8. Top companies by industry use verified/imported industry values
# --------------------------------------------------------------------------


def test_top_companies_by_industry_groups_by_verified_industry(client, organization, admin_user, db_session):
    _add_alumni(db_session, organization, full_name="A", company="Acme", industry="Technology")
    _add_alumni(db_session, organization, full_name="B", company="Acme", industry="Technology")
    _add_alumni(db_session, organization, full_name="C", company="Beta Bank", industry="Finance")
    # No industry at all - must never appear in any group.
    _add_alumni(db_session, organization, full_name="D", company="Mystery Co", industry=None)

    token = _admin_token(client)
    summary = _summary(client, token)
    groups = {g["industry"]: g["companies"] for g in summary["top_companies_by_industry"]}
    assert "Technology" in groups
    assert {c["name"]: c["count"] for c in groups["Technology"]} == {"Acme": 2}
    assert "Finance" in groups
    assert {c["name"]: c["count"] for c in groups["Finance"]} == {"Beta Bank": 1}
    for group in summary["top_companies_by_industry"]:
        assert all(c["name"] != "Mystery Co" for c in group["companies"])


def test_top_companies_by_industry_orders_largest_industries_first(client, organization, admin_user, db_session):
    for i in range(3):
        _add_alumni(db_session, organization, full_name=f"Tech {i}", company="Acme", industry="Technology")
    _add_alumni(db_session, organization, full_name="Finance Person", company="Beta Bank", industry="Finance")

    token = _admin_token(client)
    summary = _summary(client, token)
    industries_in_order = [g["industry"] for g in summary["top_companies_by_industry"]]
    assert industries_in_order.index("Technology") < industries_in_order.index("Finance")


def test_top_companies_by_industry_limits_companies_per_industry(client, organization, admin_user, db_session):
    for i in range(8):
        _add_alumni(db_session, organization, full_name=f"Person {i}", company=f"Company {i}", industry="Technology")

    token = _admin_token(client)
    summary = _summary(client, token)
    tech_group = next(g for g in summary["top_companies_by_industry"] if g["industry"] == "Technology")
    assert len(tech_group["companies"]) <= 5


# --------------------------------------------------------------------------
# 9. Seniority by industry uses deterministic seniority values
# --------------------------------------------------------------------------


def test_seniority_by_industry_uses_deterministic_title_rules(client, organization, admin_user, db_session):
    _add_alumni(
        db_session, organization, full_name="A", company="Acme", industry="Technology",
        job_title="Senior Engineer", seniority="Senior", seniority_source="derived:title_rules",
    )
    _add_alumni(
        db_session, organization, full_name="B", company="Acme", industry="Technology",
        job_title="Director of Engineering", seniority="Director", seniority_source="derived:title_rules",
    )
    # No seniority at all - must never appear.
    _add_alumni(db_session, organization, full_name="C", company="Acme", industry="Technology", job_title="Analyst")

    token = _admin_token(client)
    summary = _summary(client, token)
    rows = {
        (row["industry"], row["seniority"]): row["count"] for row in summary["seniority_by_industry"]
    }
    assert rows[("Technology", "Senior")] == 1
    assert rows[("Technology", "Director")] == 1
    assert all(seniority is not None for (_industry, seniority) in rows)


def test_seniority_by_industry_never_appears_without_an_industry(client, organization, admin_user, db_session):
    _add_alumni(
        db_session, organization, full_name="No Industry", company=None, industry=None,
        job_title="Senior Engineer", seniority="Senior", seniority_source="derived:title_rules",
    )

    token = _admin_token(client)
    summary = _summary(client, token)
    assert summary["seniority_by_industry"] == []


# --------------------------------------------------------------------------
# 10. Regression: existing summary fields untouched
# --------------------------------------------------------------------------


def test_existing_analytics_fields_are_unaffected(client, organization, admin_user, db_session):
    _add_alumni(db_session, organization, full_name="A B", verified=True)
    _add_alumni(db_session, organization, full_name="C D", verified=False, verification_status="unverified")

    token = _admin_token(client)
    summary = _summary(client, token)
    assert summary["total_alumni"] == 2
    assert summary["verified_alumni"] == 1
    assert summary["verification_percentage"] == 50.0
