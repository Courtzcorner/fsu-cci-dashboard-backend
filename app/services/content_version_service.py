"""
Shared content synchronization: small, database-backed version counters
that let every logged-in client (regardless of when its account was
created, or how long ago it last logged in) detect that an
administrator changed shared data, without polling analytics or
re-reading alumni rows.

Nothing here is stored in memory, in a browser, or in the JWT - every
counter lives in the `content_versions` table (see
app.models.content_version) and is read/written through this module
only. Callers bump the relevant domain(s) as part of the SAME database
transaction as their mutation (i.e. before `db.commit()`, using
`db.flush()` here) so a rolled-back mutation never leaves a false
version bump behind, and a committed mutation is never missing one.

Domain -> what it represents:
  alumni        the active alumni directory dataset (imported CSV rows,
                or a linked profile's effective override of one)
  analytics     anything summarized by GET /analytics/* (career
                category, seniority, industry, company, university,
                location breakdowns)
  locations     city/state/map data specifically (a subset of what
                feeds analytics, exposed separately since the map view
                often needs to refresh independently)
  events        the Events feature
  superstars    the Super STARS feature
  speakers      speaker / panel candidate data
  universities  the normalized university reference table
  profiles      user profile / directory-link moderation state (admin
                candidate queue, link confirmations, profile edits)

"global" is bumped alongside every domain bump and lets a client do a
single cheap check ("did anything at all change?") before inspecting
which specific domain(s) moved.
"""
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.content_version import CONTENT_DOMAINS, GLOBAL_DOMAIN, ContentVersion
from app.models.mixins import utcnow
from app.models.user_profile import LinkStatus

# Raw UserProfile fields whose CONFIRMED-link effective value feeds
# GET /alumni-data / GET /analytics/* / the public directory. Used by
# `bump_for_profile_update` below to decide which extra domains a
# self-service profile edit should bump.
ANALYTICS_TRACKED_PROFILE_FIELDS = frozenset(
    {"current_employer", "current_job_title", "current_industry", "current_university"}
)
LOCATION_TRACKED_PROFILE_FIELDS = frozenset({"current_city", "current_state"})
EFFECTIVE_TRACKED_PROFILE_FIELDS = ANALYTICS_TRACKED_PROFILE_FIELDS | LOCATION_TRACKED_PROFILE_FIELDS


def _get_or_create_row(db: Session, domain: str) -> ContentVersion:
    row = db.query(ContentVersion).filter(ContentVersion.domain == domain).first()
    if row is None:
        row = ContentVersion(domain=domain, version=0)
        db.add(row)
        db.flush()
    return row


