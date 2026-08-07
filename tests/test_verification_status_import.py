"""
Focused regression tests for deriving `Alumni.verified` from a text-only
"Verification Status" CSV column (see `_is_verified_status_text` in
app.services.csv_import_service), fixing the bug where CSV-imported
verification status never affected `GET /analytics/summary`.

Only `Alumni.verified` derivation and `Alumni.verification_status`
preservation are exercised here - the explicit `Verified`/`Is Verified`
boolean-column path (`_parse_bool`), verification_date handling,
organization scoping, and every other CSV field/analytics computation
are already covered by tests/test_data_pipeline_v2.py,
tests/test_replace_mode.py, and tests/test_analytics.py; this file only
adds what wasn't already covered.
"""
from app.models.alumni import Alumni, AlumniOrganization
from tests.test_import import _login, _upload

CANONICAL_HEADER = (
    "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,"
    "Notes,Verification Status,Verification Date,Education"
)


def _row(last, first, email, status, linkedin=None):
    linkedin = linkedin or f"https://linkedin.com/in/{first.lower()}{last.lower()}"
    return f"{last},{first},{email},{linkedin},Acme Inc,Engineer,Tallahassee,FL,,{status},2026-01-01,Florida State University"


def _upload_csv(client, token, rows, organization=None):
    csv_text = "\n".join([CANONICAL_HEADER, *rows]) + "\n"
    response = _upload(client, token, csv_text, organization=organization)
    assert response.status_code == 200, response.text
    return response.json()


def _alumni_by_email(db_session, email):
    return db_session.query(Alumni).filter(Alumni.email == email).one()


# --------------------------------------------------------------------------
# Positive matches: exact "verified", case/whitespace-insensitive only
# --------------------------------------------------------------------------


