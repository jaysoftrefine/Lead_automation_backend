"""Scraper and parser for direct LinkedIn Job Postings and LinkedIn Feed Posts."""

import re
import urllib.parse
from typing import Dict, Any, Optional
import requests
from bs4 import BeautifulSoup

from core.logging import logger
from db.models import RawJobPosting
from tavily import TavilyClient
from config.settings import settings


class LinkedInPostScraper:
    """Scrapes and extracts content from LinkedIn job URLs, post URLs, or raw text."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.tavily_client = TavilyClient(api_key=settings.tavily_api_key) if settings.tavily_api_key else None

    def scrape_url_or_text(
        self,
        url: Optional[str] = None,
        raw_text: Optional[str] = None,
        company_hint: Optional[str] = None,
        title_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Scrapes a LinkedIn post/job URL or extracts information from raw text.
        """
        if raw_text and not url:
            return self._parse_from_text(raw_text, company_hint, title_hint)

        if not url:
            raise ValueError("Either URL or raw text must be provided.")

        url = url.strip()

        # Check URL type
        is_job_url = "linkedin.com/jobs" in url or "/jobs/view" in url
        is_post_url = "linkedin.com/posts" in url or "linkedin.com/feed/update" in url or "linkedin.com/pulse" in url

        content_text = ""
        extracted_title = title_hint or ""
        extracted_company = company_hint or ""
        author_name = None
        author_role = None

        # 1. Attempt direct HTTP fetch with BeautifulSoup
        try:
            resp = requests.get(url, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                og_title = soup.find("meta", property="og:title")
                og_desc = soup.find("meta", property="og:description")
                page_title = soup.find("title")

                meta_title_text = og_title["content"] if og_title and "content" in og_title.attrs else (page_title.text if page_title else "")
                meta_desc_text = og_desc["content"] if og_desc and "content" in og_desc.attrs else ""

                body_text = soup.get_text(separator=" ", strip=True)
                content_text = f"{meta_title_text}\n{meta_desc_text}\n{body_text[:4000]}"

                if meta_title_text and not extracted_title:
                    extracted_title = meta_title_text.split("|")[0].split("-")[0].strip()
        except Exception as http_err:
            logger.debug(f"Direct HTTP fetch failed for {url}: {http_err}")

        # 2. Fallback to Tavily Search / Extract if direct content is too short or blocked
        if (len(content_text.strip()) < 100 or "authwall" in content_text.lower()) and self.tavily_client:
            try:
                logger.info(f"Using Tavily Intelligence to extract LinkedIn URL: {url}")
                search_data = self.tavily_client.search(query=f'"{url}"', max_results=2)
                results_list = search_data.get("results", []) if isinstance(search_data, dict) else search_data
                for res in results_list:
                    if isinstance(res, dict):
                        content_text += f"\n{res.get('title', '')}\n{res.get('content', '')}"
                        if not extracted_title and res.get("title"):
                            extracted_title = res.get("title").split("|")[0].split("-")[0].strip()
            except Exception as tavily_err:
                logger.warning(f"Tavily URL extract fallback failed: {tavily_err}")

        if not content_text:
            content_text = raw_text or f"LinkedIn posting at {url}"

        if not extracted_title:
            extracted_title = title_hint or "Hiring Opportunity"
        if not extracted_company:
            extracted_company = company_hint or self._extract_company_heuristic(content_text) or "LinkedIn Employer"

        return {
            "title": extracted_title,
            "company": extracted_company,
            "location": "Remote",
            "description": content_text[:5000],
            "author_name": author_name,
            "author_role": author_role,
            "job_url": url,
            "site": "linkedin",
            "post_type": "job" if is_job_url else "post",
        }

    def _parse_from_text(
        self, text: str, company_hint: Optional[str] = None, title_hint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Parses raw text of a LinkedIn post or job description."""
        title = title_hint or "Hiring Position"
        company = company_hint or self._extract_company_heuristic(text) or "Target Company"

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if lines and not title_hint:
            first_line = lines[0]
            if len(first_line) < 80 and any(k in first_line.lower() for k in ["hiring", "engineer", "developer", "lead", "founder", "manager", "designer"]):
                title = first_line

        return {
            "title": title,
            "company": company,
            "location": "Remote",
            "description": text[:5000],
            "author_name": None,
            "author_role": None,
            "job_url": "https://www.linkedin.com",
            "site": "linkedin",
            "post_type": "text",
        }

    def _extract_company_heuristic(self, text: str) -> Optional[str]:
        """Simple heuristic to find company name from text snippets."""
        patterns = [
            r"(?:at|@)\s+([A-Z][a-zA-Z0-9\s&]{2,25})(?:\s+is\s+hiring|\s+team|\s*\.|\s*,|\s*\n)",
            r"([A-Z][a-zA-Z0-9\s&]{2,25})\s+is\s+looking\s+for",
            r"Join\s+(?:the\s+)?([A-Z][a-zA-Z0-9\s&]{2,25})\s+team",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                candidate = m.group(1).strip()
                if candidate.lower() not in ("we", "our", "the", "a", "an", "linkedin"):
                    return candidate
        return None


# Global instance
linkedin_post_scraper = LinkedInPostScraper()
