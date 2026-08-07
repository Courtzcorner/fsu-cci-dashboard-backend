"""
FAMU STARS: a new institutional organization (slug `famu-stars`, display
name "FAMU STARS", context_type "institution", theme_key "stars-famu")
added alongside the existing `fsu-stars`/`stars-national`/`fsu-cci`
organizations, following the exact same seed/authorization/import
machinery - no new architecture.

Covers:
- idempotent seeding (app.seed.seed_data.seed_organizations)
- fsu-cci remains hidden, untouched, unrenamed
- admin can access/import into famu-stars immediately after seeding
- FAMU-only imports never affect FSU STARS, STARS National, or legacy
  fsu-cci data (organization-scoped import/current-import/export/analytics)
- alumni visibility for famu-stars now matches the SAME unconditional
  temporary-compatibility exemption already applied to fsu-stars/
  stars-national (see app.services.temporary_alumni_context_policy):
  famu-stars is visible/accessible to eligible alumni even with zero
  active data, not gated behind the generic
  "institution with no active dataset is hidden from non-admins" rule.
- fsu-stars and stars-national behavior is provably unchanged by any of
  the above.
- fsu-cci remains hidden/blocked exactly as before.
"""
import csv
import io

from app.deps import CurrentUser, get_authorized_organization
from app.models.alumni import Alumni, AlumniOrganization
from app.models.organization import Organization
from app.models.user_organization import UserOrganization
from app.seed.seed_data import ORGANIZATION_CONTEXT_METADATA, ORGANIZATIONS_SEED_DATA, seed_organizations
from app.services.organization_context_service import build_available_contexts
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, ALUMNI_PASSWORD, ALUMNI_USERNAME, login
from tests.test_import import _upload


def _current_user(user, role=None):
    return CurrentUser(id=user.id, username=user.username, role=role or user.role, alumni_id=user.alumni_id)


def _make_organization(db_session, slug, name, context_type="institution", theme_key=None):
    org = Organization(name=name, slug=slug, context_type=context_type, theme_key=theme_key)
    db_session.add(org)
    db_session.commit()
    return org


def _link_active_alumni(db_session, organization):
    alumni = Alumni(first_name="Active", last_name="Person", full_name="Active Person", is_active=True)
    db_session.add(alumni)
    db_session.flush()
    db_session.add(AlumniOrganization(alumni_id=alumni.id, organization_id=organization.id))
    db_session.commit()
    return alumni


def _csv_of(rows: list[tuple[str, str]]) -> str:
    lines = ["First Name,Last Name,LinkedIn URL"]
    for i, (first, last) in enumerate(rows):
        lines.append(f"{first},{last},linkedin.com/in/{first.lower()}{last.lower()}{i}")
    return "\n".join(lines) + "\n"


def _active_alumni_count(db_session, organization) -> int:
    return (
        db_session.query(Alumni)
        .join(AlumniOrganization, AlumniOrganization.alumni_id == Alumni.id)
        .filter(AlumniOrganization.organization_id == organization.id, Alumni.is_active.is_(True))
        .count()
    )


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


def test_famu_stars_is_in_the_seed_data_with_correct_context_metadata():
    slugs = {entry["slug"] for entry in ORGANIZATIONS_SEED_DATA}
    assert "famu-stars" in slugs
    entry = next(e for e in ORGANIZATIONS_SEED_DATA if e["slug"] == "famu-stars")
    assert entry["name"] == "FAMU STARS"
    assert ORGANIZATION_CONTEXT_METADATA["famu-stars"] == {"context_type": "institution", "theme_key": "stars-famu"}


def test_seed_organizations_creates_famu_stars_with_correct_fields(db_session):
    seed_organizations(db_session)

    famu = db_session.query(Organization).filter(Organization.slug == "famu-stars").first()
    assert famu is not None
    assert famu.name == "FAMU STARS"
    assert famu.context_type == "institution"
    assert famu.theme_key == "stars-famu"


