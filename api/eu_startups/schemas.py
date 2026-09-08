"""Pydantic schemas for EU Startups API endpoints."""

from typing import List, Optional
from pydantic import BaseModel


class AgentStartupPersonInput(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    company: str
    email: Optional[str] = None
    linkedin: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    category: Optional[str] = None
    research_prompt: Optional[str] = None


class BatchAddAgentStartupsRequest(BaseModel):
    startups: List[AgentStartupPersonInput]
    research_prompt: Optional[str] = None


class CheckStartupPresenceItem(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    company: str
    email: Optional[str] = None


class CheckStartupPresenceRequest(BaseModel):
    leads: List[CheckStartupPresenceItem]