def test_verified_case_and_whitespace_variants_are_true(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    rows = [
        _row("A", "First", "a@example.com", "Verified"),
        _row("B", "Second", "b@example.com", "verified"),
        _row("C", "Third", "c@example.com", "VERIFIED"),
        _row("D", "Fourth", "d@example.com", " Verified "),
    ]
    _upload_csv(client, token, rows)

    for email in ("a@example.com", "b@example.com", "c@example.com", "d@example.com"):
        alumni = _alumni_by_email(db_session, email)
        assert alumni.verified is True, f"{email} expected verified=True"


# --------------------------------------------------------------------------
# Negative matches: no substring/fuzzy matching, no unrelated-word matching
# --------------------------------------------------------------------------


def test_non_verified_status_values_remain_false(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    cases = [
        ("e@example.com", "Unverified"),
        ("f@example.com", "Pending"),
        ("g@example.com", "Updated"),
        ("h@example.com", "Yes"),
        ("i@example.com", "True"),
        ("j@example.com", "1"),
        ("k@example.com", "Verified Pending"),
        ("l@example.com", "Not Verified"),
    ]
    rows = [_row(f"Last{i}", f"First{i}", email, status) for i, (email, status) in enumerate(cases)]
    _upload_csv(client, token, rows)

    for email, status in cases:
        alumni = _alumni_by_email(db_session, email)
        assert alumni.verified is False, f"{email!r} with status {status!r} expected verified=False"
        # Text is preserved exactly regardless of whether it matched.
        assert alumni.verification_status == status


# --------------------------------------------------------------------------
# Explicit boolean column path (_parse_bool) is unaffected
# --------------------------------------------------------------------------

BOOLEAN_HEADER = (
    "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,Verified"
)


def test_explicit_boolean_verified_column_still_uses_parse_bool(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    cases = [
        ("m@example.com", "true", True),
        ("n@example.com", "TRUE", True),
        ("o@example.com", "yes", True),
        ("p@example.com", "1", True),
        ("q@example.com", "y", True),
        ("r@example.com", "verified", True),
        ("s@example.com", "false", False),
        ("t@example.com", "no", False),
    ]
    lines = [BOOLEAN_HEADER]
    for i, (email, raw, _expected) in enumerate(cases):
        lines.append(
            f"Last{i},First{i},{email},https://linkedin.com/in/user{i},Acme Inc,Engineer,Tallahassee,FL,{raw}"
        )
    response = _upload(client, token, "\n".join(lines) + "\n")
    assert response.status_code == 200, response.text

    for email, _raw, expected in cases:
        alumni = _alumni_by_email(db_session, email)
        assert alumni.verified is expected, f"{email!r} raw={_raw!r} expected verified={expected}"
        # The boolean-column path derives a normalized status string, as
        # it already did before this fix - completely unchanged.
        assert alumni.verification_status == ("verified" if expected else "unverified")


def test_explicit_boolean_column_takes_priority_over_status_text(client, organization, admin_user, db_session):
    """When BOTH a boolean `Verified` column and a text `Verification
    Status` column are present, the existing boolean-column branch is
    used (unchanged priority) - the status-text derivation only applies
    when there is no boolean column at all."""
    token = _login(client, "admin", "AdminPass123!")
    header = (
        "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State,"
        "Notes,Verified,Verification Status,Verification Date,Education"
    )
    row = (
        "Last,First,u@example.com,https://linkedin.com/in/uu,Acme Inc,Engineer,Tallahassee,FL,,"
        "true,Unverified,2026-01-01,Florida State University"
    )
    response = _upload(client, token, "\n".join([header, row]) + "\n")
    assert response.status_code == 200, response.text

    alumni = _alumni_by_email(db_session, "u@example.com")
    # The boolean column ("true") wins - exactly the pre-existing
    # priority (`if raw_verified is not None: ...` branch is taken
    # first) - even though the text column literally says "Unverified".
    assert alumni.verified is True
    assert alumni.verification_status == "Unverified"


# --------------------------------------------------------------------------
# Raw text preserved exactly (audit/display string never rewritten)
# --------------------------------------------------------------------------


def test_verification_status_text_preserved_exactly(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    rows = [
        _row("A", "Mixed", "mixed@example.com", "VeRiFiEd"),
        _row("B", "Spaced", "spaced@example.com", " Verified "),
    ]
    _upload_csv(client, token, rows)

    # _clean_value trims surrounding whitespace for every CSV field (not
    # specific to this fix) but never changes case.
    assert _alumni_by_email(db_session, "mixed@example.com").verification_status == "VeRiFiEd"
    assert _alumni_by_email(db_session, "spaced@example.com").verification_status == "Verified"


# --------------------------------------------------------------------------
# Mixed rows -> correct verified count/percentage in analytics
# --------------------------------------------------------------------------


def test_mixed_verification_statuses_produce_correct_analytics_counts(client, organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    rows = [
        _row("A", "One", "one@example.com", "Verified"),
        _row("B", "Two", "two@example.com", "Verified"),
        _row("C", "Three", "three@example.com", "Unverified"),
        _row("D", "Four", "four@example.com", "Pending"),
    ]
    _upload_csv(client, token, rows)

    response = client.get("/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["verified"] == 2
    assert body["verified_alumni"] == 2
    assert body["verification_percentage"] == 50.0


# --------------------------------------------------------------------------
# Update / reimport semantics
# --------------------------------------------------------------------------


def test_reimport_omitting_status_column_does_not_reset_existing_verified(
    client, organization, admin_user, db_session
):
    token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, token, [_row("A", "Persist", "persist@example.com", "Verified")])
    assert _alumni_by_email(db_session, "persist@example.com").verified is True

    # Reimport the SAME person with a header that has no verification
    # columns at all - the existing verified=True must be preserved
    # (the general "omitted field never overwrites existing nonnull
    # value" safe-update rule, unchanged by this fix).
    no_status_header = "Last Name,First Name,Email,Linkedin URL,Company Name,Job Title,City,State"
    row = "Persist,A,persist@example.com,https://linkedin.com/in/afirst,Acme Inc,Engineer,Tallahassee,FL"
    response = _upload(client, token, "\n".join([no_status_header, row]) + "\n")
    assert response.status_code == 200, response.text

    assert _alumni_by_email(db_session, "persist@example.com").verified is True
    assert _alumni_by_email(db_session, "persist@example.com").verification_status == "Verified"


def test_reimport_explicitly_setting_unverified_updates_verified_to_false(
    client, organization, admin_user, db_session
):
    """Documented, existing field-update semantics extended consistently:
    an EXPLICIT nonblank Verification Status value always wins on
    reimport (exactly like every other CSV field, and exactly like the
    explicit boolean-column path already did before this fix) - it is
    never left stale just because the new value doesn't say "Verified".
    This does not introduce any new verification lifecycle; it applies
    the exact same "nonblank CSV always wins" rule this pipeline already
    uses everywhere else."""
    token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, token, [_row("A", "Flip", "flip@example.com", "Verified")])
    assert _alumni_by_email(db_session, "flip@example.com").verified is True

    _upload_csv(client, token, [_row("A", "Flip", "flip@example.com", "Unverified")])
    alumni = _alumni_by_email(db_session, "flip@example.com")
    assert alumni.verified is False
    assert alumni.verification_status == "Unverified"


# --------------------------------------------------------------------------
# Organization scoping unaffected
# --------------------------------------------------------------------------


def test_verified_derivation_is_organization_scoped(client, organization, other_organization, admin_user, db_session):
    token = _login(client, "admin", "AdminPass123!")
    _upload_csv(client, token, [_row("A", "Home", "home@example.com", "Verified")])  # default org (fsu-cci)
    _upload_csv(
        client, token, [_row("B", "Other", "other@example.com", "Verified")], organization="stars-national",
    )

    home_summary = client.get(
        "/analytics/summary", headers={"Authorization": f"Bearer {token}"},
    ).json()
    other_summary = client.get(
        "/analytics/summary", params={"organization": "stars-national"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert home_summary["totals"]["alumni"] == 1
    assert home_summary["totals"]["verified"] == 1
    assert other_summary["totals"]["alumni"] == 1
    assert other_summary["totals"]["verified"] == 1

    home_alumni = (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id)
        .all()
    )
    assert {a.email for a in home_alumni} == {"home@example.com"}
