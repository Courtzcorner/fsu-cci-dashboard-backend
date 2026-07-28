"""
Deterministic identity matching between a self-service UserProfile and
the active (imported, `is_active=True`) Alumni dataset.

This module is READ-ONLY with respect to the CSV import pipeline: it
never writes to `Alumni`, never touches `csv_import_service`, and only
ever compares already-imported, already-active rows against
user-supplied profile data. No AI/fuzzy inference is used for automatic
linking - every signal is an exact match on a normalized value, and
every score is fully explained by `matched_signals`.

MATCH POLICY
------------
A candidate QUALIFIES for user confirmation if either:
  - STRONG: normalized email exact match, OR normalized LinkedIn URL
    exact match, by itself. (Still never auto-linked - a strong match is
    just presented on the "Is this you?" confirmation screen like any
    other candidate.)
  - STANDARD: normalized full name matches AND at least two of
    {university, employer, job_title, city_state, email, LinkedIn,
    graduation_year} also match exactly.

Three or more non-name fields matching, without a name match, is never
enough by itself (prevents "two people at the same employer and
university" false links). Nothing here is ever auto-linked without an
explicit POST /profile/me/confirm-match/{alumni_id} call.

SCORING (documented, not used for automatic linking beyond the policy
above - purely for ranking/transparency):
  email exact            100
  LinkedIn exact         100
  full name exact         40
  university exact        20
  employer exact           20
  job title exact          15
  city+state exact         10
  graduation year exact    10
"""
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.models.alumni import Alumni
from app.models.user_profile import UserProfile
from app.services.us_geography import US_STATE_NAME_TO_CODE

SCORE_EMAIL_EXACT = 100
SCORE_LINKEDIN_EXACT = 100
SCORE_FULL_NAME_EXACT = 40
SCORE_UNIVERSITY_EXACT = 20
SCORE_EMPLOYER_EXACT = 20
SCORE_JOB_TITLE_EXACT = 15
SCORE_CITY_STATE_EXACT = 10
SCORE_GRADUATION_YEAR_EXACT = 10

# Every signal that can count toward the "at least two of" standard-match
# requirement. Email/LinkedIn count here too (in addition to qualifying a
# candidate on their own as a "strong" match) - see MATCH_POLICY above.
STANDARD_SIGNAL_FIELDS = (
    "university_exact", "employer_exact", "job_title_exact", "city_state_exact",
    "email_exact", "linkedin_exact", "graduation_year_exact",
)
MIN_STANDARD_SIGNALS_REQUIRED = 2

MATCH_TYPE_STRONG = "strong"
MATCH_TYPE_STANDARD = "standard"

# Human-readable field names for MatchCandidateOut.matched_fields /
# nonmatching_fields - purely presentational, never used for scoring.
SIGNAL_DISPLAY_NAMES = {
    "email_exact": "email",
    "linkedin_exact": "linkedin_url",
    "full_name_exact": "full_name",
    "university_exact": "university",
    "employer_exact": "current_employer",
    "job_title_exact": "job_title",
    "city_state_exact": "location",
    "graduation_year_exact": "graduation_year",
}
ALL_DISPLAY_FIELDS = tuple(SIGNAL_DISPLAY_NAMES.values())

_WHITESPACE_RE = re.compile(r"\s+")
_NAME_PUNCTUATION_RE = re.compile(r"[.,'\u2019`]")
_CORP_SUFFIX_RE = re.compile(
    r"\s*[,]?\s*\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited)\.?\s*$"
)


def normalize_name(value: Optional[str]) -> Optional[str]:
    """trim, lowercase, collapse whitespace, remove harmless punctuation."""
    if not value or not value.strip():
        return None
    cleaned = _NAME_PUNCTUATION_RE.sub("", value.strip().lower())
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned or None


def normalize_email(value: Optional[str]) -> Optional[str]:
    if not value or not value.strip():
        return None
    return value.strip().lower()


def normalize_linkedin_url(value: Optional[str]) -> Optional[str]:
    """Normalizes scheme, host (www./no www., linkedin.com only),
    trailing slash, and strips query parameters so
    "https://www.linkedin.com/in/jordan-lee/?trk=x" and
    "linkedin.com/in/jordan-lee" normalize identically."""
    if not value or not value.strip():
        return None
    raw = value.strip()
    if "//" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    if not host:
        return None
    return f"{host}{path}"


def normalize_company(value: Optional[str]) -> Optional[str]:
    """trim, lowercase, normalize whitespace, and strip a trailing
    corporate suffix (Inc/LLC/Corp/...) ONLY when it is safely
    separable at the end of the string - never altering the rest of the
    name."""
    if not value or not value.strip():
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.strip().lower())
    cleaned = _CORP_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned or None


def normalize_job_title(value: Optional[str]) -> Optional[str]:
    if not value or not value.strip():
        return None
    cleaned = _NAME_PUNCTUATION_RE.sub("", value.strip().lower())
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned or None


def normalize_university(value: Optional[str]) -> Optional[str]:
    if not value or not value.strip():
        return None
    return _WHITESPACE_RE.sub(" ", value.strip().lower()).strip() or None


def normalize_state(value: Optional[str]) -> Optional[str]:
    """Always compares on the 2-letter state code so "FL", "Florida",
    and "florida" are all treated as the same state."""
    if not value or not value.strip():
        return None
    cleaned = value.strip().lower()
    if len(cleaned) == 2:
        return cleaned.upper()
    code = US_STATE_NAME_TO_CODE.get(cleaned)
    return code.upper() if code else cleaned


