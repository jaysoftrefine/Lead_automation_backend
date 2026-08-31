import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

try:
    from eu_startups.db import get_connection
except ImportError:
    from db import get_connection


BASE_URL = "https://www.eu-startups.com"
DIRECTORY_URL = f"{BASE_URL}/directory/"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

session = requests.Session(impersonate="chrome")


def get_page(url):
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        print(f"[{response.status_code}] {url}")
        if response.status_code != 200:
            return None
        return response.text
    except Exception as e:
        print(f"Request error: {url} -> {e}")
        return None


def clean_text(value):
    if not value:
        return None
    return " ".join(value.split()).strip() or None


def get_text(element):
    return clean_text(element.get_text(" ", strip=True)) if element else None


def extract_email(text):
    if not text:
        return None
    match = re.search(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        text,
    )
    return match.group(0) if match else None


def extract_year(text):
    if not text:
        return None
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    return int(match.group(1)) if match else None


def is_eu_startups_url(url):
    parsed = urlparse(url)
    return (
        parsed.netloc.lower().endswith("eu-startups.com")
        and parsed.path.startswith("/directory/")
    )


def is_listing_url(url):
    """Accept only URLs that look like individual directory entries."""
    if not is_eu_startups_url(url):
        return False

    path = urlparse(url).path.rstrip("/") + "/"

    # Directory index and pagination are not startup listings.
    if path == "/directory/" or re.fullmatch(r"/directory/page/\d+/", path):
        return False

    # These are site/navigation pages, not listings.
    blocked = (
        "/directory/category/",
        "/directory/tag/",
        "/directory/search/",
        "/directory/author/",
        "/directory/page/",
    )
    if any(path.startswith(x) for x in blocked):
        return False

    # An individual directory entry is normally /directory/<slug>/
    parts = [x for x in path.split("/") if x]
    return len(parts) == 2 and parts[0] == "directory"


def find_listing_container(a):
    """
    Find the nearest container that represents one directory card.
    We intentionally do NOT parse all <a> tags on the page.
    """
    for parent in a.parents:
        classes = " ".join(parent.get("class", []))
        ident = parent.get("id", "")
        marker = f"{classes} {ident}".lower()

        if any(
            word in marker
            for word in (
                "wpbdp-listing",
                "business-listing",
                "listing-card",
                "directory-listing",
                "wpbdp-listings-list",
            )
        ):
            return parent

        # Avoid climbing to body/main/page-level containers.
        if parent.name in ("body", "html", "main"):
            break

    return a.parent


