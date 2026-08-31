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

session = requests.Session(impersonate="chrome")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

BASE_URL = "https://www.eu-startups.com"


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


def discover_startups_ai(topic: str, country: Optional[str] = None, count: int = 5) -> List[Dict[str, Any]]:
    """Use Gemini and search to discover new European startups in a specific domain."""
    if not client:
        return []

    country_query = f"in {country}" if country else "in Europe"
    query = f"top fastest growing European startups 2024 2025 2026 {topic} {country_query} founder CEO website"
    
    search_context = ""
    if DDGS:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=8):
                    search_context += f"Title: {r.get('title')}\nURL: {r.get('href')}\nBody: {r.get('body')}\n\n"
        except Exception:
            pass

    prompt = f"""You are an elite European Tech Venture Researcher.
Find {count} REAL, active, recently founded European startups in the industry/topic: '{topic}' {country_query}.

Search Context:
{search_context}

Return a JSON list of real European startups:
{{
  "startups": [
    {{
      "company_name": "Exact Company Name",
      "website": "https://company-domain.com",
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
        resp = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
            contents=prompt,
            config={"temperature": 0.2}
        )
        clean_text = resp.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        return data.get("startups", [])
    except Exception as e:
        print("AI Discovery error:", e)
        return []


def run_discovery(topic: str = "", country: str = "", limit: int = 5) -> Dict[str, Any]:
    """Execute lead discovery, enrichment, and validation."""
    conn = get_connection()
    cur = conn.cursor()

    new_startup_ids = []
    
    # 1. If no specific niche is given or user wants general European startups, crawl directory
    if not topic and not country:
        # Determine highest directory page to check
        cur_count = cur.execute("SELECT COUNT(*) FROM startups").fetchone()[0]
        start_page = (cur_count // 10) + 1
        listings = discover_directory_listings(start_page=start_page, max_pages=3)
        
        for url in listings[:limit]:
            try:
                if scrape_startup(url):
                    row = cur.execute("SELECT * FROM startups WHERE eu_startups_url = ?", (url,)).fetchone()
                    if row:
                        enrich_startup(conn, row)
                        new_startup_ids.append(row["id"])
            except Exception as e:
                print(f"Error scraping {url}: {e}")
            time.sleep(1)
    else:
        # 2. Topic/Country specific AI discovery
        startups = discover_startups_ai(topic=topic, country=country, count=limit)
        for s in startups:
            cname = s.get("company_name")
            web = s.get("website")
            if not cname or not web:
                continue
                
            existing = cur.execute("SELECT id FROM startups WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(?))", (cname,)).fetchone()
            if existing:
                startup_id = existing[0]
                cur.execute("""
                    UPDATE startups SET description = COALESCE(?, description), website = COALESCE(?, website), updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (s.get("description"), web, startup_id))
            else:
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
                startup_id = cur.lastrowid
            conn.commit()
            
            row = cur.execute("SELECT * FROM startups WHERE id = ?", (startup_id,)).fetchone()
            if row:
                enrich_startup(conn, row)
                new_startup_ids.append(startup_id)
            time.sleep(0.5)

    # 3. Clean up: keep only startups with verified founder email or LinkedIn
    keep_ids = [r[0] for r in cur.execute("""
        SELECT DISTINCT startup_id
        FROM people
        WHERE (email IS NOT NULL AND TRIM(email) <> '')
           OR (linkedin IS NOT NULL AND TRIM(linkedin) <> '')
    """).fetchall()]

    if keep_ids:
        placeholders = ",".join("?" for _ in keep_ids)
        cur.execute(f"DELETE FROM startups WHERE id NOT IN ({placeholders})", keep_ids)
        cur.execute(f"DELETE FROM people WHERE startup_id NOT IN ({placeholders})", keep_ids)
        cur.execute(f"DELETE FROM contacts WHERE startup_id NOT IN ({placeholders})", keep_ids)
        cur.execute(f"DELETE FROM crawl_status WHERE startup_id NOT IN ({placeholders})", keep_ids)
        conn.commit()

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
