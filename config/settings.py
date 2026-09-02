"""Application configuration settings using Pydantic Settings."""

from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Global configuration settings for the Lead Generation pipeline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # SQLite Central Database Settings
    sqlite_db_path: str = Field(
        default="data/eu_startups.db",
        description="Path to centralized SQLite database file",
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
        description="Google API Key for Gemini",
        alias="GOOGLE_API_KEY"
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


settings = Settings()
