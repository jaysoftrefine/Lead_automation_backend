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

    # Cloud Database Settings (Supabase / PostgreSQL)
    database_url: Optional[str] = Field(
        default=None,
        description="PostgreSQL / Supabase connection string (e.g. postgresql://postgres.xxx:pass@aws-0-region.pooler.supabase.com:6543/postgres)",
        validation_alias=AliasChoices("DATABASE_URL", "SUPABASE_DB_URL", "POSTGRES_URL")
    )
    supabase_url: Optional[str] = Field(
        default=None,
        description="Supabase Project API URL (e.g. https://xyz.supabase.co)",
        alias="SUPABASE_URL"
    )
    supabase_key: Optional[str] = Field(
        default=None,
        description="Supabase Anon / Service Role Key",
        alias="SUPABASE_KEY"
    )

    # SQLite Central Database Settings (Fallback if DATABASE_URL is not set)
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

    # Google Gemini Settings (Dual-account / key rotation support)
    google_api_key: Optional[str] = Field(
        default=None,
        description="Gemini API Key (Primary/Legacy)",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    google_api_key_1: Optional[str] = Field(
        default=None,
        description="First Google Gemini API Key",
        validation_alias=AliasChoices("GEMINI_API_KEY_1", "GOOGLE_API_KEY_1")
    )
    google_api_key_2: Optional[str] = Field(
        default=None,
        description="Second Google Gemini API Key (Fallback account)",
        validation_alias=AliasChoices("GEMINI_API_KEY_2", "GOOGLE_API_KEY_2")
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
        default="openai/gpt-oss-20b",
        description="NVIDIA model name",
        alias="NVIDIA_MODEL"
    )

    # Tavily Web Search (DEPRECATED - Replaced by Google LLM + DDGS Web Search)
    tavily_api_key: Optional[str] = Field(
        default=None,
        description="Tavily API Key (Deprecated: replaced by Google LLM + DDGS Web Search)",
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
    outreach_stage1_time: str = Field(
        default="10:00",
        description="Local fire time for Stage 1 (temp1) in HH:MM format",
        alias="OUTREACH_STAGE1_TIME"
    )
    outreach_stage2_time: str = Field(
        default="19:00",
        description="Local fire time for Stage 2 (temp2) in HH:MM format",
        alias="OUTREACH_STAGE2_TIME"
    )
    outreach_stage3_time: str = Field(
        default="10:00",
        description="Local fire time for Stage 3 (temp3) in HH:MM format",
        alias="OUTREACH_STAGE3_TIME"
    )
    outreach_sending_mode: str = Field(
        default="scheduled",
        description="Outreach timing mode: 'scheduled' (fire at fixed local time per timezone) or 'immediate'",
        alias="OUTREACH_SENDING_MODE"
    )
    outreach_smtp_rotation: str = Field(
        default="round_robin",
        description="SMTP rotation strategy: 'round_robin' or 'lead_hash'",
        alias="OUTREACH_SMTP_ROTATION"
    )
    outreach_default_timezone: str = Field(
        default="America/New_York",
        description="Fallback IANA timezone for remote/unresolved locations",
        alias="OUTREACH_DEFAULT_TIMEZONE"
    )

    def get_google_api_keys(self) -> List[str]:
        """Returns all configured Google/Gemini API keys without duplicates."""
        keys: List[str] = []
        for candidate in [self.google_api_key_1, self.google_api_key_2, self.google_api_key]:
            if candidate and str(candidate).strip() and str(candidate).strip() not in keys:
                keys.append(str(candidate).strip())
        return keys


settings = Settings()

# Automatically sync GEMINI_API_KEY and GOOGLE_API_KEY in os.environ so all SDKs find it
google_keys = settings.get_google_api_keys()
if google_keys:
    os.environ["GEMINI_API_KEY"] = google_keys[0]
    os.environ["GOOGLE_API_KEY"] = google_keys[0]
    if len(google_keys) > 1:
        os.environ["GOOGLE_API_KEY_2"] = google_keys[1]
        os.environ["GEMINI_API_KEY_2"] = google_keys[1]

