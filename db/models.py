"""Pydantic data models for jobs, contacts, and enriched leads."""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, HttpUrl


class ContactPerson(BaseModel):
    """Details of a recruiter, hiring manager, or executive found for a lead."""
    name: Optional[str] = Field(None, description="Full name of the contact person")
    role: Optional[str] = Field(None, description="Job title / role (e.g., Technical Recruiter, VP Engineering, CTO)")
    email: Optional[str] = Field(None, description="Email address of the contact or general hiring email")
    phone: Optional[str] = Field(None, description="Direct or corporate contact phone number")
    linkedin_url: Optional[str] = Field(None, description="LinkedIn profile URL of the contact person")
    confidence_score: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Confidence score (0-100) on the accuracy of this contact info"
    )
    source_url: Optional[str] = Field(None, description="Source webpage where this contact detail was located")
    is_verified: bool = Field(default=False, description="Whether email passed MX and SMTP deliverability checks")
    verification_status: str = Field(default="unverified", description="Email deliverability status: valid, invalid, or unverified")
    verification_details: Optional[str] = Field(None, description="Detailed MX / SMTP diagnostic message")


class RawJobPosting(BaseModel):
    """Raw scraped job posting from JobSpy (LinkedIn, Naukri, etc.)."""
    id: Optional[str] = Field(None, description="Unique identifier (URL hash or platform ID)")
    title: str = Field(..., description="Job title or freelance project title")
    company: str = Field(..., description="Company name")
    location: Optional[str] = Field(None, description="Job location or remote status")
    job_url: str = Field(..., description="Direct URL to the job posting")
    site: str = Field(..., description="Source platform: linkedin, naukri, indeed, glassdoor, etc.")
    description: Optional[str] = Field(None, description="Full text description of the job posting")
    job_type: Optional[str] = Field(None, description="Full-time, contract, freelance, part-time")
    salary_min: Optional[float] = Field(None, description="Minimum compensation if specified")
    salary_max: Optional[float] = Field(None, description="Maximum compensation if specified")
    salary_currency: Optional[str] = Field(None, description="Currency of salary")
    date_posted: Optional[str] = Field(None, description="Date or relative time job was posted")
    scraped_at: datetime = Field(default_factory=datetime.utcnow, description="Timestamp of scraping")

    # Extra raw attributes
    raw_metadata: Dict[str, Any] = Field(default_factory=dict, description="Any additional fields from scraper")


class EnrichedLead(BaseModel):
    """Enriched lead stored in MongoDB after agent thinking and Tavily search."""
    # Reference to original job
    job_url: str = Field(..., description="Primary unique key - Direct URL to the job posting")
    title: str = Field(..., description="Job or project title")
    company: str = Field(..., description="Company name")
    site: str = Field(..., description="Source platform (linkedin, naukri, etc.)")
    location: Optional[str] = Field(None, description="Job location")
    job_type: Optional[str] = Field(None, description="Job type (Full-time, Contract, etc.)")
    job_description: Optional[str] = Field(None, description="Summary or full job description")
    
    # Enrichment fields
    is_valid_lead: bool = Field(True, description="Whether the LLM validated this as a qualified lead")
    relevance_score: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Relevance / Quality score (0-100)"
    )
    company_domain: Optional[str] = Field(None, description="Official company domain/website")
    company_summary: Optional[str] = Field(None, description="Brief overview of the company")
    company_size: Optional[str] = Field(None, description="Estimated company size (e.g. 11-50, 500+)")
    
    # Decision Makers & Contacts found via Tavily Web Search
    contacts: List[ContactPerson] = Field(
        default_factory=list,
        description="List of verified recruiters, executives, or hiring contacts"
    )
    
    # Tech Stack & Requirements
    key_technologies: List[str] = Field(
        default_factory=list,
        description="Key tech stack, tools, or skills identified in the job"
    )
    
    # Agent Reasoning & Notes
    hiring_urgency: Optional[str] = Field(
        None,
        description="Assessed urgency: Immediate, High, Normal, Unspecified"
    )
    lead_summary: Optional[str] = Field(None, description="High-level summary of the opportunity for outreach")
    agent_thinking_process: Optional[str] = Field(
        None,
        description="Step-by-step reasoning and search rationale recorded by the thinking agent"
    )
    search_queries_used: List[str] = Field(
        default_factory=list,
        description="List of Tavily search queries executed during enrichment"
    )
    
    # Status & Timestamps
    status: str = Field(
        default="new",
        description="Lead status: new, contacted, qualified, rejected, archived"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