def normalize_city_state(city: Optional[str], state: Optional[str]) -> Optional[str]:
    norm_city = _WHITESPACE_RE.sub(" ", city.strip().lower()).strip() if city and city.strip() else None
    norm_state = normalize_state(state)
    if not norm_city or not norm_state:
        return None
    return f"{norm_city}|{norm_state}"


@dataclass
class MatchCandidate:
    alumni: Alumni
    score: int
    matched_signals: list[str] = field(default_factory=list)
    match_type: Optional[str] = None  # "strong" | "standard" | None (non-qualifying)

    @property
    def qualifies(self) -> bool:
        return self.match_type in (MATCH_TYPE_STRONG, MATCH_TYPE_STANDARD)


def _profile_full_name(profile: UserProfile) -> Optional[str]:
    first = profile.first_name
    last = profile.last_name
    if not first and not last:
        return None
    return normalize_name(f"{first or ''} {last or ''}")


def score_candidate(profile: UserProfile, alumni: Alumni) -> MatchCandidate:
    signals: list[str] = []
    score = 0

    profile_email = normalize_email(profile.primary_email) or normalize_email(profile.secondary_email)
    alumni_email = normalize_email(alumni.email)
    email_match = bool(profile_email and alumni_email and profile_email == alumni_email)
    if email_match:
        signals.append("email_exact")
        score += SCORE_EMAIL_EXACT

    profile_linkedin = normalize_linkedin_url(profile.linkedin_url)
    alumni_linkedin = normalize_linkedin_url(alumni.linkedin_url)
    linkedin_match = bool(profile_linkedin and alumni_linkedin and profile_linkedin == alumni_linkedin)
    if linkedin_match:
        signals.append("linkedin_exact")
        score += SCORE_LINKEDIN_EXACT

    profile_name = _profile_full_name(profile)
    alumni_name = normalize_name(alumni.full_name)
    name_match = bool(profile_name and alumni_name and profile_name == alumni_name)
    if name_match:
        signals.append("full_name_exact")
        score += SCORE_FULL_NAME_EXACT

    profile_university = normalize_university(profile.current_university)
    alumni_university = normalize_university(alumni.university)
    university_match = bool(profile_university and alumni_university and profile_university == alumni_university)
    if university_match:
        signals.append("university_exact")
        score += SCORE_UNIVERSITY_EXACT

    profile_employer = normalize_company(profile.current_employer)
    alumni_employer = normalize_company(alumni.company)
    employer_match = bool(profile_employer and alumni_employer and profile_employer == alumni_employer)
    if employer_match:
        signals.append("employer_exact")
        score += SCORE_EMPLOYER_EXACT

    profile_title = normalize_job_title(profile.current_job_title)
    alumni_title = normalize_job_title(alumni.job_title)
    title_match = bool(profile_title and alumni_title and profile_title == alumni_title)
    if title_match:
        signals.append("job_title_exact")
        score += SCORE_JOB_TITLE_EXACT

    profile_city_state = normalize_city_state(profile.current_city, profile.current_state)
    alumni_city_state = normalize_city_state(alumni.city, alumni.state_code or alumni.state)
    city_state_match = bool(profile_city_state and alumni_city_state and profile_city_state == alumni_city_state)
    if city_state_match:
        signals.append("city_state_exact")
        score += SCORE_CITY_STATE_EXACT

    grad_year_match = bool(
        profile.graduation_year and alumni.graduation_year and profile.graduation_year == alumni.graduation_year
    )
    if grad_year_match:
        signals.append("graduation_year_exact")
        score += SCORE_GRADUATION_YEAR_EXACT

    match_type = None
    if email_match or linkedin_match:
        match_type = MATCH_TYPE_STRONG
    elif name_match:
        # Email/LinkedIn also count toward the "at least two" standard
        # requirement (in addition to qualifying alone as "strong") -
        # they just didn't happen to match exactly in this branch.
        standard_signal_count = sum(
            [university_match, employer_match, title_match, city_state_match, grad_year_match]
        )
        if standard_signal_count >= MIN_STANDARD_SIGNALS_REQUIRED:
            match_type = MATCH_TYPE_STANDARD

    return MatchCandidate(alumni=alumni, score=score, matched_signals=signals, match_type=match_type)


def matched_field_names(matched_signals: list[str]) -> list[str]:
    return [SIGNAL_DISPLAY_NAMES[s] for s in matched_signals if s in SIGNAL_DISPLAY_NAMES]


def nonmatching_field_names(matched_signals: list[str]) -> list[str]:
    matched = set(matched_field_names(matched_signals))
    return [name for name in ALL_DISPLAY_FIELDS if name not in matched]


def compute_match_candidates(db: Session, profile: UserProfile) -> list[MatchCandidate]:
    """Scores every ACTIVE alumni record against this profile and
    returns only the QUALIFYING candidates (strong or standard per the
    policy above), sorted by score descending. Never considers inactive
    (superseded-by-reimport) alumni rows."""
    active_alumni = db.query(Alumni).filter(Alumni.is_active.is_(True)).all()
    candidates = [score_candidate(profile, alumni) for alumni in active_alumni]
    qualifying = [c for c in candidates if c.qualifies]
    qualifying.sort(key=lambda c: c.score, reverse=True)
    return qualifying
