import sqlite3
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

DB_NAME = DB_PATH


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_database():
    conn = get_connection()
    cursor = conn.cursor()

    # -------------------------
    # STARTUPS
    # -------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS startups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            company_name TEXT,
            description TEXT,

            website TEXT,
            eu_startups_url TEXT UNIQUE,

            country TEXT,
            state TEXT,
            city TEXT,

            founded_year INTEGER,

            category TEXT,
            tags TEXT,

            company_linkedin TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -------------------------
    # PEOPLE
    # -------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            startup_id INTEGER NOT NULL,

            name TEXT,
            role TEXT,

            email TEXT,
            linkedin TEXT,

            source_url TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (startup_id)
                REFERENCES startups(id)
                ON DELETE CASCADE,

            UNIQUE(startup_id, name, role)
        )
    """)

    # -------------------------
    # CONTACTS
    # -------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            startup_id INTEGER NOT NULL,

            contact_type TEXT,
            value TEXT,

            source_url TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (startup_id)
                REFERENCES startups(id)
                ON DELETE CASCADE
        )
    """)

    # -------------------------
    # CRAWL STATUS
    # -------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crawl_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            startup_id INTEGER UNIQUE,

            status TEXT DEFAULT 'pending',

            attempts INTEGER DEFAULT 0,

            last_crawled TIMESTAMP,

            error TEXT,

            FOREIGN KEY (startup_id)
                REFERENCES startups(id)
                ON DELETE CASCADE
        )
    """)

    # -------------------------
    # INDEXES
    # -------------------------

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_startups_country
        ON startups(country)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_startups_state
        ON startups(state)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_startups_city
        ON startups(city)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_startups_category
        ON startups(category)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_people_role
        ON people(role)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_people_email
        ON people(email)
    """)

    conn.commit()
    conn.close()

    print(f"Database created: {DB_NAME}")


if __name__ == "__main__":
    create_database()