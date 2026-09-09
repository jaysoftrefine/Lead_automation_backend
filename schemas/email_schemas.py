"""Pydantic schemas for email campaigns, templates, audiences, and SMTP endpoints."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class TemplateCreate(BaseModel):
    """Payload to create an outreach email template."""
    name: str
    subject: str
    body: str
    tags: Optional[str] = ""
    cc: Optional[str] = None          # comma-separated CC addresses
    attachment_path: Optional[str] = None
    attachment_name: Optional[str] = None


class TemplateUpdate(BaseModel):
    """Payload to update an existing email template."""
    name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[str] = None
    cc: Optional[str] = None          # comma-separated CC addresses
    attachment_path: Optional[str] = None
    attachment_name: Optional[str] = None


class SMTPConfigBody(BaseModel):
    """Payload to configure SMTP credentials and mail server settings."""
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    from_name: str = "HirePilot AI"
    use_ssl: bool = False
    use_tls: bool = True


class SMTPAccountCreate(BaseModel):
    """Payload to add a new SMTP credential account."""
    name: Optional[str] = None
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_pass: str
    from_name: str = "HirePilot AI"
    use_ssl: bool = False
    use_tls: bool = True
    is_default: bool = False


class SMTPAccountUpdate(BaseModel):
    """Payload to update an existing SMTP credential account."""
    name: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    from_name: Optional[str] = None
    use_ssl: Optional[bool] = None
    use_tls: Optional[bool] = None
    is_default: Optional[bool] = None


class SequenceStep(BaseModel):
    """A single step in an email drip sequence."""
    template_id: str
    days_after: int = 0   # days after campaign start_date when this step fires


class CampaignCreate(BaseModel):
    """Payload to create a new outreach campaign."""
    name: str
    template_id: str                                    # used for one_shot campaigns
    smtp_account_id: Optional[str] = None
    audience_sources: List[str] = ["sqlite"]            # "sqlite", "mongo", "manual", "selected"
    audience_filters: Dict[str, Any] = {}               # country, category
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    delay_seconds: float = 0.8
    draft: bool = False
    # Sequence / drip fields
    campaign_type: str = "one_shot"                     # "one_shot" | "sequence"
    steps: Optional[List[SequenceStep]] = None          # drip steps (sequence campaigns)
    reminder_email: Optional[str] = None                # admin email for pre-send reminders
    reminder_hours_before: int = 24                     # hours before step to send reminder


class CampaignUpdate(BaseModel):
    """Payload to update an existing campaign configuration or state."""
    name: Optional[str] = None
    template_id: Optional[str] = None
    smtp_account_id: Optional[str] = None
    audience_sources: Optional[List[str]] = None
    audience_filters: Optional[Dict[str, Any]] = None
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    delay_seconds: Optional[float] = None
    status: Optional[str] = None
    # Sequence / drip fields
    steps: Optional[List[SequenceStep]] = None
    reminder_email: Optional[str] = None
    reminder_hours_before: Optional[int] = None


class TestEmailBody(BaseModel):
    """Payload to send a test verification email."""
    to_email: str
    smtp_account_id: Optional[str] = None
    template_id: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    attachment_path: Optional[str] = None
    attachment_name: Optional[str] = None


class CampaignPreviewGeneratedRequest(BaseModel):
    """Payload to preview generated email messages for a campaign."""
    template_id: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    audience_sources: List[str] = ["sqlite"]
    audience_filters: Dict[str, Any] = {}
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    limit: int = 10


class AudienceCreate(BaseModel):
    """Payload to create a saved audience filter/segment."""
    name: str
    description: Optional[str] = ""
    sources: List[str] = ["sqlite"]
    filters: Dict[str, Any] = {}
    manual_recipients: Optional[List[Any]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None


class AudienceUpdate(BaseModel):
    """Payload to update an audience segment."""
    name: Optional[str] = None
    description: Optional[str] = None
    sources: Optional[List[str]] = None
    filters: Optional[Dict[str, Any]] = None
    manual_recipients: Optional[List[Any]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None


class QueueGenerateRequest(BaseModel):
    """Payload to generate an outbound email dispatch queue."""
    template_id: str
    smtp_account_id: Optional[str] = None
    audience_id: Optional[str] = None
    audience_sources: List[str] = ["sqlite"]
    audience_filters: Dict[str, Any] = {}
    manual_emails: Optional[List[str]] = None
    selected_recipients: Optional[List[Dict[str, Any]]] = None
    limit: int = 50


class QueueItemUpdate(BaseModel):
    """Payload to update an individual email item in the send queue."""
    subject: Optional[str] = None
    body: Optional[str] = None
    smtp_account_id: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_email: Optional[str] = None
    company_name: Optional[str] = None


class QueueSendRequest(BaseModel):
    """Payload to send an individual queue item with an explicit SMTP account."""
    smtp_account_id: Optional[str] = None