def test_seed_organizations_is_idempotent_for_famu_stars(db_session):
    seed_organizations(db_session)
    first_id = db_session.query(Organization).filter(Organization.slug == "famu-stars").first().id

    seed_organizations(db_session)  # re-run

    famu_rows = db_session.query(Organization).filter(Organization.slug == "famu-stars").all()
    assert len(famu_rows) == 1
    assert famu_rows[0].id == first_id
    assert famu_rows[0].name == "FAMU STARS"


def test_seed_organizations_leaves_fsu_cci_fsu_stars_and_national_untouched(db_session):
    """Adding famu-stars to the seed data must never rename, merge, or
    change the ID of any pre-existing organization row."""
    seed_organizations(db_session)
    fsu_cci_before = db_session.query(Organization).filter(Organization.slug == "fsu-cci").first()
    fsu_stars_before = db_session.query(Organization).filter(Organization.slug == "fsu-stars").first()
    national_before = db_session.query(Organization).filter(Organization.slug == "stars-national").first()
    ids_before = (fsu_cci_before.id, fsu_stars_before.id, national_before.id)

    seed_organizations(db_session)  # re-run, should be a total no-op for existing rows

    fsu_cci_after = db_session.query(Organization).filter(Organization.slug == "fsu-cci").first()
    fsu_stars_after = db_session.query(Organization).filter(Organization.slug == "fsu-stars").first()
    national_after = db_session.query(Organization).filter(Organization.slug == "stars-national").first()

    assert (fsu_cci_after.id, fsu_stars_after.id, national_after.id) == ids_before
    assert fsu_cci_after.name == "FSU College of Communication and Information"
    assert fsu_cci_after.theme_key is None
    assert fsu_stars_after.name == "FSU STARS"
    assert fsu_stars_after.theme_key == "stars-fsu"
    assert national_after.name == "STARS National"
    assert national_after.theme_key == "stars-national"


def test_seed_organizations_does_not_reuse_fsu_cci_organization_id(db_session):
    seed_organizations(db_session)
    fsu_cci = db_session.query(Organization).filter(Organization.slug == "fsu-cci").first()
    famu = db_session.query(Organization).filter(Organization.slug == "famu-stars").first()
    assert famu.id != fsu_cci.id


# --------------------------------------------------------------------------
# fsu-cci hidden-context protection is unaffected
# --------------------------------------------------------------------------


def test_fsu_cci_remains_hidden_after_seeding_famu_stars(db_session, admin_user, alumni_user):
    seed_organizations(db_session)

    admin_slugs = {c.slug for c in build_available_contexts(db_session, _current_user(admin_user))}
    alumni_slugs = {c.slug for c in build_available_contexts(db_session, _current_user(alumni_user))}
    assert "fsu-cci" not in admin_slugs
    assert "fsu-cci" not in alumni_slugs


def test_fsu_cci_data_is_untouched_by_famu_import(client, organization, admin_user, db_session):
    """`organization` fixture IS fsu-cci with an already-linked active
    alumnus (see tests/conftest.py) - a FAMU-only import must never
    touch it."""
    _link_active_alumni(db_session, organization)
    famu = _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    fsu_cci_count_before = _active_alumni_count(db_session, organization)
    assert fsu_cci_count_before >= 1

    response = _upload(client, token, _csv_of([("Rattler", "One")]), organization="famu-stars")
    assert response.status_code == 200
    assert response.json()["organization"] == "famu-stars"

    assert _active_alumni_count(db_session, organization) == fsu_cci_count_before
    assert _active_alumni_count(db_session, famu) == 1


# --------------------------------------------------------------------------
# Admin access/import into famu-stars
# --------------------------------------------------------------------------


def test_legacy_admin_can_see_famu_stars_with_no_active_dataset(db_session, admin_user):
    """An admin (legacy, zero UserOrganization rows - today's actual
    production state) must see famu-stars immediately after seeding, even
    with zero data, so the first import can be performed - exactly like
    any other institution context."""
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    contexts = build_available_contexts(db_session, _current_user(admin_user))
    ctx = next((c for c in contexts if c.slug == "famu-stars"), None)
    assert ctx is not None
    assert ctx.has_active_dataset is False
    assert ctx.can_import is True
    assert ctx.context_type == "institution"
    assert ctx.theme_key == "stars-famu"


