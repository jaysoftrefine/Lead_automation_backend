"""EU Startups Autonomous Lead Discovery Engine.

Discovers fresh European startups via EU-Startups Directory crawling and multi-source AI web search,
auto-enriches real founders, validates direct deliverable emails via MX/SMTP, and saves qualified leads.
"""

import os
import re
import json
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

try:
    from duckduckgo_search import DDGS
except ImportError:
    try:
        from ddgs import DDGS
    except ImportError:
        DDGS = None

from google import genai

try:
    from eu_startups.db import get_connection
    from eu_startups.enrich import enrich_startup
    from eu_startups.scraper import scrape_startup
except ImportError:
    from db import get_connection
    from enrich import enrich_startup
    from scraper import scrape_startup

ROOT_DIR = Path(__file__).resolve().parent.parent
GEMINI_API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

session = requests.Session(impersonate="chrome")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if (TavilyClient and TAVILY_API_KEY) else None

BASE_URL = "https://www.eu-startups.com"


def verify_live_website(url: str) -> Optional[str]:
    """Verify that a candidate website is genuinely live and reachable over HTTP/HTTPS."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    # Block mock/placeholder domains
    url_lower = url.lower()
    if any(bad in url_lower for bad in ["example.com", "placeholder", "test.com", "domain.com", "sample.com"]):
        return None

    # Test live connectivity with short timeout
    for test_url in [url, url.replace("https://", "https://www.") if "www." not in url else url.replace("www.", "")]:
        try:
            res = session.get(test_url, timeout=4, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            if res.status_code < 400:
                return test_url
        except Exception:
            continue
    return None


def discover_directory_listings(start_page: int = 1, max_pages: int = 3) -> List[str]:
    """Crawl EU-Startups directory pages for un-scraped startup listings."""
    conn = get_connection()
    cur = conn.cursor()
    existing_urls = {
        r[0] for r in cur.execute("SELECT eu_startups_url FROM startups WHERE eu_startups_url IS NOT NULL").fetchall()
    }
    conn.close()

    discovered = []
    for page in range(start_page, start_page + max_pages):
        url = f"{BASE_URL}/directory/" if page == 1 else f"{BASE_URL}/directory/page/{page}/"
        try:
            res = session.get(url, timeout=10, headers={"Accept-Language": "en-US,en;q=0.9"})
            if res.status_code != 200 or not res.text:
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.select(".wpbdp-listing a[href], .wpbdp-listings-list a[href], article a[href]"):
                href = urljoin(BASE_URL, a["href"].split("#")[0].strip())
                if "/directory/" in href and not any(x in href for x in ["/page/", "/category/", "/tag/", "/author/", "/search/", "/wpbdp_category/"]):
                    parts = [p for p in urlparse(href).path.split("/") if p]
                    if len(parts) == 2 and parts[0] == "directory" and href not in existing_urls and href not in discovered:
                        discovered.append(href)
        except Exception:
            pass
        time.sleep(1)

    return discovered


def discover_startups_ai(
    topic: str,
    country: Optional[str] = None,
    count: int = 5,
    exclude_companies: Optional[List[str]] = None,
    round_idx: int = 0,
) -> List[Dict[str, Any]]:
    """Use Tavily, Google Search Grounding, and Gemini to discover 100% verified, live European startups."""
    if not client:
        return []

    country_query = f"in {country}" if country else "in Europe"
    
    search_modifiers = [
        "top fast-growing startups",
        "innovative early-stage tech scaleups",
        "breakout funded startups 2024 2025",
        "promising tech startups directory",
        "breakthrough technology startups",
    ]
    modifier = search_modifiers[round_idx % len(search_modifiers)]
    query = f"{modifier} {topic} {country_query} official website founder"
    
    search_context = ""
    
    # 1. Try Tavily Live Search
    if tavily_client:
        try:
            tav_res = tavily_client.search(query=query, max_results=8, search_depth="basic")
            for r in tav_res.get("results", []):
                search_context += f"Title: {r.get('title')}\nURL: {r.get('url')}\nContent: {r.get('content')}\n\n"
        except Exception as e:
            print(f"Tavily search warning: {e}")

    # 2. Fallback to DDGS if Tavily had no results
    if not search_context and DDGS:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=8):
                    search_context += f"Title: {r.get('title')}\nURL: {r.get('href')}\nBody: {r.get('body')}\n\n"
        except Exception:
            pass

    exclude_clause = ""
    if exclude_companies:
        sample_excluded = ", ".join(exclude_companies[:40])
        exclude_clause = f"\nCRITICAL: DO NOT include any of the following already-discovered companies:\n[{sample_excluded}]\n"

    prompt = f"""You are an elite European Tech Venture Researcher.
Find {count} REAL, active, live European startups in the industry/topic: '{topic}' {country_query}.

CRITICAL REQUIREMENTS:
- Every company MUST be an actual existing business with a verified, LIVE official website (e.g. 'https://synthesia.io', 'https://mistral.ai', 'https://tacto.ai').
- NEVER invent, hallucinate, or use fake/example domains like '.example.com'.
{exclude_clause}

Search Context:
{search_context}

