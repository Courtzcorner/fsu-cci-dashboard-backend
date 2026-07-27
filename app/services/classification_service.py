"""
Deterministic classification rules for `seniority`, `career_category`, and
`industry`.

Nothing here is an AI/ML guess. Every derived value comes from an explicit,
documented, ordered rule list matched against the job title (or, for
industry, a verified company mapping) - the same input always produces the
same output. Every value is tagged with a `*_source` column so downstream
consumers (analytics, exports) can always tell confirmed CSV data from
backend-derived data:

- "imported"        - came directly from a nonblank CSV column
- "derived:title_rules" - matched one of the documented keyword rules below
- "company_mapping" - matched a verified Company.industry mapping
- "unknown"         - no rule matched; the value is null/"Unclassified",
                       never guessed
"""
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.reference import Company
from app.models.roles import DataSource

SENIORITY_SOURCE = "derived:title_rules"
CAREER_CATEGORY_SOURCE = "derived:title_rules"
COMPANY_MAPPING_SOURCE = "company_mapping"

UNCLASSIFIED = "Unclassified"

# Ordered MOST SPECIFIC FIRST. A title is classified by the first rule it
# matches, so "Senior Vice President of Engineering" resolves to "Vice
# President" (checked before the generic "senior" rule), and "Chief
# Marketing Officer" resolves to "Executive" (checked before "manager" /
# "director", even though "officer" titles often also contain those words).
SENIORITY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Executive", ("chief", "ceo", "cto", "cio", "cmo", "coo", "cfo", "founder")),
    ("Vice President", ("vice president", "vp")),
    ("Director", ("director", "head")),
    ("Manager", ("manager",)),
    ("Lead", ("lead", "principal")),
    ("Senior", ("senior", "sr")),
    ("Associate", ("associate",)),
    ("Entry", ("assistant", "coordinator")),
    ("Intern", ("intern",)),
]

# Ordered; documented keyword -> career category. First match wins.
CAREER_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Engineering", ("engineer", "developer", "engineering")),
    ("Product Management", ("product manager", "product owner", "product")),
    ("Data & Analytics", ("data scientist", "data analyst", "data engineer", "analyst", "data")),
    ("Design", ("designer", "ux", "ui", "design")),
    ("Marketing", ("marketing",)),
    ("Communications", ("communications",)),
    ("Public Relations", ("public relations", "pr")),
    ("Sales", ("sales", "account manager", "account executive")),
    ("Finance", ("finance", "accounting", "accountant", "financial")),
    ("Human Resources", ("human resources", "hr", "recruiter")),
    ("Editorial", ("editor", "journalist", "reporter", "writer")),
    ("Education", ("teacher", "professor", "instructor", "educator")),
    ("Operations", ("operations",)),
    ("Legal", ("attorney", "counsel", "paralegal", "legal")),
    ("Consulting", ("consultant", "consulting")),
]


@dataclass
class ClassificationResult:
    value: Optional[str]
    source: str


def _keyword_pattern(keyword: str) -> "re.Pattern":
    # \b word-boundary matching so short keywords like "vp" or "sr" never
    # match inside a longer, unrelated word (e.g. "coo" inside
    # "coordinator", or "lead" inside "leadership development").
    escaped = re.escape(keyword.strip())
    return re.compile(rf"\b{escaped}\b")


def _first_matching_rule(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> Optional[str]:
    lowered = text.strip().lower()
    for label, keywords in rules:
        if any(_keyword_pattern(keyword).search(lowered) for keyword in keywords):
            return label
    return None


def derive_seniority(job_title: Optional[str]) -> ClassificationResult:
    """Deterministic title -> seniority mapping. See SENIORITY_RULES for
    the full documented rule order. Never guesses: an unmatched title
    always resolves to (None, "unknown")."""
    if not job_title or not job_title.strip():
        return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)
    match = _first_matching_rule(job_title, SENIORITY_RULES)
    if match:
        return ClassificationResult(value=match, source=SENIORITY_SOURCE)
    return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)


def derive_career_category(job_title: Optional[str]) -> ClassificationResult:
    """Deterministic title -> career category mapping. See
    CAREER_CATEGORY_RULES for the full documented rule order."""
    if not job_title or not job_title.strip():
        return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)
    match = _first_matching_rule(job_title, CAREER_CATEGORY_RULES)
    if match:
        return ClassificationResult(value=match, source=CAREER_CATEGORY_SOURCE)
    return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)


def build_company_industry_map(db: Session, organization_id: str) -> dict[str, str]:
    """Preloads the verified company -> industry mapping ONCE per import
    (not once per row) to avoid an N+1 query pattern against `companies`.
    Only companies with an explicitly assigned `industry` value (never an
    AI guess - see app.models.reference.Company.industry) are included.
    """
    rows = (
        db.query(Company.name, Company.industry)
        .filter(Company.organization_id == organization_id, Company.industry.isnot(None))
        .all()
    )
    return {name.strip().lower(): industry for name, industry in rows if name and industry}


def resolve_industry(
    company: Optional[str],
    imported_industry: Optional[str],
    company_industry_map: Optional[dict[str, str]] = None,
) -> ClassificationResult:
    """Resolve industry using ONLY deterministic sources, in priority
    order - industry is NEVER guessed from a company name via keywords or
    an AI model:

    1. A nonblank imported "Industry" CSV column for this row.
    2. A verified company -> industry mapping (Company.industry) for this
       organization, matched case-insensitively by company name.
    3. Unclassified (None, source="unknown").
    """
    if imported_industry and imported_industry.strip():
        return ClassificationResult(value=imported_industry.strip(), source=DataSource.IMPORTED.value)

    if company and company_industry_map:
        mapped = company_industry_map.get(company.strip().lower())
        if mapped:
            return ClassificationResult(value=mapped, source=COMPANY_MAPPING_SOURCE)

    return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)


def classify_alumni_fields(
    job_title: Optional[str],
    company: Optional[str],
    existing_industry: Optional[str],
    existing_career_category: Optional[str],
    existing_seniority: Optional[str],
    company_industry_map: Optional[dict[str, str]] = None,
) -> dict:
    """Returns a dict with industry/career_category/seniority + their
    *_source fields. Imported (nonblank CSV) values always win; only a
    genuinely blank field is ever derived, and only via the documented
    deterministic rules above - never fabricated.
    """
    result: dict = {}

    industry_result = resolve_industry(company, existing_industry, company_industry_map)
    result["industry"] = industry_result.value
    result["industry_source"] = industry_result.source

    if existing_career_category and existing_career_category.strip():
        result["career_category"] = existing_career_category.strip()
        result["career_category_source"] = DataSource.IMPORTED.value
    else:
        career_result = derive_career_category(job_title)
        result["career_category"] = career_result.value
        result["career_category_source"] = career_result.source

    if existing_seniority and existing_seniority.strip():
        result["seniority"] = existing_seniority.strip()
        result["seniority_source"] = DataSource.IMPORTED.value
    else:
        seniority_result = derive_seniority(job_title)
        result["seniority"] = seniority_result.value
        result["seniority_source"] = seniority_result.source

    return result