def extract_startup_links(html):
    """
    Extract only individual /directory/<slug>/ links.
    The previous implementation collected every internal EU-Startups
    link, which caused navigation/menu/CSS text to become records.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    # Prefer links inside known directory/listing containers.
    candidates = []
    for selector in (
        ".wpbdp-listing a[href]",
        ".wpbdp-listings-list a[href]",
        ".business-listing a[href]",
        ".listing-card a[href]",
        "article a[href]",
    ):
        candidates.extend(soup.select(selector))

    # Fallback: inspect links but validate the URL strictly.
    if not candidates:
        candidates = soup.find_all("a", href=True)

    for a in candidates:
        href = urljoin(BASE_URL, a.get("href", "").strip())
        href = href.split("#", 1)[0]

        if not is_listing_url(href):
            continue

        # A real listing link should have meaningful anchor text.
        anchor = get_text(a)
        if not anchor or len(anchor) < 2:
            continue

        links.add(href)

    return links


def extract_labeled_value(soup, labels):
    """
    Extract a value from the actual field row/container, rather than
    searching the entire page for a matching string.

    Supports common WP Business Directory Plugin structures:
      .wpbdp-field-xxx
      label + value
      field title + sibling
    """
    labels = [x.lower().strip() for x in labels]

    # 1) WPBDP field classes.
    for label in labels:
        slug = re.sub(r"[^a-z0-9]+", "-", label).strip("-")
        for selector in (
            f".wpbdp-field-{slug}",
            f".wpbdp-field-{slug} .value",
            f"[class*='wpbdp-field-{slug}']",
        ):
            node = soup.select_one(selector)
            if not node:
                continue

            # If the selector is the whole field, remove label text.
            value = get_text(node)
            if value:
                for original in labels:
                    value = re.sub(
                        rf"^\s*{re.escape(original)}\s*:?\s*",
                        "",
                        value,
                        flags=re.I,
                    )
                value = clean_text(value)
                if value and value.lower() not in labels:
                    return value

    # 2) Explicit label/title/value structure.
    for node in soup.find_all(["label", "dt", "strong", "b", "span", "div"]):
        title = get_text(node)
        if not title:
            continue

        normalized = re.sub(r"[:\s]+$", "", title).lower()
        if normalized not in labels:
            continue

        # Same parent: look for .value or a likely value element.
        parent = node.parent
        if parent:
            value_node = parent.select_one(".value")
            if value_node and value_node is not node:
                value = get_text(value_node)
                if value:
                    return value

            siblings = list(parent.children)
            try:
                pos = siblings.index(node)
                for sibling in siblings[pos + 1:]:
                    if getattr(sibling, "name", None):
                        value = get_text(sibling)
                        if value and value.lower() not in labels:
                            return value
            except ValueError:
                pass

        # Next sibling / element.
        sibling = node.find_next_sibling()
        if sibling:
            value = get_text(sibling)
            if value and value.lower() not in labels:
                return value

    return None


def extract_linked_website(soup):
    # Prefer links whose visible text indicates the company's own website.
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = (get_text(a) or "").lower()

        if not href.startswith(("http://", "https://")):
            continue

        host = urlparse(href).netloc.lower()
        if "eu-startups.com" in host or "linkedin.com" in host:
            continue

        if any(word in text for word in ("website", "visit website", "company website")):
            return href

    # Fallback to external links, excluding common social/tracking links.
    ignored_hosts = (
        "facebook.com",
        "twitter.com",
        "x.com",
        "instagram.com",
        "linkedin.com",
        "youtube.com",
        "tiktok.com",
        "pinterest.com",
    )

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href.startswith(("http://", "https://")):
            continue

        host = urlparse(href).netloc.lower()
        if "eu-startups.com" in host:
            continue
        if any(x in host for x in ignored_hosts):
            continue

        return href

    return None


def split_location(location):
    """
    Best-effort location split.
    EU-Startups commonly provides city/location in 'Based in'.
    We keep the complete value as city when it cannot safely be split.
    """
    location = clean_text(location)
    if not location:
        return None, None

    # Do not invent state information.
    return None, location


def extract_people(soup):
    """
    Extract people only from explicit person/team/contact structures.
    We never turn arbitrary page text into a person.
    """
    people = []

    # Common selectors for team/contact blocks.
    selectors = (
        ".wpbdp-field-founder",
        ".wpbdp-field-founders",
        ".wpbdp-field-ceo",
        ".wpbdp-field-cto",
        ".wpbdp-field-co-founder",
        ".wpbdp-field-cofounder",
        ".founder",
        ".founders",
        ".team-member",
        ".person",
        ".contact-person",
    )

    for selector in selectors:
        for node in soup.select(selector):
            text = get_text(node)
            if not text:
                continue

            # Determine role from class/field name.
            role = "Contact"
            low = selector.lower()
            if "founder" in low:
                role = "Founder"
            elif "co-founder" in low or "cofounder" in low:
                role = "Co-Founder"
            elif "ceo" in low:
                role = "CEO"
            elif "cto" in low:
                role = "CTO"

            email = extract_email(text)
            linkedin = None
            link = node.select_one("a[href*='linkedin.com']")
            if link:
                linkedin = link.get("href")

            # Remove email from candidate name.
            name = re.sub(r"\S+@\S+\.\S+", "", text)
            name = clean_text(name)

            if name and len(name) <= 150:
                people.append({
                    "name": name,
                    "role": role,
                    "email": email,
                    "linkedin": linkedin,
                })

    # Explicit labels such as "Founder: John Smith".
    for label, role in (
        ("Founder", "Founder"),
        ("Co-Founder", "Co-Founder"),
        ("Cofounder", "Co-Founder"),
        ("CEO", "CEO"),
        ("CTO", "CTO"),
    ):
        value = extract_labeled_value(soup, [label])
        if value:
            email = extract_email(value)
            name = clean_text(re.sub(r"\S+@\S+\.\S+", "", value))
            if name:
                people.append({
                    "name": name,
                    "role": role,
                    "email": email,
                    "linkedin": None,
                })

    # Deduplicate.
    unique = {}
    for person in people:
        key = (
            (person["name"] or "").strip().lower(),
            (person["role"] or "").strip().lower(),
        )
        if key[0]:
            unique[key] = person

    return list(unique.values())


def save_startup(data):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO startups (
            company_name, description, website, eu_startups_url,
            country, state, city, founded_year, category, tags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(eu_startups_url)
        DO UPDATE SET
            company_name = excluded.company_name,
            description = excluded.description,
            website = excluded.website,
            country = excluded.country,
            state = excluded.state,
            city = excluded.city,
            founded_year = excluded.founded_year,
            category = excluded.category,
            tags = excluded.tags,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            data["company_name"],
            data["description"],
            data["website"],
            data["eu_startups_url"],
            data["country"],
            data["state"],
            data["city"],
            data["founded_year"],
            data["category"],
            data["tags"],
        ),
    )

    conn.commit()

    row = cursor.execute(
        "SELECT id FROM startups WHERE eu_startups_url = ?",
        (data["eu_startups_url"],),
    ).fetchone()

    startup_id = row["id"] if row else None
    conn.close()
    return startup_id


def save_person(startup_id, name, role, email, linkedin, source_url):
    if not startup_id or not name:
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO people (
            startup_id, name, role, email, linkedin, source_url
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(startup_id, name, role)
        DO UPDATE SET
            email = COALESCE(excluded.email, people.email),
            linkedin = COALESCE(excluded.linkedin, people.linkedin),
            source_url = excluded.source_url
        """,
        (startup_id, name, role, email, linkedin, source_url),
    )

    conn.commit()
    conn.close()


