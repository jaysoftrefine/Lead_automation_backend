"""Pydantic schemas for lead generation pipeline and lead management endpoints."""

from typing import List, Optional
from pydantic import BaseModel, Field


class RunPipelineRequest(BaseModel):
    """Configuration payload to trigger autonomous lead scraping & enrichment pipeline."""
    search_term: str = Field(..., example="Python Backend Developer")
    location: str = Field("Remote", example="Remote")
    sites: Optional[List[str]] = Field(default_factory=lambda: ["linkedin", "indeed"])
    platforms: Optional[List[str]] = None
    company_size: str = Field("small", example="small", description="Target company size: 'small' (1-50 employees), 'medium' (51-500), 'large' (500+), 'all'")
    job_type: Optional[str] = Field("all", example="all", description="Target job type: 'all', 'contract' (freelance/C2C), 'fulltime', 'parttime', 'internship'")
    limit: Optional[int] = Field(10, ge=1, le=100)
    results_wanted: Optional[int] = None
    provider: Optional[str] = Field(None, example="gemini")
    llm_provider: Optional[str] = None
    model: Optional[str] = Field(None, example="gemini-2.5-flash")
    model_name: Optional[str] = None
    min_score: int = Field(20, ge=0, le=100)
    hours_old: int = Field(168, ge=0)
    is_remote: bool = Field(True, description="Filter strictly for Remote positions within target location")
    skip_existing: bool = Field(False, description="Skip jobs that already exist in database")


class TestEnrichmentRequest(BaseModel):
    """Payload to test LLM research and contact enrichment for a single company."""
    title: str = Field(..., example="Senior Software Engineer")
    company: str = Field(..., example="Stripe")
    location: Optional[str] = Field("Remote")
    job_description: Optional[str] = Field(None)
    job_url: Optional[str] = Field(None)
    target_company_size: Optional[str] = Field("small")
    target_job_type: Optional[str] = Field("all")
    provider: Optional[str] = Field(None)
    model: Optional[str] = Field(None)
    save_to_db: bool = Field(True)


class UpdateLeadStatusRequest(BaseModel):
    """Payload to update lead workflow status."""
    job_url: str
    status: str = Field(..., example="qualified")  # new, contacted, qualified, rejected, archived


class InstantResearchRequest(BaseModel):
    """Payload for on-the-fly deep web research of companies and executives."""
    prompt: str = Field(..., example="Research fast-growing European B2B SaaS startups in AI & automation and extract their founders with direct emails.")
    max_search_results: Optional[int] = Field(5, ge=1, le=10)


class ManualContactInput(BaseModel):
    """Contact executive details when adding a lead manually."""
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None


class CreateManualLeadRequest(BaseModel):
    """Payload to manually register an enriched lead."""
    company: str = Field(..., description="Company name")
    title: str = Field(..., description="Job or opportunity title")
    company_domain: Optional[str] = None
    location: Optional[str] = "Remote"
    company_size: Optional[str] = None
    job_type: Optional[str] = "fulltime"
    job_url: Optional[str] = None
    lead_summary: Optional[str] = None
    key_technologies: Optional[List[str]] = Field(default_factory=list)
    relevance_score: Optional[int] = 80
    contacts: Optional[List[ManualContactInput]] = Field(default_factory=list)
