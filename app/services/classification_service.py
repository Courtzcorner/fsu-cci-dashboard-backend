"""
Optional classification inference for industry / career_category / seniority
when those fields are blank in the imported spreadsheet.

Imported values always take priority and are tagged `imported`. Inference
here is a best-effort fallback tagged `inferred` so downstream consumers
(especially analytics) can distinguish confirmed data from guesses. The
frontend may show inferred values as a temporary fallback, but this service
- and the `*_source` columns it sets - is the backend's official record of
provenance.
"""
from dataclasses import dataclass
from typing import Optional

from app.models.roles import DataSource

SENIORITY_KEYWORDS: list[tuple[str, str]] = [
    ("chief", "Executive"), ("ceo", "Executive"), ("cfo", "Executive"), ("cto", "Executive"),
    ("founder", "Executive"), ("president", "Executive"), ("vp", "Vice President"),
    ("vice president", "Vice President"), ("director", "Director"), ("head of", "Director"),
    ("senior manager", "Manager"), ("manager", "Manager"), ("lead", "Senior"),
    ("senior", "Senior"), ("principal", "Senior"), ("staff", "Senior"),
    ("associate", "Associate"), ("junior", "Entry Level"), ("intern", "Entry Level"),
    ("coordinator", "Entry Level"), ("assistant", "Entry Level"),
]

INDUSTRY_KEYWORDS: list[tuple[str, str]] = [
    ("bank", "Financial Services"), ("financ", "Financial Services"), ("capital", "Financial Services"),
    ("software", "Technology"), ("technology", "Technology"), ("tech", "Technology"),
    ("media", "Media & Entertainment"), ("broadcast", "Media & Entertainment"),
    ("news", "Media & Entertainment"), ("entertainment", "Media & Entertainment"),
    ("hospital", "Healthcare"), ("health", "Healthcare"), ("pharma", "Healthcare"),
    ("university", "Education"), ("school", "Education"), ("education", "Education"),
    ("government", "Government"), ("public sector", "Government"),
    ("consult", "Consulting"), ("nonprofit", "Nonprofit"), ("non-profit", "Nonprofit"),
    ("retail", "Retail"), ("manufactur", "Manufacturing"), ("marketing", "Marketing & Advertising"),
    ("advertis", "Marketing & Advertising"), ("real estate", "Real Estate"),
]

CAREER_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("engineer", "Engineering"), ("developer", "Engineering"), ("product manager", "Product Management"),
    ("product", "Product Management"), ("marketing", "Marketing"), ("communications", "Communications"),
    ("pr ", "Public Relations"), ("public relations", "Public Relations"), ("sales", "Sales"),
    ("account manager", "Sales"), ("finance", "Finance"), ("accounting", "Finance"),
    ("human resources", "Human Resources"), ("hr ", "Human Resources"), ("design", "Design"),
    ("editor", "Editorial"), ("journalist", "Editorial"), ("reporter", "Editorial"),
    ("teacher", "Education"), ("professor", "Education"), ("operations", "Operations"),
    ("data", "Data & Analytics"), ("analyst", "Data & Analytics"),
]


@dataclass
class ClassificationResult:
    value: Optional[str]
    source: str


def _match_keywords(text: str, keywords: list[tuple[str, str]]) -> Optional[str]:
    lowered = text.lower()
    for keyword, label in keywords:
        if keyword in lowered:
            return label
    return None


def infer_seniority(job_title: Optional[str]) -> ClassificationResult:
    if not job_title:
        return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)
    match = _match_keywords(job_title, SENIORITY_KEYWORDS)
    if match:
        return ClassificationResult(value=match, source=DataSource.INFERRED.value)
    return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)


def infer_industry(company: Optional[str], job_title: Optional[str]) -> ClassificationResult:
    combined = " ".join(filter(None, [company, job_title]))
    if not combined.strip():
        return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)
    match = _match_keywords(combined, INDUSTRY_KEYWORDS)
    if match:
        return ClassificationResult(value=match, source=DataSource.INFERRED.value)
    return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)


def infer_career_category(job_title: Optional[str]) -> ClassificationResult:
    if not job_title:
        return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)
    match = _match_keywords(job_title, CAREER_CATEGORY_KEYWORDS)
    if match:
        return ClassificationResult(value=match, source=DataSource.INFERRED.value)
    return ClassificationResult(value=None, source=DataSource.UNKNOWN.value)


def classify_alumni_fields(
    job_title: Optional[str],
    company: Optional[str],
    existing_industry: Optional[str],
    existing_career_category: Optional[str],
    existing_seniority: Optional[str],
) -> dict:
    """Returns a dict with industry/career_category/seniority + their
    *_source fields, only inferring for fields that arrived blank.
    """
    result: dict = {}

    if existing_industry:
        result["industry"] = existing_industry
        result["industry_source"] = DataSource.IMPORTED.value
    else:
        inferred = infer_industry(company, job_title)
        result["industry"] = inferred.value
        result["industry_source"] = inferred.source

    if existing_career_category:
        result["career_category"] = existing_career_category
        result["career_category_source"] = DataSource.IMPORTED.value
    else:
        inferred = infer_career_category(job_title)
        result["career_category"] = inferred.value
        result["career_category_source"] = inferred.source

    if existing_seniority:
        result["seniority"] = existing_seniority
        result["seniority_source"] = DataSource.IMPORTED.value
    else:
        inferred = infer_seniority(job_title)
        result["seniority"] = inferred.value
        result["seniority_source"] = inferred.source

    return result
