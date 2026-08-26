"""Company size classification and filtering utilities."""

import re
from typing import Optional


def classify_company_size(size_str: Optional[str]) -> str:
    """
    Classifies arbitrary company size strings into standard categories:
    'small' (1-200 employees, startups, boutique agencies)
    'medium' (201-1000 employees)
    'large' (1000+ employees, enterprise, corporations)
    'unknown' (unspecified)
    """
    if not size_str or not isinstance(size_str, str):
        return "unknown"

    raw = size_str.strip().lower()

    if raw in ("unspecified", "unknown", "n/a", "none", ""):
        return "unknown"

    # Explicit text patterns with word boundaries / clean matching
    if any(k in raw for k in [
        "startup", "early stage", "seed", "boutique", "small business", "small team", "micro"
    ]):
        return "small"

    if any(k in raw for k in [
        "enterprise", "corporate", "corporation", "multinational", "fortune 500", "large enterprise"
    ]):
        return "large"

    if any(k in raw for k in [
        "mid-size", "midsize", "medium size"
    ]):
        return "medium"

    # Plus ranges (e.g. 1000+, 500+, 200+)
    if re.search(r"\b(1000|5000|10000|50000|1,000|5,000|10,000|50,000)\s*\+", raw):
        return "large"

    # Exact standard ranges
    if re.search(r"\b(1\s*-\s*10|1\s*-\s*20|1\s*-\s*50|11\s*-\s*50|51\s*-\s*200|10\s*-\s*50|20\s*-\s*50)\b", raw):
        return "small"

    if re.search(r"\b(201\s*-\s*500|501\s*-\s*1000|201\s*-\s*1000|200\s*-\s*500|500\s*-\s*1000)\b", raw):
        return "medium"

    # Number parsing fallback (e.g. "45 employees", "150 people", "1500 staff")
    numbers = [int(n) for n in re.findall(r"\b\d+\b", raw.replace(",", ""))]
    if numbers:
        max_num = max(numbers)
        if max_num <= 200:
            return "small"
        elif max_num <= 1000:
            return "medium"
        else:
            return "large"

    if "small" in raw:
        return "small"
    if "medium" in raw:
        return "medium"
    if "large" in raw:
        return "large"

    return "unknown"


def is_matching_company_size(company_size: Optional[str], target_filter: str = "small") -> bool:
    """
    Checks if a company's size string matches the requested target filter.
    target_filter options:
    - 'small': <= 200 employees, startups (and gives benefit of doubt to unknown/unspecified early stage companies)
    - 'medium': 201-1000 employees
    - 'large': 1000+ employees
    - 'all': matches all sizes
    """
    if not target_filter or target_filter.lower() in ("all", "any"):
        return True

    filter_normalized = target_filter.lower().strip()
    classified = classify_company_size(company_size)

    if filter_normalized == "small":
        # Match 'small' and also 'unknown' (to avoid dropping stealth startups with unlisted headcount)
        return classified in ("small", "unknown")

    if filter_normalized == "medium":
        return classified in ("medium", "unknown")

    if filter_normalized == "large":
        return classified in ("large",)

    return True
