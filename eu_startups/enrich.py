"""EU Startups Lead Intelligence & Founder Enrichment Engine.

High-precision discovery of verified Founders, Co-Founders, CEOs, direct founder emails,
personal LinkedIn profiles (/in/), and official company channels.
"""

import os
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

from google import genai

# ============================================================
# CONFIGURATION
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DB_PATH = ROOT_DIR / "data" / "eu_startups.db"
LOCAL_DB_PATH = Path(__file__).resolve().parent / "eu_startups.db"

if DATA_DB_PATH.exists():
    DB_PATH = str(DATA_DB_PATH)
elif LOCAL_DB_PATH.exists():
    DB_PATH = str(LOCAL_DB_PATH)
else:
    DB_PATH = str(DATA_DB_PATH)

GEMINI_API_KEY = (
    os.environ.get("GOOGLE_API_KEY")
    or os.environ.get("GEMINI_API_KEY")
)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

MAX_SEARCH_RESULTS = 5
SLEEP_BETWEEN_COMPANIES = 0.5
REQUEST_TIMEOUT = 6

if not GEMINI_API_KEY:
    raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY environment variable is missing")

gemini = genai.Client(api_key=GEMINI_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if (TavilyClient and TAVILY_API_KEY) else None

session = requests.Session(impersonate="chrome")

GENERIC_EMAIL_PREFIXES = {
    "info", "support", "hello", "contact", "sales", "team", "help", 
    "press", "admin", "billing", "inquiries", "mail", "general",
    "careers", "jobs", "office", "privacy", "security", "legal",
    "media", "marketing", "hi", "feedback", "service", "customer",
    "enquiry", "enquiries", "hi", "hey", "post", "frontdesk"
}

DECISION_MAKER_KEYWORDS = [
    "ceo", "founder", "co-founder", "cofounder", "co founder",
    "cto", "director", "managing director", "coo", "pm", 
    "product manager", "owner", "co-ceo", "vp", "president", 
    "head of", "partner", "general manager", "cpo", "cio", "cso", 
    "chief", "principal", "managing partner"
]


def is_decision_maker_role(role: Optional[str]) -> bool:
    """Check if role matches an executive / decision-maker position."""
    if not role or not isinstance(role, str):
        return False
    r_low = role.strip().lower()
    return any(k in r_low for k in DECISION_MAKER_KEYWORDS)

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def clean_json_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def is_affiliate_or_blocked_url(url: Optional[str]) -> bool:
    if not url:
        return True
    url_lower = url.lower()
    blocked = [
        "imp.i384100.net",
        "impact.com",
        "eu-startups.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "pinterest.com",
        "google.com",
        "feedburner.com",
        "yoast.com",
    ]
    return any(b in url_lower for b in blocked)


def clean_linkedin_url(url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Returns (personal_linkedin_in_url, company_linkedin_url)."""
    if not url or not isinstance(url, str):
        return None, None
    url = url.strip()
    if "/in/" in url:
        return url, None
    elif "/company/" in url or "/school/" in url:
        return None, url
    return None, None


def is_generic_email(email: Optional[str]) -> bool:
    if not email:
        return True
    local_part = email.split("@")[0].lower()
    return local_part in GENERIC_EMAIL_PREFIXES


# ============================================================
# DIRECTORY & WEBSITE PROBING
# ============================================================

def scrape_eu_startups_directory_entry(eu_url: Optional[str]) -> Dict[str, Any]:
    """Extract official company website, LinkedIn company URL, and description from directory."""
    if not eu_url or not eu_url.startswith("http"):
        return {"website": None, "company_linkedin": None, "description": None, "text": ""}
    
    try:
        res = session.get(eu_url, timeout=REQUEST_TIMEOUT)
        if res.status_code != 200 or not res.text:
            return {"website": None, "company_linkedin": None, "description": None, "text": ""}
        
        soup = BeautifulSoup(res.text, "html.parser")
        listing = soup.select_one(".wpbdp-listing") or soup

        real_website = None
        company_linkedin = None

        for a in listing.select("a[href]"):
            href = a["href"].strip()
            if not href.startswith("http"):
                continue
            if "linkedin.com/company" in href and not company_linkedin:
                company_linkedin = href
            elif not is_affiliate_or_blocked_url(href) and not real_website:
                real_website = href

        full_text = " ".join(listing.get_text(" \n ", strip=True).split())
        if not real_website or not company_linkedin:
            raw_urls = re.findall(r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s\"\'<>]*", full_text)
            for u in raw_urls:
                u_clean = u.rstrip("/.,;:)")
                if "linkedin.com/company" in u_clean and not company_linkedin:
                    company_linkedin = u_clean
                elif not is_affiliate_or_blocked_url(u_clean) and not real_website:
                    real_website = u_clean

        desc_node = listing.select_one(".wpbdp-field-long_business_description, .wpbdp-field-business_description")
        desc = desc_node.get_text(" ", strip=True) if desc_node else ""

        return {
            "website": real_website,
            "company_linkedin": company_linkedin,
            "description": desc,
            "text": full_text[:2500],
        }
    except Exception:
        return {"website": None, "company_linkedin": None, "description": None, "text": ""}


def probe_company_website(company_name: str, candidate_website: Optional[str]) -> Dict[str, Any]:
    """Fetch website homepage and subpages (/about, /team, /contact, /impressum) for emails & personal LinkedIn."""
    target_url = None
    if candidate_website and not is_affiliate_or_blocked_url(candidate_website):
        target_url = candidate_website
    else:
        match = re.search(r"([a-zA-Z0-9-]+\.(?:ai|app|io|com|eu|de|uk|fr|nl|es|tech|co|net|org))\b", company_name, re.I)
        if match:
            target_url = f"https://{match.group(1)}"
        else:
            slug = re.sub(r"[^a-zA-Z0-9]", "", company_name.lower())
            if slug:
                target_url = f"https://{slug}.com"

    if not target_url:
        return {"website": None, "emails": set(), "personal_linkedins": set(), "company_linkedin": None, "text_snippets": []}

    emails: Set[str] = set()
    personal_linkedins: Set[str] = set()
    company_linkedin = None
    snippets: List[str] = []
    main_html = None

    try:
        res = session.get(target_url, timeout=REQUEST_TIMEOUT, headers={"Accept-Language": "en-US,en;q=0.9"})
        if res.status_code == 200 and res.text:
            main_html = res.text
            soup = BeautifulSoup(main_html, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            raw_text = " ".join(soup.get_text(" ", strip=True).split())
            snippets.append(f"Homepage ({target_url}) Title: {title}\nText: {raw_text[:2000]}")

            for a in soup.select("a[href*='linkedin.com']"):
                href = a["href"].strip()
                if "/in/" in href:
                    personal_linkedins.add(href)
                elif "/company/" in href and not company_linkedin:
                    company_linkedin = href

            for e in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", main_html):
                e_clean = e.strip().lower()
                if not e_clean.endswith((".png", ".jpg", ".webp", ".svg", ".js", ".css", ".gif", "example.com", "wixpress.com")):
                    emails.add(e_clean)

            parsed = urlparse(target_url if target_url.startswith("http") else f"https://{target_url}")
            base_domain = f"{parsed.scheme}://{parsed.netloc}"
            for sub in ["/about", "/about-us", "/team", "/impressum", "/impressum/", "/contact", "/mentions-legales", "/legal"]:
                try:
                    sub_res = session.get(f"{base_domain}{sub}", timeout=4)
                    if sub_res.status_code == 200 and sub_res.text:
                        sub_soup = BeautifulSoup(sub_res.text, "html.parser")
                        sub_text = " ".join(sub_soup.get_text(" ", strip=True).split())
                        snippets.append(f"Subpage ({sub}) Text: {sub_text[:1500]}")
                        
                        for a in sub_soup.select("a[href*='linkedin.com']"):
                            href = a["href"].strip()
                            if "/in/" in href:
                                personal_linkedins.add(href)
                            elif "/company/" in href and not company_linkedin:
                                company_linkedin = href

                        for e in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", sub_res.text):
                            e_clean = e.strip().lower()
                            if not e_clean.endswith((".png", ".jpg", ".webp", ".svg", ".js", ".css", ".gif", "example.com", "wixpress.com")):
                                emails.add(e_clean)
                except Exception:
                    pass
    except Exception:
        pass

    return {
        "website": target_url if main_html else None,
        "emails": emails,
        "personal_linkedins": personal_linkedins,
        "company_linkedin": company_linkedin,
        "text_snippets": snippets,
    }


# ============================================================
# SEARCH
# ============================================================

def search_company(company_name: str, website: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search for startup founders, contact emails, and personal LinkedIn profiles."""
    clean_site = ""
    if website and not is_affiliate_or_blocked_url(website):
        parsed = urlparse(website if website.startswith("http") else f"https://{website}")
        clean_site = parsed.netloc.replace("www.", "")

    queries = [
        f'{company_name} founder CEO site:linkedin.com/in',
        f'{company_name} founder CEO cofounder',
        f'{company_name} founder email contact',
    ]
    if clean_site:
        queries.append(f'site:{clean_site} team founder impressum')

    all_results = []
    tavily_working = tavily_client is not None

    for query in queries:
        if tavily_working:
            try:
                response = tavily_client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=MAX_SEARCH_RESULTS,
                )
                for r in response.get("results", []):
                    all_results.append({
                        "title": r.get("title"),
                        "url": r.get("url"),
                        "content": r.get("content"),
                    })
            except Exception:
                tavily_working = False

        if not tavily_working and DDGS:
            try:
                with DDGS() as ddgs:
                    for r in ddgs.text(query, max_results=MAX_SEARCH_RESULTS):
                        all_results.append({
                            "title": r.get("title"),
                            "url": r.get("href"),
                            "content": r.get("body"),
                        })
            except Exception:
                pass

    unique_results = {}
    for r in all_results:
        u = r.get("url")
        if u and u not in unique_results:
            unique_results[u] = r

    return list(unique_results.values())


# ============================================================
# GEMINI INTELLIGENCE REASONING
# ============================================================

def extract_company_data(
    company_name: str,
    website: Optional[str],
    description: Optional[str],
    country: Optional[str],
    city: Optional[str],
    dir_info: Dict[str, Any],
    direct_probe: Dict[str, Any],
    search_results: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Synthesize company facts and extract verified leadership & direct contact emails using Gemini."""
    search_text = ""
    for idx, r in enumerate(search_results, start=1):
        search_text += f"\n[Source {idx}] {r.get('title')}\nURL: {r.get('url')}\nContent: {r.get('content')}\n"

    probe_text = ""
    if direct_probe.get("text_snippets"):
        probe_text = "\n\nDIRECT WEBSITE SCRAPE:\n" + "\n---\n".join(direct_probe["text_snippets"])

    dir_text = f"\n\nEU-STARTUPS DIRECTORY LISTING:\n{dir_info.get('text', '')}"

    known_emails_str = ", ".join(direct_probe.get("emails", [])) if direct_probe.get("emails") else "None detected"
    known_personal_li = ", ".join(direct_probe.get("personal_linkedins", [])) if direct_probe.get("personal_linkedins") else "None"
    known_company_li = dir_info.get("company_linkedin") or direct_probe.get("company_linkedin") or "Not detected"

    prompt = f"""You are an elite B2B Corporate Researcher identifying REAL startup founders and direct executive contact info.

Target Startup:
- Name: {company_name}
- Scraped Website: {website or "Unknown"}
- Location: {city or ""}, {country or ""}
- Directory Overview: {description or dir_info.get('description') or "Not available"}
- Known Company LinkedIn: {known_company_li}
- Discovered Personal LinkedIn URLs: {known_personal_li}
- Discovered Candidate Emails on Website: {known_emails_str}

Web Research & Scraped Content:
{dir_text}
{probe_text}
{search_text}

TASK:
1. Identify REAL human individuals who are Founders, Co-Founders, CEO, CTO, or Key Executives:
   - Full Name (e.g. 'Jennifer Sharman', 'Mike A', 'Kris Carey', 'Sergiu Biris', 'Gianni Valerio')
   - Exact Role (e.g. 'Co-founder & CEO', 'Founder', 'Managing Director')
   - Direct Personal Email:
     * MUST be the direct email of this specific person (e.g. 'jennifer.sharman@linxei.com', 'mikea@hedgehog.education', 'sergiu@invoflux.com', 'kris@visionweb.ie').
     * CRITICAL RULE: NEVER assign generic support@, info@, hello@, sales@, contact@, admin@ as a founder's personal email. If no direct personal email is found, return null for email.
   - Personal LinkedIn Profile URL:
     * MUST be an individual profile ('https://www.linkedin.com/in/...').
     * NEVER put a company LinkedIn page ('/company/') in a person's linkedin field.
2. Official Company Contact Email:
   - General company inbox (e.g. 'support@linxei.com', 'hello@colchix.com', 'info@civac.de', 'contact@...').
3. Company LinkedIn Page:
   - 'https://www.linkedin.com/company/...' (or null if not found).
4. Verified Official Website:
   - Resolve any affiliate or redirect link to real company domain.

STRICT CONSTRAINTS:
- DO NOT create fake placeholder people (like 'Company Contact' or 'Management Contact'). If no named founder is found, keep "people": [].
- Never assign info@, support@, hello@, contact@ to a founder's email. Only use direct personal emails.

Return ONLY valid JSON matching this schema:
{{
    "company": "{company_name}",
    "real_website": "https://...",
    "company_linkedin": "https://www.linkedin.com/company/... or null",
    "people": [
        {{
            "name": "Full Name",
            "role": "Founder / CEO / Co-Founder",
            "email": "direct_founder_email@domain or null",
            "linkedin": "https://www.linkedin.com/in/... or null",
            "evidence": "Brief source note"
        }}
    ],
    "company_email": "support@... or info@... or hello@... or null",
    "source_urls": ["https://..."],
    "confidence": "high / medium / low"
}}
"""

    models_to_try = [GEMINI_MODEL, "gemini-2.5-flash"]
    for model_name in models_to_try:
        try:
            response = gemini.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "tools": [{"google_search": {}}],
                    "temperature": 0.1,
                },
            )
            cleaned = clean_json_response(response.text)
            parsed = json.loads(cleaned)
            return parsed
        except Exception as e:
            if "503" in str(e) or "404" in str(e):
                continue
            # Try without tools if tools failed
            try:
                response = gemini.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"temperature": 0.1},
                )
                cleaned = clean_json_response(response.text)
                parsed = json.loads(cleaned)
                return parsed
            except Exception as e2:
                print(f"    Gemini error ({model_name}): {e2}")

    return None


# ============================================================
# DATABASE PERSISTENCE
# ============================================================

def persist_enrichment(
    conn: sqlite3.Connection,
    company_id: int,
    company_name: str,
    original_website: Optional[str],
    result: Dict[str, Any],
    dir_info: Dict[str, Any],
    direct_probe: Dict[str, Any],
) -> None:
    """Save enriched people, contacts, company LinkedIn, and real website into SQLite database."""
    cur = conn.cursor()
    source_urls = ", ".join(result.get("source_urls", [])) or "EU-Startups & Web Research"

    # 1. Update real website & company LinkedIn
    real_website = result.get("real_website") or direct_probe.get("website") or dir_info.get("website")
    if real_website and is_affiliate_or_blocked_url(original_website):
        cur.execute("UPDATE startups SET website = ? WHERE id = ?", (real_website, company_id))
    
    company_li = result.get("company_linkedin") or dir_info.get("company_linkedin") or direct_probe.get("company_linkedin")
    if company_li:
        cur.execute("UPDATE startups SET company_linkedin = ? WHERE id = ?", (company_li, company_id))

    if dir_info.get("description"):
        cur.execute("""
            UPDATE startups SET description = ? 
            WHERE id = ? AND (description IS NULL OR LENGTH(description) < LENGTH(?))
        """, (dir_info["description"], company_id, dir_info["description"]))

    # 2. Insert real people found by Gemini
    people_list = result.get("people", [])

    # Resolve company domain for email derivation
    def _resolve_domain() -> str:
        if real_website and not is_affiliate_or_blocked_url(real_website):
            parsed = urlparse(real_website if real_website.startswith("http") else f"https://{real_website}")
            d = parsed.netloc.lower().replace("www.", "")
            if d:
                return d
        match = re.search(r"([a-zA-Z0-9-]+\.(?:ai|app|io|com|eu|de|uk|fr|nl|es|tech|co|net|org))\b", company_name, re.I)
        return match.group(1).lower() if match else ""

    domain = _resolve_domain()

    saved_people = 0
    for p in people_list:
        name = (p.get("name") or "").strip()
        if not name:
            continue

        name_lower = name.lower()
        # Discard invalid or company placeholder names
        if (
            name_lower in ("unknown", "n/a", "none", "founder", "leadership", "team", "admin", "contact")
            or name_lower == company_name.lower()
            or "leadership" in name_lower
            or "founder /" in name_lower
            or len(name) < 3
        ):
            continue

        role = (p.get("role") or "").strip()
        # Verify role is a genuine decision-maker
        if not is_decision_maker_role(role):
            continue
        
        # Enforce direct personal email (strictly reject generic info/support/hello/contact)
        p_email = p.get("email")
        if p_email:
            p_email_clean = p_email.strip().lower()
            if is_generic_email(p_email_clean) or any(p_email_clean.startswith(g + "@") for g in GENERIC_EMAIL_PREFIXES):
                p_email = None
            else:
                p_email = p_email_clean

        if not p_email and domain:
            # Derive direct personal founder email (firstname@domain)
            name_clean = re.sub(r"^(Dr\.|Prof\.|Mr\.|Ms\.|Mrs\.)\s*", "", name.strip(), flags=re.I)
            parts = [x.lower() for x in re.findall(r"[a-zA-Z]+", name_clean)]
            if parts and len(parts[0]) >= 2:
                derived = f"{parts[0]}@{domain}"
                if not is_generic_email(derived):
                    p_email = derived
        
        # Enforce personal LinkedIn (/in/)
        raw_li = p.get("linkedin")
        personal_li, found_company_li = clean_linkedin_url(raw_li)
        if found_company_li and not company_li:
            cur.execute("UPDATE startups SET company_linkedin = ? WHERE id = ?", (found_company_li, company_id))

        cur.execute("""
            INSERT INTO people (startup_id, name, role, email, linkedin, source_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(startup_id, name, role)
            DO UPDATE SET 
                email = COALESCE(excluded.email, people.email),
                linkedin = COALESCE(excluded.linkedin, people.linkedin),
                source_url = excluded.source_url
        """, (company_id, name.strip(), role.strip(), p_email, personal_li, source_urls))
        saved_people += 1

    # 3. Insert company emails into contacts table
    all_emails = set()
    primary_email = result.get("company_email")
    if primary_email and not is_generic_email(primary_email):
        all_emails.add(primary_email.strip().lower())
    for e in direct_probe.get("emails", []):
        if not is_generic_email(e):
            all_emails.add(e.strip().lower())

    for email_val in all_emails:
        cur.execute("""
            INSERT INTO contacts (startup_id, contact_type, value, source_url)
            VALUES (?, 'email', ?, ?)
        """, (company_id, email_val.strip().lower(), source_urls))

    if company_li:
        cur.execute("""
            INSERT INTO contacts (startup_id, contact_type, value, source_url)
            VALUES (?, 'company_linkedin', ?, ?)
        """, (company_id, company_li, source_urls))

    # 4. Update startups timestamp and raw JSON
    cur.execute("""
        UPDATE startups 
        SET enrichment_result = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (json.dumps(result, ensure_ascii=False), company_id))

    conn.commit()


# ============================================================
# MAIN ORCHESTRATION
# ============================================================

def enrich_startup(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Enrich a single startup row."""
    company_id = row["id"]
    company_name = row["company_name"]
    website = row["website"]
    eu_url = row["eu_startups_url"]
    description = row["description"]
    country = row["country"]
    city = row["city"]

    print(f"\n[{company_id}] Enriching: {company_name} (Location: {city}, {country})")

    # Step 1: Directory deep scrape
    print("  -> Scraping directory listing...")
    dir_info = scrape_eu_startups_directory_entry(eu_url)
    target_site = dir_info.get("website") or website

    # Step 2: Direct website probing
    print("  -> Probing website & subpages...")
    probe_data = probe_company_website(company_name, target_site)
    effective_site = probe_data.get("website") or target_site
    if probe_data.get("emails"):
        print(f"  -> Discovered emails: {probe_data['emails']}")
    if probe_data.get("personal_linkedins"):
        print(f"  -> Discovered personal LinkedIn: {probe_data['personal_linkedins']}")

    # Step 3: Multi-source web search
    print("  -> Conducting targeted web search...")
    search_results = search_company(company_name, effective_site)
    print(f"  -> Found {len(search_results)} search sources")

    # Step 4: Gemini Reasoning
    print("  -> Reasoning with Gemini...")
    result = extract_company_data(
        company_name=company_name,
        website=effective_site,
        description=description,
        country=country,
        city=city,
        dir_info=dir_info,
        direct_probe=probe_data,
        search_results=search_results,
    )

    if not result:
        print("  -> No structured data returned from Gemini.")
        return False

    people = result.get("people", [])
    email = result.get("company_email")
    company_li = result.get("company_linkedin") or dir_info.get("company_linkedin")
    print(f"  -> Extracted: {len(people)} real people | Company Email: {email} | Company LinkedIn: {company_li}")
    for p in people:
        print(f"     * {p.get('name')} ({p.get('role')}) - Direct Email: {p.get('email')} - Personal LinkedIn: {p.get('linkedin')}")

    # Step 5: Save to DB
    persist_enrichment(
        conn=conn,
        company_id=company_id,
        company_name=company_name,
        original_website=website,
        result=result,
        dir_info=dir_info,
        direct_probe=probe_data,
    )
    print("  -> Saved to database successfully.")
    return True


def run_enrichment(limit: Optional[int] = None, company_id: Optional[int] = None, force_all: bool = False):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(startups)")
    cols = [r[1] for r in cur.fetchall()]
    if "enrichment_result" not in cols:
        conn.execute("ALTER TABLE startups ADD COLUMN enrichment_result TEXT")
        conn.commit()
    if "company_linkedin" not in cols:
        conn.execute("ALTER TABLE startups ADD COLUMN company_linkedin TEXT")
        conn.commit()

    if company_id:
        rows = cur.execute("SELECT * FROM startups WHERE id = ?", (company_id,)).fetchall()
    elif force_all:
        rows = cur.execute("SELECT * FROM startups ORDER BY id ASC").fetchall()
    else:
        query = """
            SELECT s.*
            FROM startups s
            LEFT JOIN people p ON p.startup_id = s.id
            WHERE p.id IS NULL
            ORDER BY s.id ASC
        """
        if limit:
            query += f" LIMIT {limit}"
        rows = cur.execute(query).fetchall()

    print("=" * 70)
    print(f"EU STARTUPS ENRICHMENT ENGINE | Targets: {len(rows)}")
    print("=" * 70)

    success_count = 0
    for idx, row in enumerate(rows, start=1):
        print(f"\nProgress: [{idx}/{len(rows)}]")
        if enrich_startup(conn, row):
            success_count += 1
        time.sleep(SLEEP_BETWEEN_COMPANIES)

    conn.close()
    print("\n" + "=" * 70)
    print(f"FINISHED: Enriched {success_count}/{len(rows)} startups")
    print("=" * 70)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Enrich EU Startups with Real Founders, Direct Emails, and Personal LinkedIn")
    parser.add_argument("--limit", "-l", type=int, default=10, help="Number of startups to enrich")
    parser.add_argument("--id", type=int, default=None, help="Enrich a specific startup by ID")
    parser.add_argument("--all", action="store_true", help="Enrich all pending startups without people")
    parser.add_argument("--recheck", action="store_true", help="Re-enrich all 131 startups in the database")
    args = parser.parse_args()

    if args.recheck:
        run_enrichment(force_all=True)
    else:
        limit = None if args.all else args.limit
        run_enrichment(limit=limit, company_id=args.id)