def test_admin_can_import_current_import_and_export_for_famu_stars(client, admin_user, db_session):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    import_response = _upload(client, token, _csv_of([("Rattler", "One"), ("Rattler", "Two")]), organization="famu-stars")
    assert import_response.status_code == 200
    assert import_response.json()["organization"] == "famu-stars"
    assert import_response.json()["created"] == 2

    current_import = client.get(
        "/admin/current-import", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert current_import.status_code == 200
    assert current_import.json()["organization"] == "famu-stars"
    assert current_import.json()["active_database_total"] == 2

    export = client.get(
        "/admin/export-alumni", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 2
    assert {row["First Name"] for row in rows} == {"Rattler"}


def test_alumni_role_receives_403_on_all_three_famu_admin_endpoints(client, admin_user, alumni_user, db_session):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    assert _upload(client, token, _csv_of([("X", "Y")]), organization="famu-stars").status_code == 403
    assert (
        client.get(
            "/admin/current-import", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/admin/export-alumni", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 403
    )


# --------------------------------------------------------------------------
# Cross-organization isolation: FAMU import never affects FSU/National/CCI
# --------------------------------------------------------------------------


def test_famu_import_does_not_affect_fsu_stars_or_national_datasets(client, organization, admin_user, db_session):
    fsu_stars = _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    national = _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    famu = _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    _upload(client, token, _csv_of([("Nat", "Ional")]), organization="stars-national")

    _upload(client, token, _csv_of([("Rattler", "One")]), organization="famu-stars")

    assert _active_alumni_count(db_session, fsu_stars) == 1
    assert _active_alumni_count(db_session, national) == 1
    assert _active_alumni_count(db_session, famu) == 1
    assert db_session.query(Alumni).filter(Alumni.first_name == "Fiona").one().is_active is True
    assert db_session.query(Alumni).filter(Alumni.first_name == "Nat").one().is_active is True


def test_famu_replace_mode_never_touches_fsu_or_national_active_dataset(client, organization, admin_user, db_session):
    fsu_stars = _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    famu = _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    _upload(client, token, _csv_of([("A", "Rattler"), ("B", "Rattler"), ("C", "Rattler")]), organization="famu-stars")
    assert _active_alumni_count(db_session, famu) == 3

    # A smaller re-upload (replace mode) into famu-stars only affects famu-stars.
    response = _upload(client, token, _csv_of([("A", "Rattler")]), organization="famu-stars")
    assert response.status_code == 200
    assert response.json()["active_database_total"] == 1

    assert _active_alumni_count(db_session, famu) == 1
    assert _active_alumni_count(db_session, fsu_stars) == 1  # fsu-stars untouched


def test_famu_current_import_and_export_never_leak_other_organizations(client, organization, admin_user, db_session):
    fsu_stars = _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    _upload(client, token, _csv_of([("Rattler", "One"), ("Rattler", "Two")]), organization="famu-stars")

    famu_current = client.get(
        "/admin/current-import", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    fsu_current = client.get(
        "/admin/current-import", params={"organization": "fsu-stars"}, headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert famu_current["active_database_total"] == 2
    assert fsu_current["active_database_total"] == 1
    assert famu_current["csv_import_id"] != fsu_current["csv_import_id"]

    famu_export = client.get(
        "/admin/export-alumni", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    rows = list(csv.DictReader(io.StringIO(famu_export.text)))
    assert len(rows) == 2
    assert all(row["First Name"] == "Rattler" for row in rows)
    _ = fsu_stars


# --------------------------------------------------------------------------
# Analytics/alumni-data organization scoping for famu-stars
# --------------------------------------------------------------------------


def test_analytics_summary_for_famu_stars_reflects_only_famu_data(client, organization, admin_user, db_session):
    _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    _upload(client, token, _csv_of([("Rattler", "One"), ("Rattler", "Two"), ("Rattler", "Three")]), organization="famu-stars")

    response = client.get(
        "/analytics/summary", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["totals"]["alumni"] == 3


def test_alumni_data_for_famu_stars_reflects_only_famu_data(client, organization, admin_user, db_session):
    _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    _upload(client, token, _csv_of([("Fiona", "Stars")]), organization="fsu-stars")
    _upload(client, token, _csv_of([("Rattler", "One")]), organization="famu-stars")

    response = client.get(
        "/alumni-data", params={"organization": "famu-stars", "page_size": 100},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["first_name"] == "Rattler"


# --------------------------------------------------------------------------
# Alumni visibility now matches the fsu-stars/stars-national unconditional
# exemption: famu-stars is visible/accessible to eligible alumni even with
# zero active data (see
# TEMPORARY_ALUMNI_SLUGS_EXEMPT_FROM_ACTIVE_DATASET_RULE).
# --------------------------------------------------------------------------


def test_legacy_alumni_sees_famu_stars_with_zero_active_dataset(db_session, alumni_user):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    contexts = build_available_contexts(db_session, _current_user(alumni_user))
    ctx = next((c for c in contexts if c.slug == "famu-stars"), None)
    assert ctx is not None
    assert ctx.has_active_dataset is False
    assert ctx.role == "alumni"
    assert ctx.can_import is False


def test_legacy_alumni_still_sees_famu_stars_once_it_has_an_active_dataset(db_session, alumni_user):
    famu = _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _link_active_alumni(db_session, famu)

    contexts = build_available_contexts(db_session, _current_user(alumni_user))
    ctx = next((c for c in contexts if c.slug == "famu-stars"), None)
    assert ctx is not None
    assert ctx.has_active_dataset is True
    assert ctx.role == "alumni"
    assert ctx.can_import is False


def test_alumni_with_unrelated_membership_still_sees_empty_famu_stars(db_session, alumni_user, organization):
    """An alumni account whose only explicit membership excludes
    famu-stars must still receive it via the temporary compatibility
    policy even while it has no active dataset - exactly like
    fsu-stars/stars-national."""
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    contexts = build_available_contexts(db_session, _current_user(alumni_user))
    ctx = next((c for c in contexts if c.slug == "famu-stars"), None)
    assert ctx is not None
    assert ctx.has_active_dataset is False
    assert ctx.role == "alumni"
    assert ctx.can_import is False


def test_alumni_with_unrelated_membership_sees_famu_stars_once_active(db_session, alumni_user, organization):
    famu = _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _link_active_alumni(db_session, famu)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    contexts = build_available_contexts(db_session, _current_user(alumni_user))
    slugs = {c.slug for c in contexts}
    assert "famu-stars" in slugs


def test_get_authorized_organization_allows_empty_famu_stars_for_membership_restricted_alumni(
    db_session, alumni_user, organization
):
    """get_authorized_organization (the dependency that actually
    implements the temporary compatibility policy for direct reads - see
    app.deps) must mirror build_available_contexts: a
    membership-restricted alumni account requesting an EMPTY famu-stars
    context directly is granted read-context access via the temporary
    compatibility policy, exactly like fsu-stars/stars-national."""
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    access = get_authorized_organization(organization="famu-stars", current_user=_current_user(alumni_user), db=db_session)
    assert access.organization.slug == "famu-stars"
    assert access.effective_role == "alumni"


def test_get_authorized_organization_allows_active_famu_stars_for_membership_restricted_alumni(
    db_session, alumni_user, organization
):
    famu = _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _link_active_alumni(db_session, famu)
    db_session.add(UserOrganization(user_id=alumni_user.id, organization_id=organization.id, role="alumni"))
    db_session.commit()

    access = get_authorized_organization(organization="famu-stars", current_user=_current_user(alumni_user), db=db_session)
    assert access.organization.slug == "famu-stars"
    assert access.effective_role == "alumni"


def test_famu_stars_never_grants_more_than_alumni_role_via_compatibility_policy(db_session, alumni_user):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")

    access = get_authorized_organization(organization="famu-stars", current_user=_current_user(alumni_user), db=db_session)
    assert access.effective_role == "alumni"


def test_alumni_can_directly_access_empty_famu_stars_via_alumni_data_endpoint(client, alumni_user, db_session):
    """?organization=famu-stars must resolve for an authenticated alumni
    user under the same compatibility behavior as fsu-stars/stars-national,
    even before an active FAMU dataset exists."""
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    response = client.get(
        "/alumni-data", params={"organization": "famu-stars"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_available_contexts_endpoint_shows_famu_stars_for_alumni_with_zero_data(client, alumni_user, db_session):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)

    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    contexts = {c["slug"]: c for c in response.json()["contexts"]}
    assert {"famu-stars", "fsu-stars", "stars-national"} <= set(contexts.keys())
    assert contexts["famu-stars"]["role"] == "alumni"
    assert contexts["famu-stars"]["can_import"] is False
    assert contexts["famu-stars"]["has_active_dataset"] is False


def test_available_contexts_endpoint_shows_famu_stars_for_admin_with_zero_data(client, admin_user, db_session):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    response = client.get("/organizations/available-contexts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    contexts = {c["slug"]: c for c in response.json()["contexts"]}
    assert {"famu-stars", "fsu-stars", "stars-national"} <= set(contexts.keys())
    assert contexts["famu-stars"]["role"] == "admin"
    assert contexts["famu-stars"]["can_import"] is True
    assert contexts["famu-stars"]["has_active_dataset"] is False


# --------------------------------------------------------------------------
# fsu-stars / stars-national behavior is provably unchanged
# --------------------------------------------------------------------------


def test_fsu_stars_still_unconditionally_visible_to_alumni_with_no_active_dataset(db_session, alumni_user):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")  # sibling exists, no data
    _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")

    contexts = build_available_contexts(db_session, _current_user(alumni_user))
    ctx = next((c for c in contexts if c.slug == "fsu-stars"), None)
    assert ctx is not None
    assert ctx.has_active_dataset is False


def test_stars_national_still_unconditionally_visible_to_alumni_with_no_active_dataset(db_session, alumni_user):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")

    contexts = build_available_contexts(db_session, _current_user(alumni_user))
    ctx = next((c for c in contexts if c.slug == "stars-national"), None)
    assert ctx is not None
    assert ctx.has_active_dataset is False


def test_direct_alumni_read_of_empty_fsu_stars_and_national_remains_allowed(db_session, client, alumni_user):
    _make_organization(db_session, "famu-stars", "FAMU STARS", theme_key="stars-famu")
    _make_organization(db_session, "fsu-stars", "FSU STARS", theme_key="stars-fsu")
    _make_organization(db_session, "stars-national", "STARS National", context_type="national")

    token = login(client, ALUMNI_USERNAME, ALUMNI_PASSWORD)
    for slug in ("fsu-stars", "stars-national"):
        response = client.get(
            "/alumni-data", params={"organization": slug}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200


# --------------------------------------------------------------------------
# Full production-shaped seed check (all four organizations together)
# --------------------------------------------------------------------------


def test_full_seed_produces_all_four_organizations_with_correct_metadata(db_session):
    seed_organizations(db_session)
    orgs = {org.slug: org for org in db_session.query(Organization).all()}

    assert set(orgs.keys()) == {"fsu-cci", "fsu-stars", "stars-national", "famu-stars"}
    assert orgs["fsu-cci"].context_type == "institution"
    assert orgs["fsu-cci"].theme_key is None
    assert orgs["fsu-stars"].context_type == "institution"
    assert orgs["fsu-stars"].theme_key == "stars-fsu"
    assert orgs["stars-national"].context_type == "national"
    assert orgs["stars-national"].theme_key == "stars-national"
    assert orgs["famu-stars"].context_type == "institution"
    assert orgs["famu-stars"].theme_key == "stars-famu"


def test_seeding_does_not_touch_any_alumni_or_import_data(db_session, organization, admin_user):
    """Purely a sanity check that seed_organizations() never issues
    anything but Organization upserts - no Alumni/CSVImport table is
    touched, confirming no production data risk from re-running it."""
    _link_active_alumni(db_session, organization)
    alumni_count_before = db_session.query(Alumni).count()

    seed_organizations(db_session)

    assert db_session.query(Alumni).count() == alumni_count_before
