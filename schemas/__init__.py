"""Centralized Pydantic schemas for HirePilot Backend API."""

from schemas.eu_startups_schemas import (
    ManualStartupPerson,
    CreateManualStartupRequest,
)
from schemas.lead_schemas import (
    RunPipelineRequest,
    TestEnrichmentRequest,
    UpdateLeadStatusRequest,
    UpdateLeadTypeRequest,
    InstantResearchRequest,
    ManualContactInput,
    CreateManualLeadRequest,
)
from schemas.email_schemas import (
    TemplateCreate,
    TemplateUpdate,
    SMTPConfigBody,
    CampaignCreate,
    CampaignUpdate,
    TestEmailBody,
    CampaignPreviewGeneratedRequest,
    AudienceCreate,
    AudienceUpdate,
    QueueGenerateRequest,
    QueueItemUpdate,
)

__all__ = [
    # EU Startups
    "ManualStartupPerson",
    "CreateManualStartupRequest",
    # Leads & Pipeline
    "RunPipelineRequest",
    "TestEnrichmentRequest",
    "UpdateLeadStatusRequest",
    "UpdateLeadTypeRequest",
    "InstantResearchRequest",
    "ManualContactInput",
    "CreateManualLeadRequest",
    # Email Campaigns
    "TemplateCreate",
    "TemplateUpdate",
    "SMTPConfigBody",
    "CampaignCreate",
    "CampaignUpdate",
    "TestEmailBody",
    "CampaignPreviewGeneratedRequest",
    "AudienceCreate",
    "AudienceUpdate",
    "QueueGenerateRequest",
    "QueueItemUpdate",
]
