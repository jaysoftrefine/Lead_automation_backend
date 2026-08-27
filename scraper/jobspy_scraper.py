"""JobSpy scraper implementation for LinkedIn, Naukri, and other job boards."""

import math
from typing import List, Optional, Dict, Any
from core.logging import logger
from core.exceptions import ScraperException
from db.models import RawJobPosting
from scraper.base import BaseScraper

try:
    from jobspy import scrape_jobs
    from jobspy.model import Country

    # Patch Country.from_string to handle unlisted countries (e.g., Armenia, Georgia, etc.) gracefully
    def _safe_country_from_string(cls, country_str: str):
        if not country_str or not isinstance(country_str, str):
            return None
        cleaned = country_str.strip().lower()
        for country in cls:
            country_names = [c.strip().lower() for c in country.value[0].split(",")]
            if cleaned in country_names:
                return country
        # Fallback to the country string itself instead of raising a fatal ValueError
        return country_str

    Country.from_string = classmethod(_safe_country_from_string)

except ImportError:
    scrape_jobs = None


class JobSpyScraper(BaseScraper):
    """
    Scraper implementation leveraging python-jobspy library.
    Supported sites include: linkedin, naukri, indeed, glassdoor, zip_recruiter, google.
    """

    def __init__(self, proxies: Optional[List[str]] = None):
        self.proxies = proxies

    def scrape(
        self,
        search_term: str,
        location: Optional[str] = "Remote",
        results_wanted: int = 20,
        hours_old: Optional[int] = 72,
        sites: Optional[List[str]] = None,
        job_type: Optional[str] = None,
        country_indeed: str = "USA",
        **kwargs
    ) -> List[RawJobPosting]:
        """
        Scrape jobs across specified platforms using python-jobspy.
        """
        if scrape_jobs is None:
            raise ScraperException("python-jobspy library is not installed. Please install requirements.txt")

        target_sites = sites or ["linkedin", "naukri"]
        logger.info(
            f"Initiating JobSpy scrape for '{search_term}' | Location: '{location}' | Job Type: {job_type or 'All'} | Sites: {target_sites} | Results wanted: {results_wanted}"
        )

        try:
            # Build scraping kwargs
            scrape_kwargs = {
                "site_name": target_sites,
                "search_term": search_term,
                "location": location or "",
                "results_wanted": results_wanted,
                "hours_old": hours_old,
                "country_indeed": country_indeed,
                "proxies": self.proxies,
                **kwargs
            }
            if job_type and job_type.lower() not in ("all", "any"):
                scrape_kwargs["job_type"] = job_type.lower().strip()

            # Call jobspy's scrape_jobs
            df = scrape_jobs(**scrape_kwargs)

            if df is None or df.empty:
                logger.warning("JobSpy returned an empty dataframe or no jobs found.")
                return []

            logger.info(f"JobSpy successfully scraped {len(df)} raw listings.")
            postings = self._parse_dataframe(df)
            return postings

        except Exception as e:
            logger.warning(f"Batch JobSpy scrape failed ({e}). Attempting resilient site-by-site fallback...")
            collected_dfs = []
            for single_site in target_sites:
                try:
                    single_kwargs = dict(scrape_kwargs)
                    single_kwargs["site_name"] = [single_site]
                    single_df = scrape_jobs(**single_kwargs)
                    if single_df is not None and not single_df.empty:
                        collected_dfs.append(single_df)
                        logger.info(f"Fallback scrape succeeded for site '{single_site}': {len(single_df)} jobs.")
                except Exception as site_err:
                    logger.warning(f"Scraping single site '{single_site}' failed: {site_err}")

            if collected_dfs:
                import pandas as pd
                combined_df = pd.concat(collected_dfs, ignore_index=True)
                return self._parse_dataframe(combined_df)

            logger.error(f"All scrapers failed for query '{search_term}': {e}")
            return []

    def _parse_dataframe(self, df) -> List[RawJobPosting]:
        """Converts JobSpy pandas DataFrame into a clean list of RawJobPosting objects."""
        postings: List[RawJobPosting] = []

        for _, row in df.iterrows():
            try:
                # Helper to safely get string values without NaN
                def clean_val(val: Any) -> Optional[str]:
                    if val is None:
                        return None
                    if isinstance(val, float) and math.isnan(val):
                        return None
                    s = str(val).strip()
                    return s if s else None

                def clean_float(val: Any) -> Optional[float]:
                    if val is None:
                        return None
                    try:
                        f = float(val)
                        return None if math.isnan(f) else f
                    except (ValueError, TypeError):
                        return None

                title = clean_val(row.get("title"))
                company = clean_val(row.get("company"))
                job_url = clean_val(row.get("job_url"))
                site = clean_val(row.get("site")) or "unknown"

                # If missing fundamental fields, skip row
                if not title or not company or not job_url:
                    continue

                raw_dict = row.to_dict()
                # Clean NaNs out of metadata
                clean_metadata = {k: v for k, v in raw_dict.items() if v is not None and not (isinstance(v, float) and math.isnan(v))}

                posting = RawJobPosting(
                    id=clean_val(row.get("id")),
                    title=title,
                    company=company,
                    location=clean_val(row.get("location")),
                    job_url=job_url,
                    site=site,
                    description=clean_val(row.get("description")),
                    job_type=clean_val(row.get("job_type")),
                    salary_min=clean_float(row.get("min_amount")),
                    salary_max=clean_float(row.get("max_amount")),
                    salary_currency=clean_val(row.get("currency")),
                    date_posted=clean_val(row.get("date_posted")),
                    raw_metadata=clean_metadata,
                )
                postings.append(posting)

            except Exception as row_err:
                logger.debug(f"Skipping malformed row during parsing: {row_err}")
                continue

        logger.info(f"Parsed {len(postings)} valid RawJobPosting models.")
        return postings
