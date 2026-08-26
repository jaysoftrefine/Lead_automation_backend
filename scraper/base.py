"""Abstract base class for job scrapers."""

from abc import ABC, abstractmethod
from typing import List, Optional
from db.models import RawJobPosting


class BaseScraper(ABC):
    """Base interface for all job scraping providers."""

    @abstractmethod
    def scrape(
        self,
        search_term: str,
        location: Optional[str] = None,
        results_wanted: int = 20,
        hours_old: Optional[int] = None,
        sites: Optional[List[str]] = None,
        **kwargs
    ) -> List[RawJobPosting]:
        """
        Scrape job postings matching criteria.
        
        :param search_term: Keyword or role to search for (e.g. 'Python Developer', 'Full Stack')
        :param location: Target location (e.g. 'Remote', 'United States', 'India')
        :param results_wanted: Max number of listings to retrieve
        :param hours_old: Max age in hours of postings
        :param sites: List of sites to target (e.g. ['linkedin', 'naukri'])
        :return: List of normalized RawJobPosting models
        """
        pass
