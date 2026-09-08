"""SMTP & DNS MX Email Deliverability Verifier for EU Startups.

Re-exports and wraps the canonical verifier in `core.email_verifier` to eliminate code duplication.
"""

from core.email_verifier import (
    EmailVerifier,
    email_verifier,
    check_domain_mx,
    verify_email_smtp,
    verify_email,
)

__all__ = [
    "EmailVerifier",
    "email_verifier",
    "check_domain_mx",
    "verify_email_smtp",
    "verify_email",
]