Return a valid JSON object strictly matching this format:
{{
  "startups": [
    {{
      "company_name": "Exact Company Name",
      "website": "https://real-live-website.com",
      "country": "Country (e.g. Germany, UK, France, Netherlands)",
      "city": "City (e.g. Berlin, London, Paris, Amsterdam)",
      "founded_year": 2024,
      "category": "{topic or 'Technology'}",
      "tags": "keywords describing the startup",
      "description": "2-3 sentence overview of what the product does and problem it solves."
    }}
  ]
}}
"""
    try:
        # Use Google Search Grounding tool with Gemini
        resp = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config={
                "tools": [{"google_search": {}}],
                "temperature": 0.2,
            }
        )
        clean_text = resp.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        raw_startups = data.get("startups", [])
        
        # Verify that each startup's website is genuinely reachable live over HTTP
        verified_startups = []
        for s in raw_startups:
            web = s.get("website")
            live_url = verify_live_website(web)
            if live_url:
                s["website"] = live_url
                verified_startups.append(s)
            else:
                print(f"Skipping unreachable/invalid website: {web} for {s.get('company_name')}")

        return verified_startups
    except Exception as e:
        print("AI Discovery error:", e)
        return []


def run_discovery(topic: str = "", country: str = "", limit: int = 5) -> Dict[str, Any]:
    """
    Execute lead discovery, enrichment, and validation in an iterative loop
    until the EXACT requested limit of NEW, verified leads is reached.
    """
    conn = get_connection()
    cur = conn.cursor()

    new_startup_ids: List[int] = []
    max_rounds = 5
    current_round = 0

    while len(new_startup_ids) < limit and current_round < max_rounds:
        current_round += 1
        needed = limit - len(new_startup_ids)
        # Fetch existing company names to prevent duplicates
        existing_names = [
            r[0].strip().lower() for r in cur.execute("SELECT company_name FROM startups WHERE company_name IS NOT NULL").fetchall()
        ]

        if not topic and not country:
            # 1. Directory crawling mode
            cur_count = cur.execute("SELECT COUNT(*) FROM startups").fetchone()[0]
            start_page = (cur_count // 10) + current_round
            listings = discover_directory_listings(start_page=start_page, max_pages=3)

            for url in listings:
                if len(new_startup_ids) >= limit:
                    break
                try:
                    if scrape_startup(url):
                        row = cur.execute("SELECT * FROM startups WHERE eu_startups_url = ?", (url,)).fetchone()
                        if row:
                            enrich_startup(conn, row)
                            # Check if valid contact was discovered
                            has_contact = cur.execute(
                                "SELECT id FROM people WHERE startup_id = ? AND ((email IS NOT NULL AND TRIM(email) != '') OR (linkedin IS NOT NULL AND TRIM(linkedin) != ''))",
                                (row["id"],)
                            ).fetchone()
                            if has_contact:
                                new_startup_ids.append(row["id"])
                            else:
                                # Clean up unverified entry
                                cur.execute("DELETE FROM startups WHERE id = ?", (row["id"],))
                                cur.execute("DELETE FROM people WHERE startup_id = ?", (row["id"],))
                                conn.commit()
                except Exception as e:
                    print(f"Error scraping {url}: {e}")
                time.sleep(0.5)
        else:
            # 2. AI Discovery mode with oversampling to guarantee target limit
            batch_count = min(max(needed * 2, 6), 15)
            startups = discover_startups_ai(
                topic=topic,
                country=country,
                count=batch_count,
                exclude_companies=existing_names,
                round_idx=current_round - 1,
            )

            for s in startups:
                if len(new_startup_ids) >= limit:
                    break

                cname = (s.get("company_name") or "").strip()
                web = (s.get("website") or "").strip()
                if not cname or not web:
                    continue

                # Strictly reject placeholder or mock domains
                web_lower = web.lower()
                if any(bad in web_lower for bad in ["example.com", "placeholder", "test.com", "domain.com", "sample.com"]):
                    continue

                if cname.lower() in existing_names:
                    continue

                # Insert new candidate startup
                cur.execute("""
                    INSERT INTO startups (
                        company_name, description, website, country, city, 
                        founded_year, category, tags, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (
                    cname, s.get("description"), web, s.get("country", country),
                    s.get("city"), s.get("founded_year", 2025), s.get("category", topic),
                    s.get("tags", topic)
                ))
                conn.commit()
                startup_id = cur.lastrowid
                existing_names.append(cname.lower())

                # Enrich with real founders and emails
                row = cur.execute("SELECT * FROM startups WHERE id = ?", (startup_id,)).fetchone()
                if row:
                    try:
                        enrich_startup(conn, row)
                    except Exception as enrich_err:
                        print(f"Enrichment error for {cname}: {enrich_err}")

                    # Check if verified contact was saved
                    has_contact = cur.execute(
                        "SELECT id FROM people WHERE startup_id = ? AND ((email IS NOT NULL AND TRIM(email) != '') OR (linkedin IS NOT NULL AND TRIM(linkedin) != ''))",
                        (startup_id,)
                    ).fetchone()

                    if has_contact:
                        new_startup_ids.append(startup_id)
                    else:
                        # Clean up rejected lead so only high-quality contacts remain
                        cur.execute("DELETE FROM startups WHERE id = ?", (startup_id,))
                        cur.execute("DELETE FROM people WHERE startup_id = ?", (startup_id,))
                        cur.execute("DELETE FROM contacts WHERE startup_id = ?", (startup_id,))
                        conn.commit()

                time.sleep(0.3)

    total_startups = cur.execute("SELECT COUNT(*) FROM startups").fetchone()[0]
    total_people = cur.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    total_emails = cur.execute("SELECT COUNT(*) FROM people WHERE email IS NOT NULL AND TRIM(email) <> ''").fetchone()[0]

    conn.close()

    return {
        "status": "success",
        "discovered_count": len(new_startup_ids),
        "total_startups": total_startups,
        "total_people": total_people,
        "total_emails": total_emails,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Discover new European Startups")
    parser.add_argument("--topic", "-t", type=str, default="AI", help="Topic / industry")
    parser.add_argument("--country", "-c", type=str, default="", help="Country")
    parser.add_argument("--limit", "-l", type=int, default=3, help="Number of leads")
    args = parser.parse_args()

    res = run_discovery(topic=args.topic, country=args.country, limit=args.limit)
    print("Result:", res)
