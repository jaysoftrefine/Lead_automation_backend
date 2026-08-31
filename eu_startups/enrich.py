import os
import json
import re
import sqlite3
import time
from typing import Optional

from tavily import TavilyClient
from google import genai


# ============================================================
# CONFIG
# ============================================================

from pathlib import Path

# Locate database path
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DB_PATH = ROOT_DIR / "data" / "eu_startups.db"
LOCAL_DB_PATH = Path(__file__).resolve().parent / "eu_startups.db"

if DATA_DB_PATH.exists():
    DB_PATH = str(DATA_DB_PATH)
elif LOCAL_DB_PATH.exists():
    DB_PATH = str(LOCAL_DB_PATH)
else:
    DB_PATH = str(DATA_DB_PATH)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GEMINI_API_KEY = (
    os.environ.get("GOOGLE_API_KEY")
    or os.environ.get("GEMINI_API_KEY")
)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

GEMINI_MODEL = "gemini-3.5-flash"

# TEST MODE:
# Only process the first 2 incomplete startups
LIMIT = 2

MAX_SEARCH_RESULTS = 5
SLEEP_BETWEEN_COMPANIES = 1


# ============================================================
# API CLIENTS
# ============================================================

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY environment variable is missing"
    )

if not TAVILY_API_KEY:
    raise ValueError(
        "TAVILY_API_KEY environment variable is missing"
    )

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)

tavily = TavilyClient(
    api_key=TAVILY_API_KEY
)


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    return sqlite3.connect(DB_PATH)


def get_columns(conn):
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(startups)")

    return [
        row[1]
        for row in cursor.fetchall()
    ]


def table_exists(conn, table_name):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def find_column(columns, possible_names):
    """
    Finds a database column using multiple possible names.
    """

    normalized = {
        column.lower()
        .replace("_", "")
        .replace(" ", ""): column
        for column in columns
    }

    for name in possible_names:

        key = (
            name.lower()
            .replace("_", "")
            .replace(" ", "")
        )

        if key in normalized:
            return normalized[key]

    return None


# ============================================================
# HELPERS
# ============================================================

def is_empty(value):

    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    return False


def clean_json_response(text):

    text = text.strip()

    # Remove ```json ... ```
    if text.startswith("```"):

        text = re.sub(
            r"^```(?:json)?",
            "",
            text,
            flags=re.IGNORECASE
        )

        text = re.sub(
            r"```$",
            "",
            text
        )

    return text.strip()


# ============================================================
# TAVILY SEARCH
# ============================================================

def search_company(
    company_name: str,
    website: Optional[str] = None
):

    queries = [
        f'"{company_name}" founder',
        f'"{company_name}" CEO founder',
        f'"{company_name}" founder email',
        f'"{company_name}" contact email',
    ]

    # Only add domain search if we have a real website
    if website:

        clean_website = (
            website
            .replace("https://", "")
            .replace("http://", "")
            .split("/")[0]
        )

        if clean_website:

            queries.append(
                f'"{company_name}" site:{clean_website} founder'
            )

    all_results = []

    for query in queries:

        print(f"    Tavily search: {query}")

        try:

            response = tavily.search(
                query=query,
                search_depth="advanced",
                max_results=MAX_SEARCH_RESULTS,
                include_answer=True,
                include_raw_content=False,
            )

            results = response.get(
                "results",
                []
            )

            for result in results:

                all_results.append({
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "content": result.get("content"),
                })

        except Exception as e:

            print(
                f"    Tavily error: {e}"
            )

    # Remove duplicate URLs
    unique_results = {}

    for result in all_results:

        url = result.get("url")

        if url:
            unique_results[url] = result

    return list(
        unique_results.values()
    )


# ============================================================
# GEMINI
# ============================================================

def extract_company_data(
    company_name: str,
    website: Optional[str],
    search_results
):

    if not search_results:
        return None

    search_text = ""

    for index, result in enumerate(
        search_results,
        start=1
    ):

        search_text += f"""
SOURCE {index}

Title:
{result.get("title")}

URL:
{result.get("url")}

Content:
{result.get("content")}

--------------------------------------------------
"""

    prompt = f"""
You are a startup/company research assistant.

We recently scraped this startup:

Company:
{company_name}

Website:
{website or "Unknown"}

We used Tavily to search the web. The search results are provided below.

Your job is to find reliable information about:

1. Founder
2. Co-founder
3. CEO
4. Other clearly relevant company leadership
5. Publicly available business/company email
6. Publicly available founder business email

IMPORTANT RULES:

- ONLY use information supported by the provided sources.
- NEVER invent a person.
- NEVER guess an email address.
- Do NOT construct an email from a person's name.
- Only return an email if it is explicitly present in a source.
- Prefer official company websites.
- LinkedIn, Crunchbase, accelerator pages, interviews,
  reputable startup databases and news sites can also be used.
- If founder information cannot be confirmed, return an empty people array.
- If no email is explicitly available, return null.
- A generic company email such as info@company.com is acceptable
  if it is explicitly found.
- A founder's publicly listed business email is acceptable
  if explicitly found.
- Include source URLs that support the result.
- Keep confidence conservative.

Return ONLY valid JSON.

Use exactly this structure:

{{
    "company": "{company_name}",
    "people": [
        {{
            "name": "Full Name",
            "role": "Founder / Co-Founder / CEO",
            "evidence": "Short explanation"
        }}
    ],
    "email": "email@example.com",
    "email_type": "company / founder / other",
    "source_urls": [
        "https://example.com"
    ],
    "confidence": "high / medium / low"
}}

If nothing reliable is found, return:

{{
    "company": "{company_name}",
    "people": [],
    "email": null,
    "email_type": null,
    "source_urls": [],
    "confidence": "low"
}}

TAVILY SEARCH RESULTS:

{search_text}
"""

    try:

        response = gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={
                "temperature": 0,
            },
        )

        text = clean_json_response(
            response.text
        )

        result = json.loads(text)

        return result

    except json.JSONDecodeError as e:

        print(
            f"    Gemini JSON error: {e}"
        )

        print(
            "    Gemini response:"
        )

        print(
            response.text[:2000]
        )

        return None

    except Exception as e:

        print(
            f"    Gemini error: {e}"
        )

        return None


