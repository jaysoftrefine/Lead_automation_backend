"""
web_search.py  –  Web Search & Lead Intelligence Tools

Bypasses Tavily by using DuckDuckGo Search (DDGS) and ReAct Chain-of-Thought
Google LLM agent for contact discovery and market intelligence.
"""

from typing import List, Dict, Any, Optional
import os
import time
from langchain_core.tools import tool
from config.settings import settings
from core.logging import logger
from ddgs import DDGS
from cot_search import CotSearchAgent, cot_search

# ==============================================================================
# TAVILY SEARCH (DEPRECATED & COMMENTED OUT)
# Replaced with Google LLM + DDGS search to avoid Tavily credit/usage limits.
# ==============================================================================
# try:
#     from tavily import TavilyClient
# except ImportError:
#     TavilyClient = None
#
# class TavilySearchTool:
#     def __init__(self, api_key: Optional[str] = None):
#         self.api_key = api_key or settings.tavily_api_key or os.environ.get("TAVILY_API_KEY")
#         self._client = TavilyClient(api_key=self.api_key) if (self.api_key and TavilyClient) else None
#     def search(self, query: str, max_results: int = 5, ...):
#         return self._client.search(...)
# ==============================================================================


class WebSearchTool:
    """
    High-resilience Web Search Tool powered by DDGS.
    Serves as a drop-in replacement for TavilySearchTool without requiring API credits.
    """

    def __init__(self, api_key: Optional[str] = None):
        # API key parameter kept for interface compatibility, but not needed for DDGS
        self.api_key = api_key
        logger.info("Initialized WebSearchTool with DDGS backend (Tavily bypassed).")

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "advanced",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute live web search for contacts, recruiter profiles, or company intelligence.
        Returns standardized snippets formatted identically to Tavily's output.
        """
        refined_query = query.strip()
        if include_domains:
            domain_filter = " OR ".join([f"site:{d.strip()}" for d in include_domains if d.strip()])
            if domain_filter:
                refined_query = f"{refined_query} ({domain_filter})"

        logger.info(f"🔎 Executing DDGS web search: '{refined_query}' (limit: {max_results})")
        raw_results = []
        max_attempts = 2

        for attempt in range(max_attempts):
            try:
                with DDGS() as ddgs:
                    raw_results = list(ddgs.text(refined_query, max_results=max_results))
                if raw_results:
                    break
            except Exception as e:
                logger.warning(f"DDGS search attempt {attempt + 1} error for '{refined_query}': {e}")
                time.sleep(1)

        formatted: List[Dict[str, Any]] = []
        for r in raw_results:
            url = r.get("href") or r.get("url") or ""
            if exclude_domains and any(ex.lower() in url.lower() for ex in exclude_domains):
                continue
            formatted.append({
                "title": r.get("title") or "",
                "url": url,
                "content": r.get("body") or r.get("content") or "",
                "score": 1.0,
            })

        logger.info(f"DDGS returned {len(formatted)} results for query: '{query}'")
        return formatted


# Drop-in alias so existing imports of TavilySearchTool continue to work seamlessly
TavilySearchTool = WebSearchTool


def get_web_search_tool(api_key: Optional[str] = None):
    """
    Creates a LangChain @tool function bound to the DDGS web search instance.
    """
    search_service = WebSearchTool(api_key=api_key)

    @tool
    def search_web_for_lead_info(
        query: str,
        search_focus: str = "contacts",
    ) -> str:
        """
        Search the live web for company contact information, recruiter emails, phone numbers,
        LinkedIn profiles of decision-makers, or company domain details.

        Args:
            query: The precise search query (e.g., 'Acme Corp recruiter email OR hiring manager', 'Stripe engineering director San Francisco LinkedIn')
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


# Backward-compatible alias
get_tavily_search_tool = get_web_search_tool

__all__ = [
    "WebSearchTool",
    "TavilySearchTool",
    "get_web_search_tool",
    "get_tavily_search_tool",
    "CotSearchAgent",
    "cot_search",
]
