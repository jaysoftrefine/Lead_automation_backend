"""
SMTP Sender — sends individual emails and tests SMTP connection.
Supports SSL (port 465) and STARTTLS (port 587).
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

from email.db import get_smtp_config


def _build_message(smtp_user: str, from_name: str, to_email: str,
                   subject: str, html_body: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{smtp_user}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def test_smtp_connection(config: dict | None = None) -> Tuple[bool, str]:
    """
    Tests whether the SMTP credentials can establish a connection.
    Returns (True, "OK") or (False, error_message).
    """
    cfg = config or get_smtp_config()
    host = cfg.get("smtp_host", "").strip()
    port = int(cfg.get("smtp_port", 587))
    user = cfg.get("smtp_user", "").strip()
    password = cfg.get("smtp_pass", "").strip()
    use_ssl = cfg.get("use_ssl", False)
    use_tls = cfg.get("use_tls", True)

    if not host or not user or not password:
        return False, "SMTP configuration is incomplete. Please provide host, user, and password."

    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=10) as server:
                server.login(user, password)
        else:
            with smtplib.SMTP(host, port, timeout=10) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                server.login(user, password)
        return True, "SMTP connection successful ✓"
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed. Check your email and app password."
    except smtplib.SMTPConnectError as e:
        return False, f"Cannot connect to {host}:{port} — {e}"
    except Exception as e:
        err = str(e).strip()
        if "WRONG_VERSION_NUMBER" in err or "wrong version number" in err.lower():
            return False, "SSL/TLS mismatch. Try switching between SSL (465) and TLS (587)."
        return False, f"SMTP error: {err}"


def send_email(to_email: str, subject: str, html_body: str,
               config: dict | None = None) -> Tuple[bool, str]:
    """
    Send a single HTML email.
    Returns (True, "") on success or (False, error_message) on failure.
    """
    cfg = config or get_smtp_config()
    host = cfg.get("smtp_host", "").strip()
    port = int(cfg.get("smtp_port", 587))
    user = cfg.get("smtp_user", "").strip()
    password = cfg.get("smtp_pass", "").strip()
    from_name = cfg.get("from_name", "LeadPulse AI")
    use_ssl = cfg.get("use_ssl", False)
    use_tls = cfg.get("use_tls", True)

    if not host or not user or not password:
        return False, "SMTP not configured. Please configure SMTP settings first."

    try:
        msg = _build_message(user, from_name, to_email, subject, html_body)
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
                server.login(user, password)
                server.sendmail(user, to_email, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                server.login(user, password)
                server.sendmail(user, to_email, msg.as_string())
        return True, ""
    except smtplib.SMTPRecipientsRefused:
        return False, f"Recipient refused: {to_email}"
    except Exception as e:
        return False, str(e).strip()