def bump_domain(
    db: Session,
    domain: str,
    *,
    updated_by_user_id: Optional[str] = None,
    change_type: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> int:
    """Increments exactly one domain's version by 1 and returns the new
    value. Part of the caller's current (uncommitted) transaction - never
    calls `db.commit()` itself, so a subsequent rollback undoes this bump
    too."""
    row = _get_or_create_row(db, domain)
    row.version += 1
    row.updated_at = utcnow()
    row.updated_by_user_id = updated_by_user_id
    row.change_type = change_type
    row.resource_id = resource_id
    db.flush()
    return row.version


def bump_domains(
    db: Session,
    domains: Iterable[str],
    *,
    updated_by_user_id: Optional[str] = None,
    change_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    include_global: bool = True,
) -> None:
    """Bumps every domain in `domains` (deduplicated, order-independent)
    and, unless `include_global=False`, the shared "global" counter as
    well - always as part of the caller's current transaction."""
    unique_domains = {d for d in domains if d}
    for domain in unique_domains:
        bump_domain(db, domain, updated_by_user_id=updated_by_user_id, change_type=change_type, resource_id=resource_id)
    if include_global and unique_domains:
        bump_domain(db, GLOBAL_DOMAIN, updated_by_user_id=updated_by_user_id, change_type=change_type, resource_id=resource_id)


# --------------------------------------------------------------------------
# Call-site-specific helpers - one per admin mutation family, so each
# router/service only has to say WHAT happened, not which domains that
# implies.
# --------------------------------------------------------------------------


def bump_for_csv_import(db: Session, *, updated_by_user_id: Optional[str], resource_id: str) -> None:
    """A successfully committed CSV import replaces the active alumni
    dataset, so every downstream view derived from it is stale:
    directory (alumni), analytics breakdowns, the map (locations), and
    the university reference table."""
    bump_domains(
        db,
        ["alumni", "analytics", "locations", "universities"],
        updated_by_user_id=updated_by_user_id,
        change_type="csv_import",
        resource_id=resource_id,
    )


def bump_for_event_change(db: Session, *, change_type: str, updated_by_user_id: Optional[str], resource_id: str) -> None:
    bump_domains(db, ["events"], updated_by_user_id=updated_by_user_id, change_type=change_type, resource_id=resource_id)


def bump_for_superstar_change(
    db: Session, *, change_type: str, updated_by_user_id: Optional[str], resource_id: str
) -> None:
    bump_domains(
        db, ["superstars"], updated_by_user_id=updated_by_user_id, change_type=change_type, resource_id=resource_id
    )


def bump_for_speaker_change(db: Session, *, change_type: str, updated_by_user_id: Optional[str], resource_id: str) -> None:
    bump_domains(db, ["speakers"], updated_by_user_id=updated_by_user_id, change_type=change_type, resource_id=resource_id)


def bump_for_profile_link_change(
    db: Session, *, change_type: str, updated_by_user_id: Optional[str], resource_id: str
) -> None:
    """Any admin/user action that changes a profile's link decision
    (confirm, admin-approve, admin-reject, unlink): the profile
    moderation queue changed (profiles), and - since a link transition
    immediately starts or stops the effective-data override - so did
    everything downstream of it (alumni directory, analytics, map)."""
    bump_domains(
        db,
        ["profiles", "alumni", "analytics", "locations"],
        updated_by_user_id=updated_by_user_id,
        change_type=change_type,
        resource_id=resource_id,
    )


def bump_for_profile_update(
    db: Session,
    profile,
    changed_fields: set[str],
    *,
    updated_by_user_id: Optional[str],
    resource_id: str,
) -> None:
    """A self-service PUT /profile/me save. Always bumps "profiles" (the
    admin moderation/candidate-queue view of this profile changed).
    Additionally bumps "alumni"/"analytics"/"locations" ONLY when this
    profile is a CONFIRMED link (i.e. it is actually contributing to the
    public effective-data layer today) AND one of the fields that feeds
    that layer actually changed value.
    """
    domains = {"profiles"}

    if profile.link_status in LinkStatus.CONFIRMED:
        touched = changed_fields & EFFECTIVE_TRACKED_PROFILE_FIELDS
        if touched:
            domains.add("alumni")
            if touched & ANALYTICS_TRACKED_PROFILE_FIELDS:
                domains.add("analytics")
            if touched & LOCATION_TRACKED_PROFILE_FIELDS:
                domains.add("locations")
                # A location change also shifts location-based analytics
                # breakdowns (e.g. "alumni by state").
                domains.add("analytics")

    bump_domains(
        db, domains, updated_by_user_id=updated_by_user_id, change_type="profile_update", resource_id=resource_id
    )


def get_sync_status(db: Session) -> dict:
    """Cheap read: a single query over the (at most len(CONTENT_DOMAINS)+1)
    rows in `content_versions` - never touches `alumni`, analytics
    aggregation, or any other large table."""
    rows = db.query(ContentVersion).all()
    by_domain = {row.domain: row for row in rows}

    domains: dict[str, int] = {domain: by_domain[domain].version if domain in by_domain else 0 for domain in CONTENT_DOMAINS}

    global_row = by_domain.get(GLOBAL_DOMAIN)
    global_version = global_row.version if global_row is not None else 0

    # "updated_at" reflects the most recently touched row of ALL of them
    # (global row included) so a client always sees the true most-recent
    # change timestamp even if, in principle, a caller ever bumped a
    # domain without bumping global.
    most_recent = max((row.updated_at for row in rows), default=None)

    return {
        "global_version": global_version,
        "updated_at": most_recent,
        "domains": domains,
    }
