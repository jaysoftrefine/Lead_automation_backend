"""LinkedIn Feed Post Scraper leveraging authenticated linkedin-api library."""

import re
import urllib.parse
from typing import List, Optional, Dict, Any

from config.settings import settings
from core.logging import logger
from db.models import RawJobPosting

try:
    from linkedin_api import Linkedin
except ImportError:
    Linkedin = None


class LinkedInFeedScraper:
    """
    Authenticated LinkedIn Post & Feed Search Scraper.
    Searches for organic 'we are hiring' posts, founder updates, and contract gigs on LinkedIn.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        li_at: Optional[str] = None,
    ):
        self.username = username or settings.linkedin_email
        self.password = password or settings.linkedin_password
        self.li_at = li_at or settings.linkedin_li_at_cookie
        self._client: Optional[Linkedin] = None

    def is_configured(self) -> bool:
        """Checks whether LinkedIn account credentials or cookies are set in .env."""
        return bool((self.username and self.password) or self.li_at)

    def _get_client(self) -> Optional[Linkedin]:
        """Initializes and returns authenticated Linkedin API client."""
        if self._client:
            return self._client

        if not self.is_configured():
            logger.warning("LinkedIn credentials not set (LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env).")
            return None

        if Linkedin is None:
            logger.error("linkedin-api library not installed. Please run: pip install linkedin-api")
            return None

        try:
            if self.li_at:
                logger.info("Authenticating with LinkedIn via li_at session cookie...")
                self._client = Linkedin("", "", cookies={"li_at": self.li_at}, authenticate=False)
            else:
                logger.info(f"Authenticating with LinkedIn via credentials for {self.username}...")
                self._client = Linkedin(self.username, self.password)

            logger.info("✅ LinkedIn API authenticated successfully.")
            return self._client
        except Exception as e:
            logger.error(f"Failed to authenticate LinkedIn API: {e}")
            return None

    def search_hiring_posts(
        self,
        search_term: str,
        limit: int = 10,
        job_type: Optional[str] = None,
    ) -> List[RawJobPosting]:
        """
        Searches LinkedIn organic posts for hiring updates matching keywords.
        Returns parsed list of RawJobPosting objects.
        """
        client = self._get_client()
        if not client:
            return []

        # Enhance query with hiring intent terms if not already present
        hiring_keywords = search_term
        lower_term = search_term.lower()
        if not any(k in lower_term for k in ["hiring", "looking for", "opportunity", "opening", "contract", "freelance"]):
            hiring_keywords = f"{search_term} hiring"

        logger.info(f"🔎 [LinkedIn Feed Scraper] Searching posts with query: '{hiring_keywords}' (Limit: {limit})")

        postings: List[RawJobPosting] = []

        try:
            raw_posts = client.search_posts(keywords=hiring_keywords, count=limit)
            if not raw_posts or not isinstance(raw_posts, list):
                logger.warning(f"No posts returned from LinkedIn API for '{hiring_keywords}'")
                return []

            logger.info(f"📥 Received {len(raw_posts)} raw LinkedIn feed posts.")

            for post in raw_posts:
                parsed_job = self._parse_post_to_job(post, fallback_query=search_term)
                if parsed_job:
                    postings.append(parsed_job)

            return postings

        except Exception as e:
            logger.error(f"Error searching LinkedIn feed posts: {e}")
            return []

    def _parse_post_to_job(self, post: Dict[str, Any], fallback_query: str) -> Optional[RawJobPosting]:
        """Parses raw LinkedIn post JSON into structured RawJobPosting model."""
        try:
            # 1. Extract Post Text / Commentary
            commentary = post.get("commentary", {})
            post_text = ""
            if isinstance(commentary, dict):
                post_text = commentary.get("text", "")
            elif isinstance(commentary, str):
                post_text = commentary

            if not post_text:
                # Try fallback text fields
                post_text = post.get("text", "") or post.get("summary", "") or str(post)

            if len(post_text.strip()) < 20:
                return None

            # 2. Extract Author Details
            actor = post.get("actor", {})
            author_name = "LinkedIn Member"
            author_headline = ""
            if isinstance(actor, dict):
                name_obj = actor.get("name", {})
                author_name = name_obj.get("text", "LinkedIn Member") if isinstance(name_obj, dict) else str(name_obj or "LinkedIn Member")
                sub_obj = actor.get("subDescription", {})
                author_headline = sub_obj.get("text", "") if isinstance(sub_obj, dict) else str(sub_obj or "")

            # 3. Construct Direct URL
            urn = post.get("entityUrn", "") or post.get("urn", "")
            if urn:
                post_url = f"https://www.linkedin.com/feed/update/{urn}"
            else:
                post_url = f"https://www.linkedin.com/feed/posts/{abs(hash(post_text))}"

            # 4. Extract Company from author headline or post text
            company = self._extract_company_from_headline(author_headline) or self._extract_company_from_text(post_text) or f"{author_name}'s Venture"

            # 5. Extract Inferred Title
            title = self._infer_job_title(post_text, fallback_query=fallback_query)

            # 6. Detect Job Type
            lower_text = post_text.lower()
            job_type = "Contract" if any(k in lower_text for k in ["contract", "freelance", "c2c", "gig", "part-time"]) else "Full-time"

            return RawJobPosting(
                title=title,
                company=company,
                location="Remote",
                job_url=post_url,
                site="linkedin_post",
                description=f"### LINKEDIN POST BY: {author_name} ({author_headline})\n\n{post_text}",
                job_type=job_type,
                date_posted="Recently",
                raw_metadata={
                    "author_name": author_name,
                    "author_headline": author_headline,
                    "post_urn": urn,
                }
            )

        except Exception as e:
            logger.debug(f"Error parsing post to RawJobPosting: {e}")
            return None

    def _extract_company_from_headline(self, headline: str) -> Optional[str]:
        """Extracts company name from author headline (e.g. 'Founder & CEO @ StartupX')."""
        if not headline:
            return None
        patterns = [
            r"(?:at|@)\s+([A-Za-z0-9\s&.,-]{2,30})(?:\s*\||\s*•|\s*,|\s*$)",
            r"(?:Founder|CEO|CTO|Director|Lead)\s+(?:of|at|@)\s+([A-Za-z0-9\s&.,-]{2,30})",
        ]
        for p in patterns:
            m = re.search(p, headline, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                if cand.lower() not in ("stealth", "stealth startup", "confidential", "linkedin"):
                    return cand
        return None

    def _extract_company_from_text(self, text: str) -> Optional[str]:
        """Heuristic to extract company from post body text."""
        patterns = [
            r"(?:at|@)\s+([A-Z][a-zA-Z0-9\s&]{2,25})\s+we\s+are",
            r"([A-Z][a-zA-Z0-9\s&]{2,25})\s+is\s+hiring",
            r"Join\s+(?:the\s+)?([A-Z][a-zA-Z0-9\s&]{2,25})\s+team",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                cand = m.group(1).strip()
                if cand.lower() not in ("we", "our", "the", "a", "an", "linkedin"):
                    return cand
        return None

    def _infer_job_title(self, text: str, fallback_query: str) -> str:
        """Infers job title from text or falls back to search query."""
        patterns = [
            r"(?:hiring|looking for|seeking)\s+(?:a|an)?\s+([A-Za-z\s/]{3,35})(?:\s+to|\s+who|\s+for|\s*\.|\s*!|\s*,|\s*\n)",
            r"(?:role|position|opening):\s*([A-Za-z\s/]{3,35})",
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                if len(cand) > 3 and len(cand) < 40:
                    return cand.title()
        return fallback_query.title() if fallback_query else "Freelance Hiring Opportunity"


# Global scraper instance
linkedin_feed_scraper = LinkedInFeedScraper()