def save_contact(startup_id, contact_type, value, source_url):
    if not startup_id or not value:
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO contacts (
            startup_id, contact_type, value, source_url
        )
        VALUES (?, ?, ?, ?)
        """,
        (startup_id, contact_type, value, source_url),
    )

    conn.commit()
    conn.close()


def scrape_startup(url):
    html = get_page(url)
    if not html:
        return False

    soup = BeautifulSoup(html, "html.parser")

    # The old scraper used page-wide field searching. This is intentionally
    # replaced with structured extraction + strict validation.
    company_name = None

    for selector in (
        ".wpbdp-field-business-name .value",
        ".wpbdp-field-business-name",
        ".business-name",
        ".wpbdp-listing-title",
        "article h1",
        "main h1",
        "h1",
    ):
        node = soup.select_one(selector)
        value = get_text(node)
        if value:
            company_name = value
            break

    # Reject pages that are clearly not startup entries.
    if not company_name:
        print(f"SKIP: no company name -> {url}")
        return False

    bad_names = {
        "home", "about", "contact", "login", "sign in",
        "magazine", "interviews", "job board", "directory",
        "category", "tags",
    }
    if company_name.lower() in bad_names:
        print(f"SKIP: navigation page -> {url}")
        return False

    description = None
    meta = soup.select_one('meta[name="description"]')
    if meta:
        description = clean_text(meta.get("content"))

    if not description:
        for selector in (
            ".wpbdp-field-description .value",
            ".wpbdp-field-description",
            ".business-description",
            "article .entry-content",
        ):
            node = soup.select_one(selector)
            value = get_text(node)
            if value:
                description = value
                break

    # EU-Startups directory's "Category" is commonly the country.
    # Prefer the exact directory field and fall back to "Country".
    country = extract_labeled_value(soup, ["Category", "Country"])

    location = extract_labeled_value(soup, ["Based in", "Location", "City"])
    state, city = split_location(location)

    # Tags are the actual startup topic/category values.
    tags = extract_labeled_value(soup, ["Tags", "Tag"])

    # Keep category useful: if there is an explicit business category field
    # separate from country, use it; otherwise use tags.
    explicit_category = extract_labeled_value(
        soup, ["Business Category", "Industry", "Category"]
    )
    category = explicit_category or tags

    founded_text = extract_labeled_value(soup, ["Founded", "Founded in"])
    founded_year = extract_year(founded_text)

    website = extract_linked_website(soup)

    data = {
        "company_name": company_name,
        "description": description,
        "website": website,
        "eu_startups_url": url,
        "country": country,
        "state": state,
        "city": city,
        "founded_year": founded_year,
        "category": category,
        "tags": tags,
    }

    # Additional sanity checks prevent obvious garbage records.
    if len(company_name) > 200:
        print(f"SKIP: suspicious company name -> {company_name[:100]}")
        return False

    startup_id = save_startup(data)

    if not startup_id:
        print(f"ERROR: could not save {url}")
        return False

    for person in extract_people(soup):
        save_person(
            startup_id=startup_id,
            name=person["name"],
            role=person["role"],
            email=person["email"],
            linkedin=person["linkedin"],
            source_url=url,
        )

    # Save public emails as contacts too.
    page_text = soup.get_text(" ", strip=True)
    email = extract_email(page_text)
    if email:
        save_contact(startup_id, "email", email, url)

    print(
        f"SAVED: {company_name} | "
        f"country={country or '-'} | "
        f"city={city or '-'} | "
        f"category={category or '-'} | "
        f"founded={founded_year or '-'}"
    )
    return True


def crawl():
    page = 1
    processed_urls = set()

    while True:
        url = DIRECTORY_URL if page == 1 else f"{BASE_URL}/directory/page/{page}/"

        print()
        print(f"========== PAGE {page} ==========")

        html = get_page(url)
        if not html:
            print("Could not load page. Stopping.")
            break

        startup_links = extract_startup_links(html)
        new_links = startup_links - processed_urls

        print(f"Found {len(startup_links)} valid startup listing links")
        print(f"New links: {len(new_links)}")

        if not new_links:
            print("No new startup links. Stopping.")
            break

        for startup_url in sorted(new_links):
            processed_urls.add(startup_url)
            try:
                scrape_startup(startup_url)
            except Exception as e:
                print(f"ERROR scraping {startup_url}: {e}")
            time.sleep(2)

        page += 1
        time.sleep(3)


if __name__ == "__main__":
    print("Starting EU-Startups scraper...")
    crawl()
    print("Scraping completed.")
