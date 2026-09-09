import os
import smtplib
import ssl
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from typing import Tuple, Optional

from email_campaigns.db import get_smtp_config
from email_campaigns.template_engine import text_to_html_email


def _build_message(smtp_user: str, from_name: str, to_email: str,
                   subject: str, html_body: str,
                   attachment_path: Optional[str] = None,
                   attachment_name: Optional[str] = None,
                   cc: Optional[str] = None) -> MIMEMultipart:
    # Ensure HTML is properly formatted
    formatted_html = text_to_html_email(html_body)
    plain_text = html_body

    # Parse CC list
    cc_list = [a.strip() for a in cc.split(",") if a.strip()] if cc else []

    # If there's an attachment, use mixed outer with alternative inner
    if attachment_path and os.path.exists(attachment_path):
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = to_email
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        # Body part (plain + html)
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(plain_text, "plain", "utf-8"))
        alt_part.attach(MIMEText(formatted_html, "html", "utf-8"))
        msg.attach(alt_part)

        # Attachment part
        try:
            with open(attachment_path, "rb") as f:
                pdf_data = f.read()
            file_name = attachment_name or os.path.basename(attachment_path)
            part = MIMEApplication(pdf_data, _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=file_name)
            msg.attach(part)
        except Exception:
            # Fallback if attachment reading fails, still send email
            pass
    else:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = to_email
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(formatted_html, "html", "utf-8"))

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


def send_email(to_email: str, subject: str, html_body: str = "",
               config: dict | None = None,
               attachment_path: Optional[str] = None,
               attachment_name: Optional[str] = None,
               body: Optional[str] = None,
               smtp_cfg: Optional[dict] = None,
               cc: Optional[str] = None) -> Tuple[bool, str]:
    """
    Send a single HTML email with optional attachment and CC.
    cc should be a comma-separated string of email addresses.
    Returns (True, "") on success or (False, error_message) on failure.
    """
    email_content = body if body is not None else html_body
    cfg = config or smtp_cfg or get_smtp_config()
    host = cfg.get("smtp_host", "").strip()
    port = int(cfg.get("smtp_port", 587))
    user = cfg.get("smtp_user", "").strip()
    password = cfg.get("smtp_pass", "").strip()
    from_name = cfg.get("from_name", "HirePilot AI")
    use_ssl = cfg.get("use_ssl", False)
    use_tls = cfg.get("use_tls", True)

    if not host or not user or not password:
        return False, "SMTP not configured. Please configure SMTP settings first."

    # Build list of all actual SMTP recipients (To + CC)
    cc_list = [a.strip() for a in cc.split(",") if a.strip()] if cc else []
    all_recipients = [to_email] + cc_list

    try:
        msg = _build_message(
            user, from_name, to_email, subject, email_content,
            attachment_path=attachment_path,
            attachment_name=attachment_name,
            cc=cc,
        )
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
                server.login(user, password)
                server.sendmail(user, all_recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                server.login(user, password)
                server.sendmail(user, all_recipients, msg.as_string())
        return True, ""
    except smtplib.SMTPRecipientsRefused:
        return False, f"Recipient refused: {to_email}"
    except Exception as e:
        return False, str(e).strip()
