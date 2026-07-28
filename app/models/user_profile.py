"""
Self-service alumni "user profile" system.

This is an ADDITIVE layer that lives entirely in its own tables and only
ever *references* `Alumni` (imported CSV data) via a nullable
`alumni_id` foreign key. Nothing here ever writes into the `alumni`
table, and nothing in the CSV import pipeline needs to know these tables
exist.

Provenance stays completely separate:
  - `Alumni.*`            -> imported CSV data (owned by csv_import_service)
  - `UserProfile.*`       -> user-supplied data (owned by this module)
  - `UserProfile.alumni_id` + `link_status` -> the (optional, reviewable)
    connection between the two, never a merge of the underlying data.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class LinkStatus:
    """Possible values of UserProfile.link_status. Plain string constants
    (not a DB enum) so SQLite/Postgres both work without a migration to
    add new values later."""

    UNMATCHED = "unmatched"
    CANDIDATE = "candidate"
    USER_CONFIRMED = "user_confirmed"
    ADMIN_CONFIRMED = "admin_confirmed"
    REJECTED = "rejected"
    CONFLICT = "conflict"

    ALL = {UNMATCHED, CANDIDATE, USER_CONFIRMED, ADMIN_CONFIRMED, REJECTED, CONFLICT}
    CONFIRMED = {USER_CONFIRMED, ADMIN_CONFIRMED}


class UserProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per user account (1:1 with `users.id`), holding all
    user-supplied profile data plus the optional link to an imported
    `Alumni` record. Never confused with `Alumni` itself - a user can
    fill out this entire profile with zero connection to any imported
    row."""

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    # --- General ---
    profile_photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preferred_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_employer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_university: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Quick, top-level education summary fields - independent of (and
    # simpler than) the multi-entry `education_history` below. Never
    # required, since the imported CSV may only ever provide a single
    # free-text "Education" value.
    degree: Mapped[str | None] = mapped_column(String(255), nullable=True)
    field_of_study: Mapped[str | None] = mapped_column(String(255), nullable=True)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User-supplied industry (only deterministic source #1 for
    # `effective_industry` below - never guessed from `current_employer`).
    current_industry: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Contact & email preferences (private by default) ---
    primary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secondary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    personal_website: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- Personal information ---
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    pronouns: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hometown: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Social links ---
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    x_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    personal_website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- Speaking and mentoring ---
    available_to_speak: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    available_to_mentor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    speaker_topics: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    mentoring_topics: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    preferred_engagement_types: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Privacy (field-level visibility, enforced server-side - see
    # app.services.public_profile_service) ---
    show_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_birthday: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_location: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_current_employer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_job_title: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_education: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_linkedin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_social_links: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_work_history: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_education_history: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Alumni record linking ---
    # Deliberately independent of `users.alumni_id` (the existing,
    # admin-managed direct link used by /me/profile) - this is a
    # self-service, evidence-based, reviewable link that a CSV reimport
    # can never silently repoint or erase.
    alumni_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("alumni.id", ondelete="SET NULL"), nullable=True, index=True
    )
    link_status: Mapped[str] = mapped_column(String(32), default=LinkStatus.UNMATCHED, nullable=False, index=True)
    link_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "user" | "admin:<user_id>" - who confirmed the link.
    linked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON-serialized list of matched signals (e.g. ["email_exact",
    # "full_name_exact", "university_exact"]) - the audit trail behind
    # every automatic or user-confirmed link, never fabricated after
    # the fact.
    match_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- Effective-data cache (see app.services.effective_profile_service) ---
    # Recomputed every time the underlying profile fields change (never
    # per analytics query - this is what keeps the SQL effective-data
    # layer in app.services.effective_alumni_service a plain CASE/COALESCE
    # join instead of a per-row Python computation). Only ever consulted
    # for a CONFIRMED link, and only for fields the owner has not marked
    # private.
    effective_full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    effective_seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_seniority_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_career_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    effective_career_category_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    effective_industry_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Set (lazily, on read - see identity_matching_service.sync_link_review_status)
    # when a previously confirmed link's Alumni record has been
    # deactivated by a newer CSV replace-mode import. Never auto-resolved;
    # only an admin action (or the user unlinking) clears it.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user = relationship("User")
    alumni = relationship("Alumni")
    work_history = relationship(
        "UserWorkHistory", back_populates="profile", cascade="all, delete-orphan",
        order_by="UserWorkHistory.display_order",
    )
    education_history = relationship(
        "UserEducationHistory", back_populates="profile", cascade="all, delete-orphan",
        order_by="UserEducationHistory.display_order",
    )

    def __repr__(self) -> str:
        return f"<UserProfile user_id={self.user_id!r} link_status={self.link_status!r}>"


class UserWorkHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_work_history"

    user_profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employer: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    profile = relationship("UserProfile", back_populates="work_history")


class UserEducationHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_education_history"

    user_profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    # Deliberately optional: the imported CSV may only ever provide a
    # single free-text "Education" value, so a user profile must be
    # completable without degree/field_of_study/graduation_year.
    degree: Mapped[str | None] = mapped_column(String(255), nullable=True)
    field_of_study: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    profile = relationship("UserProfile", back_populates="education_history")


class ProfileMatchCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Persisted output of the matching engine
    (identity_matching_service.compute_match_candidates), so
    GET /profile/me/match-candidates and the admin review queue can
    show exactly what was evaluated - never recomputed silently behind
    the user's back between "find" and "confirm"."""

    __tablename__ = "profile_match_candidates"
    __table_args__ = (UniqueConstraint("user_profile_id", "alumni_id", name="uq_profile_match_candidate"),)

    user_profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alumni_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alumni.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "strong" (exact email/LinkedIn) or "standard" (name + >=2 signals).
    match_type: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_signals: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of strings
    # "candidate" | "confirmed" | "rejected"
    status: Mapped[str] = mapped_column(String(16), default="candidate", nullable=False)

    profile = relationship("UserProfile")
    alumni = relationship("Alumni")