# ============================================================
# DATABASE UPDATE
# ============================================================

def update_company(
    conn,
    company_id,
    id_column,
    people_column,
    email_column,
    enrichment_column,
    result,
    existing_people,
    existing_email,
    normalized_schema=False,
):

    cursor = conn.cursor()

    updates = []
    values = []

    # --------------------------------------------------------
    # PEOPLE
    # --------------------------------------------------------

    # Only update people if currently empty
    if normalized_schema and is_empty(existing_people):
        people = result.get("people", [])
        for person in people:
            if not person.get("name"):
                continue
            cursor.execute(
                """
                INSERT INTO people (startup_id, name, role, source_url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(startup_id, name, role)
                DO UPDATE SET source_url = excluded.source_url
                """,
                (
                    company_id,
                    person.get("name"),
                    person.get("role"),
                    ", ".join(result.get("source_urls", [])),
                ),
            )

    elif (
        people_column
        and is_empty(existing_people)
    ):

        people = result.get(
            "people",
            []
        )

        if people:

            people_json = json.dumps(
                people,
                ensure_ascii=False
            )

            updates.append(
                f'"{people_column}" = ?'
            )

            values.append(
                people_json
            )

    # --------------------------------------------------------
    # EMAIL
    # --------------------------------------------------------

    # Only update email if currently empty
    if normalized_schema and is_empty(existing_email):
        email = result.get("email")
        if email:
            cursor.execute(
                """
                INSERT INTO contacts (startup_id, contact_type, value, source_url)
                VALUES (?, 'email', ?, ?)
                """,
                (
                    company_id,
                    email,
                    ", ".join(result.get("source_urls", [])),
                ),
            )

    elif (
        email_column
        and is_empty(existing_email)
    ):

        email = result.get(
            "email"
        )

        if email:

            updates.append(
                f'"{email_column}" = ?'
            )

            values.append(email)

    # --------------------------------------------------------
    # RAW GEMINI RESULT
    # --------------------------------------------------------

    updates.append(
        f'"{enrichment_column}" = ?'
    )

    values.append(
        json.dumps(
            result,
            ensure_ascii=False
        )
    )

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    values.append(company_id)

    sql = f"""
        UPDATE startups
        SET {", ".join(updates)}
        WHERE "{id_column}" = ?
    """

    cursor.execute(
        sql,
        values
    )

    conn.commit()


# ============================================================
# MAIN
# ============================================================

