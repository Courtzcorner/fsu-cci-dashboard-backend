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
#
# Note on "the walt disney": "The Walt Disney Company" and the approved
# alias "The Walt Disney" both already normalize to this exact same key
# via the existing trailing-corporate-suffix strip ("Company" is a
# recognized suffix word - see
# app.services.industry_backfill_service.normalize_company_name), so no
# separate APPROVED_COMPANY_ALIASES entry is needed for that alias. Bare
# "Disney" normalizes to "disney" and deliberately does NOT match.
#
# Note on "eli lilly and": the reviewed canonical name "Eli Lilly and
# Company" is forced by the SAME pre-existing trailing-corporate-suffix
# strip to normalize down to "eli lilly and" ("Company" - and, for that
# matter, "Co" - is a recognized suffix word), so that is the only
# normalized form that can ever match it; there is no other normalized
# key this canonical name could use. This is not a new alias - it is
# simply that canonical name written in its already-normalized form,
# same convention as every other key below. A documented, deterministic
# consequence: literal employer text "Eli Lilly and" (without "Company")
# normalizes to the identical string and therefore also matches - this
# was investigated and is an accepted, safe, fully deterministic result
# of the existing normalization design, not a new keyword/substring rule.
#
# Note on Amazon: "Amazon" and "Amazon Web Services (AWS)" are kept as
# two fully separate, explicit canonical entries (not an alias pair) -
# they normalize to different keys ("amazon" vs.
# "amazon web services (aws)") under the existing normalization, so they
# would never merge on their own; keeping both explicit avoids any
# ambiguity. "Amazon Web Services" (without "(AWS)") normalizes to yet a
# third, distinct key ("amazon web services") and is deliberately NOT
# mapped - it remains unknown unless a reviewer explicitly adds it.
#
# Note on "c is": deliberately NOT added - too ambiguous to review
# confidently - so it remains unclassified.
GLOBAL_DEFAULT_COMPANY_INDUSTRY: dict[str, str] = {
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
# "Self-employed" and "Self employed" (hyphen vs. space - these
# normalize to two DIFFERENT strings since the existing punctuation strip
# does not touch hyphens) are both deliberately included: rejected unless
# a future reviewer adds an explicit, reviewed mapping for one of them.
BLOCKED_EMPLOYER_VALUES: frozenset = frozenset({
    "Full-time", "Part-time", "Student", "Unemployed", "Not employed",
    "N/A", "Unknown", "None", "Self-employed",
    "Not stated", "Not specified", "Freelance", "Self employed",
})
