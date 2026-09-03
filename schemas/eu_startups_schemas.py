"""Pydantic schemas for EU Startups endpoints."""

from typing import List, Optional
from pydantic import BaseModel, Field


class ManualStartupPerson(BaseModel):
    """Contact person associated with a manually created startup."""
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    linkedin: Optional[str] = None


class CreateManualStartupRequest(BaseModel):
    """Payload to manually register or upsert a startup entry."""
    company_name: str = Field(..., description="Startup or company name")
    description: Optional[str] = None
    website: Optional[str] = None
    eu_startups_url: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    founded_year: Optional[int] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    company_linkedin: Optional[str] = None
    people: Optional[List[ManualStartupPerson]] = Field(default_factory=list)
