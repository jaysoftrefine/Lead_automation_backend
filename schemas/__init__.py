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
    UpdateLeadOutreachRequest,
    BulkUpdateLeadOutreachRequest,
    InstantResearchRequest,
    ManualContactInput,
    CreateManualLeadRequest,
    CheckPresenceItem,
    CheckPresenceRequest,
    AddExtractedLeadRequest,
    BatchAddExtractedLeadsRequest,
)
from schemas.email_schemas import (
    TemplateCreate,
    TemplateUpdate,
    SMTPConfigBody,
    SMTPAccountCreate,
    SMTPAccountUpdate,
    CampaignCreate,
    CampaignUpdate,
    TestEmailBody,
    CampaignPreviewGeneratedRequest,
    AudienceCreate,
    AudienceUpdate,
    QueueGenerateRequest,
    QueueItemUpdate,
    QueueSendRequest,
    SequenceStep,
    SequenceStepUpdate,
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
    "UpdateLeadOutreachRequest",
    "BulkUpdateLeadOutreachRequest",
    "InstantResearchRequest",
    "ManualContactInput",
    "CreateManualLeadRequest",
    # Email Campaigns
    "TemplateCreate",
    "TemplateUpdate",
    "SMTPConfigBody",
    "SMTPAccountCreate",
    "SMTPAccountUpdate",
    "CampaignCreate",
    "CampaignUpdate",
    "TestEmailBody",
    "CampaignPreviewGeneratedRequest",
    "AudienceCreate",
    "AudienceUpdate",
    "QueueGenerateRequest",
    "QueueItemUpdate",
    "QueueSendRequest",
    "SequenceStep",
    "SequenceStepUpdate",
]
