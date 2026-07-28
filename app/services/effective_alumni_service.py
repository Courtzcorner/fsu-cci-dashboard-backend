"""
SQL-level "effective alumni data" layer: combines imported `Alumni`
fields with a CONFIRMED, privacy-respecting `UserProfile` override,
entirely via SQL CASE expressions - never by loading rows into Python.
Used by GET /alumni-data, GET /analytics/summary, GET /analytics/locations,
and GET /admin/export-alumni so a profile edit shows up everywhere
without a new CSV upload.

Rules enforced here (all server-side, all at the SQL level):
  - Only a CONFIRMED link (`user_confirmed` / `admin_confirmed`) - never
    `unmatched`/`candidate`/`rejected`/`conflict` - can ever contribute
    an override.
  - A confirmed link whose Alumni record has since been deactivated by a
    newer CSV import contributes nothing, because every caller already
    filters `Alumni.is_active == True` - the row simply is not part of
    the active dataset at all, so there is nothing extra to guard here.
  - Every override is additionally gated by the profile owner's own
    privacy setting for that field (show_current_employer, show_job_title,
    show_education, show_location, show_linkedin) - a private profile
    value never leaks into the public directory or analytics.
  - The original imported `Alumni` row is never read from with a
    mutated value - these are read-only SELECT expressions, never
    UPDATEs.
"""
from sqlalchemy import and_, case
from sqlalchemy.orm import aliased

from app.models.alumni import Alumni
from app.models.user_profile import LinkStatus, UserProfile


def confirmed_profile_alias():
    """A fresh SQL alias for UserProfile, scoped to one query. Always
    joined with `confirmed_profile_join_condition` below."""
    return aliased(UserProfile, name="confirmed_profile")


def confirmed_profile_join_condition(profile_alias):
    return and_(
        profile_alias.alumni_id == Alumni.id,
        profile_alias.link_status.in_(LinkStatus.CONFIRMED),
    )


class EffectiveColumns:
    """Named SQL expressions for every "effective" field, built against
    one specific `profile_alias` (created via `confirmed_profile_alias()`
    and OUTER JOINed with `confirmed_profile_join_condition`)."""

    def __init__(self, profile_alias):
        p = profile_alias
        a = Alumni

        self.full_name = case((p.effective_full_name.isnot(None), p.effective_full_name), else_=a.full_name)

        self.company = case(
            (and_(p.show_current_employer.is_(True), p.current_employer.isnot(None)), p.current_employer),
            else_=a.company,
        )
        self.job_title = case(
            (and_(p.show_job_title.is_(True), p.current_job_title.isnot(None)), p.current_job_title),
            else_=a.job_title,
        )
        self.university = case(
            (and_(p.show_education.is_(True), p.current_university.isnot(None)), p.current_university),
            else_=a.university,
        )
        self.city = case(
            (and_(p.show_location.is_(True), p.current_city.isnot(None)), p.current_city),
            else_=a.city,
        )
        self.state = case(
            (and_(p.show_location.is_(True), p.current_state.isnot(None)), p.current_state),
            else_=a.state,
        )
        self.linkedin_url = case(
            (and_(p.show_linkedin.is_(True), p.linkedin_url.isnot(None)), p.linkedin_url),
            else_=a.linkedin_url,
        )

        # Derived fields: recomputed at profile-save time (see
        # effective_profile_service) via the SAME deterministic rules the
        # CSV import pipeline uses - never guessed here.
        self.seniority = case(
            (and_(p.show_job_title.is_(True), p.effective_seniority.isnot(None)), p.effective_seniority),
            else_=a.seniority,
        )
        self.career_category = case(
            (
                and_(p.show_job_title.is_(True), p.effective_career_category.isnot(None)),
                p.effective_career_category,
            ),
            else_=a.career_category,
        )
        self.industry = case(
            (and_(p.show_current_employer.is_(True), p.effective_industry.isnot(None)), p.effective_industry),
            else_=a.industry,
        )
