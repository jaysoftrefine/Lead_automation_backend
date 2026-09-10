import os
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, AliasChoices


class Settings(BaseSettings):
    """Global configuration settings for the Lead Generation pipeline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # SQLite Central Database Settings (Job Leads & Enriched Data)
    sqlite_db_path: str = Field(
        default="data/leads.db",
        description="Path to job leads SQLite database file",
        alias="SQLITE_DB_PATH"
    )

    # Legacy MongoDB Settings (Optional, deprecated)
    mongodb_uri: Optional[str] = Field(
        default="mongodb://localhost:27017",
        description="MongoDB connection string URI (deprecated)",
        alias="MONGODB_URI"
    )
    mongodb_db_name: Optional[str] = Field(
        default="lead_gen_db",
        description="MongoDB database name (deprecated)",
        alias="MONGODB_DB_NAME"
    )
    mongodb_collection_name: Optional[str] = Field(
        default="enriched_leads",
        description="Collection for fully enriched leads (deprecated)",
        alias="MONGODB_COLLECTION_NAME"
    )
    mongodb_raw_collection_name: Optional[str] = Field(
        default="raw_jobs",
        description="Collection for raw scraped job postings (deprecated)",
        alias="MONGODB_RAW_COLLECTION_NAME"
    )

    # LLM Settings
    default_llm_provider: Literal["gemini", "nvidia"] = Field(
        default="gemini",
        description="Default LLM provider to use ('gemini' or 'nvidia')",
        alias="DEFAULT_LLM_PROVIDER"
    )

    # Google Gemini Settings
    google_api_key: Optional[str] = Field(
        default=None,
        description="Gemini API Key",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    gemini_model: str = Field(
        default="gemini-3.5-flash-lite",
        description="Gemini model name",
        alias="GEMINI_MODEL"
    )

    # NVIDIA Settings
    nvidia_api_key: Optional[str] = Field(
        default=None,
        description="NVIDIA NIM API Key",
        alias="NVIDIA_API_KEY"
    )
    nvidia_model: str = Field(
        default="meta/llama-3.3-70b-instruct",
        description="NVIDIA model name",
        alias="NVIDIA_MODEL"
    )

    # Tavily Web Search
    tavily_api_key: Optional[str] = Field(
        default=None,
        description="Tavily API Key for web search",
        alias="TAVILY_API_KEY"
    )

    # Pipeline Defaults
    max_search_results_per_lead: int = Field(
        default=5,
        description="Max search results fetched per query by Tavily",
        alias="MAX_SEARCH_RESULTS_PER_LEAD"
    )
    scrape_results_limit: int = Field(
        default=20,
        description="Default number of jobs to scrape per run",
        alias="SCRAPE_RESULTS_LIMIT"
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level",
        alias="LOG_LEVEL"
    )
    scraping_schedule_daily_time: str = Field(
        default="22:00",
        description="Daily 24-hour time to automatically run due scraping tasks (e.g. '22:00')",
        alias="SCRAPING_SCHEDULE_DAILY_TIME"
    )
    outreach_schedule_daily_time: str = Field(
        default="09:00",
        description="Daily time to send due per-lead outreach emails (auto+open+date)",
        alias="OUTREACH_SCHEDULE_DAILY_TIME"
    )
    outreach_stage_gap_days: int = Field(
        default=6,
        ge=1,
        description="Days after a send before the next outreach template is due (6 → 7th day)",
        alias="OUTREACH_STAGE_GAP_DAYS"
    )
    outreach_first_send_delay_days: int = Field(
        default=1,
        ge=0,
        description="Days after scrap date for the first outreach send",
        alias="OUTREACH_FIRST_SEND_DELAY_DAYS"
    )
    outreach_default_mode: str = Field(
        default="auto",
        description="Default outreach mode for new leads: auto | manual",
        alias="OUTREACH_DEFAULT_MODE"
    )
    outreach_default_state: str = Field(
        default="open",
        description="Default outreach state for new leads: open | closed",
        alias="OUTREACH_DEFAULT_STATE"
    )


settings = Settings()

# Automatically sync GEMINI_API_KEY and GOOGLE_API_KEY in os.environ so all SDKs find it
if settings.google_api_key:
    os.environ["GEMINI_API_KEY"] = settings.google_api_key
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key
