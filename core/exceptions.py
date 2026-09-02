"""Custom application exceptions."""


class LeadGenException(Exception):
    """Base exception for all lead generation pipeline errors."""
    pass


class ScraperException(LeadGenException):
    """Raised when job scraping fails or encounters an error."""
    pass


class LLMException(LeadGenException):
    """Raised when LLM provider initialization or reasoning fails."""
    pass


class DatabaseException(LeadGenException):
    """Raised when database connection, query, or upsert operations fail."""
    pass


class EnrichmentException(LeadGenException):
    """Raised when lead enrichment or search tool calling fails."""
    pass
