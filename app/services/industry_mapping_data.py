"""
CURATED, HUMAN-REVIEWED company -> industry mappings for the industry
classification backfill (see app.services.industry_backfill_service and
scripts/backfill_company_industry.py). This module holds ONLY data - no
functions, no imports, no side effects - so it can be reviewed as a plain
diff by a human without reading any logic.

STRICT GUARDRAILS (approved Phase 1 scope - do not violate without a new
human review pass):
  - Every mapping below was individually reviewed and approved. Do NOT
    add a new company here without presenting it for human review first.
  - Matching is ALWAYS normalized-exact-string, NEVER substring/keyword/
    contains. "Capital" must never match "Capital One"; "Florida State"
    must never match "Florida State University" unless a reviewer
    explicitly adds it as its own alias below.
  - No AI, no live web lookups, no probabilistic scoring - a plain
    Python dict lookup, nothing else (see
    app.services.industry_backfill_service.resolve_curated_industry).
  - An alias is a short-hand a person might type for the same employer
    (e.g. "FSU" for "Florida State University") - it must resolve to the
    SAME industry as the canonical name, never a different one.
  - A short alias (<= SHORT_ALIAS_MAX_LENGTH characters) is dangerous
    (e.g. "FS", "TM", "CO" could mean almost anything) and is REJECTED by
    app.services.industry_backfill_service.validate_industry_mapping_data()
    unless explicitly present in ALLOWED_SHORT_ALIASES below - reviewed
    one at a time, exactly like a mapping.
  - Blocked employer/status values (e.g. "Full-time", "Student") are
    never classified as a company, regardless of casing/punctuation -
    see BLOCKED_EMPLOYER_VALUES.

Normalization applied before every lookup (see
app.services.industry_backfill_service.normalize_company_name):
  case-fold -> strip surrounding whitespace -> strip a fixed punctuation
  set -> collapse repeated internal whitespace -> strip a single
  trailing corporate suffix word (Inc/LLC/Corp/...) -> alias substitution
  (exact match only). All canonical keys below are already written in
  that normalized form.
"""

# Global, reviewed defaults - apply to every organization unless a more
# specific organization override below exists for the same normalized
# name (see ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES). Keys are already
# normalized (lowercase, no punctuation, no corporate suffix).
GLOBAL_DEFAULT_COMPANY_INDUSTRY: dict[str, str] = {
    "capital one": "Financial Services",
    "florida state university": "Education",
    "tallahassee memorial healthcare": "Healthcare",
    "deloitte": "Consulting",
    "microsoft": "Technology",
}

# Organization-slug-scoped overrides/additions - reviewed the same way as
# the global defaults above, but only ever applied for that one
# organization; never affects any other organization's classification.
# Empty in Phase 1 - no organization-specific mappings have been reviewed
# yet.
ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES: dict[str, dict[str, str]] = {}

# Exact-match-only aliases -> canonical (already-normalized) company key
# from GLOBAL_DEFAULT_COMPANY_INDUSTRY or ORGANIZATION_COMPANY_INDUSTRY_OVERRIDES
# above. Never a substring/prefix/suffix match - "florida state" (without
# "university") is deliberately NOT an entry here and must remain
# unclassified unless a reviewer explicitly adds it.
APPROVED_COMPANY_ALIASES: dict[str, str] = {
    "fsu": "florida state university",
    "tmh": "tallahassee memorial healthcare",
    "tallahassee memorial hospital": "tallahassee memorial healthcare",
}

# An alias at or below this length is considered dangerously short and is
# rejected by validate_industry_mapping_data() unless explicitly reviewed
# and added to ALLOWED_SHORT_ALIASES below (a generic 2-3 letter string
# like "FS", "TM", or "CO" could plausibly mean almost any employer).
SHORT_ALIAS_MAX_LENGTH = 3
ALLOWED_SHORT_ALIASES: frozenset = frozenset({"fsu", "tmh"})

# Never classified as a company/employer, regardless of casing or
# punctuation - these are employment-STATUS values, not employers.
# "Self-employed" is deliberately included: rejected unless a future
# reviewer adds an explicit, reviewed mapping for it.
BLOCKED_EMPLOYER_VALUES: frozenset = frozenset({
    "Full-time", "Part-time", "Student", "Unemployed", "Not employed",
    "N/A", "Unknown", "None", "Self-employed",
})
