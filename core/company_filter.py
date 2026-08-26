"""Company size and job type classification and filtering utilities."""

import re
from typing import Optional, Tuple


def classify_company_size(size_str: Optional[str]) -> str:
    """
    Classifies arbitrary company size strings into standard categories:
    'small' (1-50 employees: 1-10, 11-50, seed startups, boutique agencies, micro-businesses - MAX 50)
    'medium' (51-500 employees: 51-200, 201-500, mid-size companies, scaling businesses)
    'large' (500+ / 1000+ employees: enterprise, corporations)
    'unknown' (unspecified)
    """
    if not size_str or not isinstance(size_str, str):
        return "unknown"

    raw = size_str.strip().lower()

    if raw in ("unspecified", "unknown", "n/a", "none", ""):
        return "unknown"

    # Enterprise / large keywords & plus ranges
    if any(k in raw for k in [
        "enterprise", "corporate", "corporation", "multinational", "fortune 500", "large enterprise"
    ]):
        return "large"

    if re.search(r"\b(500|1000|5000|10000|50000|1,000|5,000|10,000|50,000)\s*\+", raw):
        return "large"

    # Medium standard ranges (51-200, 201-500, 501-1000)
    if re.search(r"\b(51\s*-\s*200|201\s*-\s*500|501\s*-\s*1000|201\s*-\s*1000|200\s*-\s*500|500\s*-\s*1000|51\s*-\s*500)\b", raw):
        return "medium"

    # Small standard ranges (<= 50)
    if re.search(r"\b(1\s*-\s*10|1\s*-\s*20|1\s*-\s*50|11\s*-\s*50|10\s*-\s*50|20\s*-\s*50|1\s*-\s*5)\b", raw):
        return "small"

    # Keywords for small
    if any(k in raw for k in [
        "seed", "early stage", "boutique", "micro", "small business", "small team"
    ]):
        return "small"

    # Keywords for medium
    if any(k in raw for k in ["mid-size", "midsize", "medium"]):
        return "medium"

    # Number parsing fallback (e.g. "45 employees", "150 people", "1500 staff")
    numbers = [int(n) for n in re.findall(r"\b\d+\b", raw.replace(",", ""))]
    if numbers:
        max_num = max(numbers)
        if max_num <= 50:
            return "small"
        elif max_num <= 500:
            return "medium"
        else:
            return "large"

    if "startup" in raw:
        return "small"
    if "small" in raw:
        return "small"
    if "large" in raw:
        return "large"

    return "unknown"


def is_matching_company_size(company_size: Optional[str], target_filter: str = "small") -> bool:
    """
    Checks if a company's size string matches the requested target filter.
    target_filter options:
    - 'small': <= 50 employees (1-10, 11-50) (gives benefit of doubt to unknown/unspecified early stage startups)
    - 'medium': 51-500 employees (51-200, 201-500)
    - 'large': 500+ employees
    - 'all': matches all sizes
    """
    if not target_filter or target_filter.lower() in ("all", "any"):
        return True

    filter_normalized = target_filter.lower().strip()
    classified = classify_company_size(company_size)

    if filter_normalized == "small":
        # Strictly matches small (<= 50) and unknown
        return classified in ("small", "unknown")

    if filter_normalized == "medium":
        return classified in ("medium", "unknown")

    if filter_normalized == "large":
        return classified in ("large",)

    return True


def detect_job_type_filter(search_term: str, explicit_job_type: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    Analyzes search term and explicit job type parameter to determine:
    1. The jobspy job_type parameter ('contract', 'fulltime', 'parttime', 'internship', or None)
    2. Cleaned / augmented search term for optimal job board results.
    """
    if explicit_job_type and explicit_job_type.lower() not in ("all", "any", "none", ""):
        return explicit_job_type.lower().strip(), search_term

    term_lower = search_term.lower()

    # Detect freelance / contract keywords
    freelance_keywords = ["freelance", "freelancer", "freelancing", "contract", "contractor", "outside ir35", "c2c", "corp-to-corp", "gig"]
    if any(k in term_lower for k in freelance_keywords):
        return "contract", search_term

    if "part-time" in term_lower or "part time" in term_lower:
        return "parttime", search_term

    if "internship" in term_lower or "intern " in term_lower:
        return "internship", search_term

    if "full-time" in term_lower or "full time" in term_lower:
        return "fulltime", search_term

    return None, search_term
