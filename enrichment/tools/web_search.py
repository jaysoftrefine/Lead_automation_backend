"""Tavily web search tool implementation for lead intelligence."""

from typing import List, Dict, Any, Optional
import os
from langchain_core.tools import tool
from config.settings import settings
from core.logging import logger

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None


class TavilySearchTool:
    """Wrapper around Tavily Client for research agent execution."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.tavily_api_key or os.environ.get("TAVILY_API_KEY")
        self._client = None
        if self.api_key and TavilyClient:
            self._client = TavilyClient(api_key=self.api_key)
        else:
            logger.warning("TAVILY_API_KEY is not set or tavily-python is not installed.")

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute web search for contact information, recruiter profiles, or company intelligence.
        """
        if not self._client:
            logger.warning(f"Search requested for '{query}' but TavilyClient is not initialized (missing API key).")
            return []

        logger.info(f"Executing Tavily web search: '{query}' (depth: {search_depth})")
        try:
            kwargs: Dict[str, Any] = {
                "query": query,
                "max_results": max_results,
                "search_depth": search_depth,
            }
            if include_domains:
                kwargs["include_domains"] = include_domains
            if exclude_domains:
                kwargs["exclude_domains"] = exclude_domains

            response = self._client.search(**kwargs)
            results = response.get("results", [])
            formatted = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0.0),
                }
                for r in results
            ]
            logger.info(f"Tavily returned {len(formatted)} results for query: '{query}'")
            return formatted

        except Exception as e:
            logger.error(f"Tavily search error for '{query}': {e}")
            return []


def get_tavily_search_tool(api_key: Optional[str] = None):
    """
    Creates a LangChain @tool function bound to the configured Tavily search instance.
    """
    search_service = TavilySearchTool(api_key=api_key)

    @tool
    def search_web_for_lead_info(
        query: str,
        search_focus: str = "contacts",
    ) -> str:
        """
        Search the live web using Tavily for company contact information, recruiter emails, phone numbers,
        LinkedIn profiles of decision-makers, or company domain details.

        Args:
            query: The precise search query (e.g., 'Acme Corp recruiter email OR hiring manager', 'Stripe engineering director San Francisco LinkedIn', 'info@techcompany.com phone contact')
            search_focus: The focus area - 'contacts', 'company_domain', or 'decision_makers'.
        """
        results = search_service.search(query=query, max_results=settings.max_search_results_per_lead)
        if not results:
            return f"No results found for query: '{query}'."

        formatted_output = []
        for idx, res in enumerate(results, start=1):
            formatted_output.append(
                f"[{idx}] Title: {res['title']}\n"
                f"    URL: {res['url']}\n"
                f"    Snippet: {res['content']}\n"
            )
        return "\n".join(formatted_output)

    return search_web_for_lead_info