def main():

    print("\n")
    print("=" * 70)
    print("STARTUP ENRICHMENT")
    print("=" * 70)

    conn = get_connection()

    # --------------------------------------------------------
    # Get DB columns
    # --------------------------------------------------------

    columns = get_columns(conn)

    print("\nDatabase columns:")
    print(columns)

    # --------------------------------------------------------
    # Detect columns
    # --------------------------------------------------------

    id_column = find_column(
        columns,
        [
            "id",
            "startup_id",
            "company_id",
        ]
    )

    company_column = find_column(
        columns,
        [
            "company",
            "company_name",
            "startup",
            "startup_name",
            "name",
        ]
    )

    people_column = find_column(
        columns,
        [
            "people",
            "person",
            "founders",
            "founder",
            "team",
        ]
    )

    email_column = find_column(
        columns,
        [
            "email",
            "emails",
            "contact_email",
        ]
    )

    website_column = find_column(
        columns,
        [
            "website",
            "url",
            "company_website",
            "website_url",
        ]
    )

    normalized_schema = (
        not people_column
        and not email_column
        and table_exists(conn, "people")
        and table_exists(conn, "contacts")
    )

    # --------------------------------------------------------
    # Print detected columns
    # --------------------------------------------------------

    print("\nDetected columns:")
    print(f"  ID      : {id_column}")
    print(f"  Company : {company_column}")
    print(f"  People  : {people_column}")
    print(f"  Email   : {email_column}")
    print(f"  Website : {website_column}")

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    if not id_column:

        raise ValueError(
            "Could not find ID column"
        )

    if not company_column:

        raise ValueError(
            "Could not find company/startup name column"
        )

    if not normalized_schema and not people_column and not email_column:

        raise ValueError(
            "Could not find people or email column"
        )

    # --------------------------------------------------------
    # Add enrichment_result column
    # --------------------------------------------------------

    enrichment_column = find_column(
        columns,
        [
            "enrichment_result",
            "llm_result",
            "research_result",
        ]
    )

    if not enrichment_column:

        print(
            "\nAdding enrichment_result column..."
        )

        conn.execute(
            """
            ALTER TABLE startups
            ADD COLUMN enrichment_result TEXT
            """
        )

        conn.commit()

        enrichment_column = (
            "enrichment_result"
        )

    # --------------------------------------------------------
    # Get all startups
    # --------------------------------------------------------

    cursor = conn.cursor()

    people_select = (
        f'"{people_column}"'
        if people_column
        else """
            (
                SELECT GROUP_CONCAT(name, ', ')
                FROM people
                WHERE people.startup_id = startups.id
            )
        """
    )

    email_select = (
        f'"{email_column}"'
        if email_column
        else """
            COALESCE(
                (
                    SELECT GROUP_CONCAT(value, ', ')
                    FROM contacts
                    WHERE contacts.startup_id = startups.id
                      AND LOWER(contacts.contact_type) LIKE '%email%'
                ),
                (
                    SELECT GROUP_CONCAT(email, ', ')
                    FROM people
                    WHERE people.startup_id = startups.id
                      AND email IS NOT NULL
                )
            )
        """
    )

    website_select = (
        f'"{website_column}"'
        if website_column
        else "NULL"
    )

    query = f"""
        SELECT
            "{id_column}",
            "{company_column}",
            {people_select},
            {email_select},
            {website_select}
        FROM startups
    """

    cursor.execute(query)

    all_rows = cursor.fetchall()

    # --------------------------------------------------------
    # Find incomplete startups
    # --------------------------------------------------------

    incomplete_rows = []

    for row in all_rows:

        people_value = row[2]
        email_value = row[3]

        people_missing = is_empty(
            people_value
        )

        email_missing = is_empty(
            email_value
        )

        if (
            people_missing
            or email_missing
        ):

            incomplete_rows.append(row)

        # TEST LIMIT
        if len(incomplete_rows) >= LIMIT:
            break

    # --------------------------------------------------------
    # Start processing
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print(
        f"Found {len(incomplete_rows)} incomplete startups"
    )
    print(
        f"TEST LIMIT = {LIMIT}"
    )
    print("=" * 70)

    enriched = 0

    for index, row in enumerate(
        incomplete_rows,
        start=1
    ):

        company_id = row[0]
        company_name = row[1]
        people_value = row[2]
        email_value = row[3]
        website_value = row[4]

        print("\n")
        print("=" * 70)
        print(
            f"[{index}/{len(incomplete_rows)}] "
            f"{company_name}"
        )
        print("=" * 70)

        print(
            f"Website: {website_value}"
        )

        print(
            f"People missing: "
            f"{is_empty(people_value)}"
        )

        print(
            f"Email missing: "
            f"{is_empty(email_value)}"
        )

        # ----------------------------------------------------
        # Tavily
        # ----------------------------------------------------

        print("\nSearching Tavily...")

        search_results = search_company(
            company_name,
            website_value
        )

        print(
            f"Found {len(search_results)} "
            f"unique search sources"
        )

        if not search_results:

            print(
                "No Tavily results. Skipping."
            )

            continue

        # ----------------------------------------------------
        # Gemini
        # ----------------------------------------------------

        print("\nSending results to Gemini...")

        result = extract_company_data(
            company_name,
            website_value,
            search_results
        )

        if not result:

            print(
                "Gemini did not return valid data."
            )

            continue

        # ----------------------------------------------------
        # Show result
        # ----------------------------------------------------

        print("\nGemini result:")

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False
            )
        )

        # ----------------------------------------------------
        # Update database
        # ----------------------------------------------------

        update_company(
            conn=conn,
            company_id=company_id,
            id_column=id_column,
            people_column=people_column,
            email_column=email_column,
            enrichment_column=enrichment_column,
            result=result,
            existing_people=people_value,
            existing_email=email_value,
            normalized_schema=normalized_schema,
        )

        print(
            "\nDatabase updated successfully."
        )

        enriched += 1

        time.sleep(
            SLEEP_BETWEEN_COMPANIES
        )

    # --------------------------------------------------------
    # Finish
    # --------------------------------------------------------

    conn.close()

    print("\n")
    print("=" * 70)
    print("FINISHED")
    print("=" * 70)

    print(
        f"Processed: {len(incomplete_rows)}"
    )

    print(
        f"Updated: {enriched}"
    )

    print(
        f"Remaining limit: {LIMIT}"
    )

    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()