"""
Template Engine — resolves {{variable}} placeholders in email subjects and bodies.

Supported variables:
  {{founder_name}}    - person's name from the people table
  {{first_name}}      - first name only
  {{role}}            - founder's role (e.g. CEO, Founder)
  {{company_name}}    - company name
  {{company_website}} - company website URL
  {{city}}            - company city
  {{country}}         - company country
  {{category}}        - startup category
  {{sender_name}}     - from_name in SMTP config (the outreach sender)
  {{email}}           - recipient email (for reference in body)
"""

import re
from typing import Dict, Optional


AVAILABLE_VARIABLES = [
    ("{{name}}",            "Recipient's name"),
    ("{{company_name}}",    "Company name"),
]


def resolve_variables(subject: str, body: str, context: Dict[str, str]) -> tuple[str, str]:
    """Replace all {{variable}} placeholders with context values."""
    for key, value in context.items():
        placeholder = f"{{{{{key}}}}}"
        safe_val = str(value or "").strip()
        subject = subject.replace(placeholder, safe_val)
        body = body.replace(placeholder, safe_val)
    return subject, body


def build_context(
    person_name: Optional[str] = None,
    role: Optional[str] = None,
    company_name: Optional[str] = None,
    website: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sender_name: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, str]:
    """Build the variable resolution context for a single recipient."""
    first_name = ""
    if person_name:
        parts = person_name.strip().split()
        first_name = parts[0] if parts else person_name

    # Determine best available name (first name preferred, full name fallback)
    best_name = first_name or person_name or ""

    return {
        "name":            best_name,
        "first_name":      first_name or best_name,
        "founder_name":    person_name or best_name,
        "role":            role or "",
        "company_name":    company_name or "",
        "company_website": website or "",
        "city":            city or "",
        "country":         country or "",
        "category":        category or "",
        "sender_name":     sender_name or "LeadPulse AI",
        "email":           email or "",
    }


def get_sample_context(sender_name: str = "Your Name") -> Dict[str, str]:
    """Returns sample context for template preview rendering."""
    return {
        "name":            "Jennifer",
        "first_name":      "Jennifer",
        "founder_name":    "Jennifer Sharman",
        "role":            "Co-founder & CEO",
        "company_name":    "Linxei Ltd",
        "company_website": "https://linxei.com",
        "city":            "London",
        "country":         "United Kingdom",
        "category":        "AI & SaaS",
        "sender_name":     sender_name,
        "email":           "jennifer.sharman@linxei.com",
    }
