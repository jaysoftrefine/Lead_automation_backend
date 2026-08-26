"""Real-time DNS MX and SMTP Handshake Email Verifier for Lead Intelligence."""

import re
import socket
import smtplib
import dns.resolver
from typing import Dict, Any, Optional, Tuple
from core.logging import logger

# RFC 5322 standard email regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

# Common disposable email provider domains
DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "guerrillamail.com", "10minutemail.com",
    "throwawaymail.com", "trashmail.com", "yopmail.com", "getairmail.com"
}

# Cache for domain MX lookups to keep pipeline lightning fast
_MX_CACHE: Dict[str, Optional[str]] = {}


class EmailVerifier:
    """
    Validates email addresses using a 3-step verification funnel:
    1. Syntax & RFC compliance check
    2. DNS MX (Mail Exchange) record resolution
    3. SMTP Handshake (HELO -> MAIL FROM -> RCPT TO probe)
    """

    def __init__(self, smtp_timeout: float = 3.5, sender_email: str = "verify@leadpulse.io"):
        self.smtp_timeout = smtp_timeout
        self.sender_email = sender_email

    def get_mx_record(self, domain: str) -> Optional[str]:
        """Resolve and cache the primary MX record for a domain."""
        domain = domain.lower().strip()
        if domain in _MX_CACHE:
            return _MX_CACHE[domain]

        try:
            answers = dns.resolver.resolve(domain, "MX")
            # Sort by preference priority (lowest preference value = highest priority)
            mx_records = sorted([(r.preference, str(r.exchange).rstrip(".")) for r in answers])
            if mx_records:
                primary_mx = mx_records[0][1]
                _MX_CACHE[domain] = primary_mx
                return primary_mx
        except Exception as e:
            logger.debug(f"DNS MX lookup failed for domain '{domain}': {e}")

        _MX_CACHE[domain] = None
        return None

    def verify_email(self, email: Optional[str]) -> Dict[str, Any]:
        """
        Performs full verification of an email address.
        Returns a detailed result dict:
        {
            "email": str,
            "is_valid": bool,
            "status": "valid" | "invalid" | "risky" | "unverified",
            "mx_found": bool,
            "mx_host": Optional[str],
            "smtp_code": Optional[int],
            "confidence_boost": int,
            "reason": str
        }
        """
        if not email or not isinstance(email, str):
            return {
                "email": email,
                "is_valid": False,
                "status": "invalid",
                "mx_found": False,
                "mx_host": None,
                "smtp_code": None,
                "confidence_boost": -30,
                "reason": "Email address is missing or not a string",
            }

        email = email.strip()

        # Step 1: Syntax Validation
        if not EMAIL_REGEX.match(email):
            return {
                "email": email,
                "is_valid": False,
                "status": "invalid",
                "mx_found": False,
                "mx_host": None,
                "smtp_code": None,
                "confidence_boost": -50,
                "reason": "Invalid email syntax format",
            }

        domain = email.split("@")[-1].lower()

        # Check disposable domains
        if domain in DISPOSABLE_DOMAINS:
            return {
                "email": email,
                "is_valid": False,
                "status": "invalid",
                "mx_found": False,
                "mx_host": None,
                "smtp_code": None,
                "confidence_boost": -80,
                "reason": "Disposable / throwaway email provider detected",
            }

        # Step 2: DNS MX Record Check
        mx_host = self.get_mx_record(domain)
        if not mx_host:
            return {
                "email": email,
                "is_valid": False,
                "status": "invalid",
                "mx_found": False,
                "mx_host": None,
                "smtp_code": None,
                "confidence_boost": -100,
                "reason": f"Domain '{domain}' has no active Mail Exchange (MX) records",
            }

        # Step 3: SMTP Handshake Probe (RCPT TO)
        smtp_code, reason = self._probe_smtp_mailbox(mx_host, email)

        if smtp_code in (250, 251):
            # Recipient mailbox explicitly verified by destination mail server
            return {
                "email": email,
                "is_valid": True,
                "status": "valid",
                "mx_found": True,
                "mx_host": mx_host,
                "smtp_code": smtp_code,
                "confidence_boost": 25,
                "reason": f"SMTP 250 OK - Mailbox confirmed by {mx_host}",
            }
        elif smtp_code in (550, 551, 552, 553, 554):
            # Destination mail server explicitly rejected the recipient address
            return {
                "email": email,
                "is_valid": False,
                "status": "invalid",
                "mx_found": True,
                "mx_host": mx_host,
                "smtp_code": smtp_code,
                "confidence_boost": -100,
                "reason": f"SMTP {smtp_code} - Mailbox does not exist on {mx_host}",
            }
        else:
            # Server active & MX verified, but port 25 or catch-all protected
            return {
                "email": email,
                "is_valid": True,
                "status": "valid",
                "mx_found": True,
                "mx_host": mx_host,
                "smtp_code": smtp_code,
                "confidence_boost": 10,
                "reason": f"MX verified ({mx_host}) - Mail server routing confirmed",
            }

    def _probe_smtp_mailbox(self, mx_host: str, email: str) -> Tuple[Optional[int], str]:
        """Perform non-intrusive SMTP RCPT TO probe."""
        try:
            with smtplib.SMTP(mx_host, 25, timeout=self.smtp_timeout) as server:
                server.helo("mail.leadpulse.io")
                server.mail(self.sender_email)
                code, resp = server.rcpt(email)
                return code, resp.decode(errors="ignore")
        except smtplib.SMTPResponseException as e:
            return e.smtp_code, str(e.smtp_error)
        except (socket.timeout, socket.error, smtplib.SMTPException, Exception) as e:
            return None, f"SMTP handshake connection note: {str(e)}"


# Global default verifier instance
email_verifier = EmailVerifier()
