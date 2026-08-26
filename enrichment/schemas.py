"""Structured Output Schemas for the Lead Enrichment Agent."""

from typing import List, Optional
from pydantic import BaseModel, Field
from db.models import ContactPerson


class ExtractedLeadData(BaseModel):
    """Strict structured output returned by the Thinking LLM Agent."""

    is_valid_lead: bool = Field(
        default=True,
        description="True if this is a genuine hiring/freelancing lead with actionable information, False if spam, expired, or non-actionable"
    )
    relevance_score: int = Field(
        default=60,
        ge=0,
        le=100,
        description="Quality and relevance score from 0 to 100 based on role clarity, company stature, and contact reachability"
    )
    company_domain: Optional[str] = Field(
        default=None,
        description="Official website or domain of the hiring company (e.g. acme.com)"
    )
    company_summary: Optional[str] = Field(
        default=None,
        description="2-3 sentence summary of what the company does and its primary industry"
    )
    company_size: Optional[str] = Field(
        default=None,
        description="Estimated company size category (e.g., '1-10 employees', '11-50', '51-200', '201-1000', '1000+')"
    )
    contacts: List[ContactPerson] = Field(
        default_factory=list,
        description="List of verified recruiters, talent acquisition leads, engineering managers, founders, or generic career/HR contact details discovered"
    )
    key_technologies: List[str] = Field(
        default_factory=list,
        description="List of primary programming languages, frameworks, cloud providers, or tools mentioned in the job posting"
    )
    hiring_urgency: str = Field(
        default="Normal",
        description="Assessed hiring urgency: 'Immediate', 'High', 'Normal', or 'Low'"
    )
    lead_summary: str = Field(
        default="",
        description="Concise strategic overview of why this lead is valuable and the suggested outreach angle"
    )
    thinking_process: str = Field(
        default="",
        description="Detailed step-by-step reasoning log explaining what information was sought, how search queries were evaluated, how emails/contacts were verified, and how conclusions were reached."
    )
    search_queries_used: List[str] = Field(
        default_factory=list,
        description="List of search queries that were executed during the investigation"
    )
