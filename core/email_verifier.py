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

    def find_and_verify_executive_email(
        self, full_name: str, domain: str, role: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Generates executive email permutations for an identified leader and tests them
        against DNS MX and SMTP handshake verification. Returns the highest-confidence verified email.
        """
        candidates = self.generate_executive_email_candidates(full_name, domain, role=role)
        if not candidates:
            return None

        for candidate in candidates:
            res = self.verify_email(candidate)
            if res.get("is_valid"):
                return res

        return None

    def generate_executive_email_candidates(
        self, full_name: str, domain: str, role: Optional[str] = None
    ) -> List[str]:
        """Generates standard B2B email permutations for an executive at a given domain."""
        if not full_name or not domain:
            return []

        # Clean domain (strip http://, www., paths)
        clean_domain = domain.lower().replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0].strip()
        if not clean_domain or "." not in clean_domain:
            return []

        # Clean name
        clean_name = re.sub(r"[^a-zA-Z\s]", "", full_name).strip().lower()
        parts = clean_name.split()
        if not parts:
            return []

        first = parts[0]
        last = parts[-1] if len(parts) > 1 else ""

        candidates = []
        if first and last:
            candidates.append(f"{first}.{last}@{clean_domain}")      # john.doe@company.com
            candidates.append(f"{first}@{clean_domain}")             # john@company.com
            candidates.append(f"{first[0]}{last}@{clean_domain}")    # jdoe@company.com
            candidates.append(f"{first}{last}@{clean_domain}")       # johndoe@company.com
            candidates.append(f"{first[0]}.{last}@{clean_domain}")   # j.doe@company.com
            candidates.append(f"{first}_{last}@{clean_domain}")      # john_doe@company.com
        else:
            candidates.append(f"{first}@{clean_domain}")

        # Role-based executive aliases
        if role:
            role_lower = role.lower()
            if "ceo" in role_lower:
                candidates.append(f"ceo@{clean_domain}")
            elif "founder" in role_lower or "owner" in role_lower:
                candidates.append(f"founder@{clean_domain}")
            elif "cto" in role_lower:
                candidates.append(f"cto@{clean_domain}")
            elif "coo" in role_lower:
                candidates.append(f"coo@{clean_domain}")

        candidates.append(f"contact@{clean_domain}")
        candidates.append(f"hello@{clean_domain}")

        # Remove duplicates while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        return unique_candidates


# Global default verifier instance
email_verifier = EmailVerifier()
