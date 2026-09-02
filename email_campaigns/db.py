"""
Email DB — SQLite schema for templates, campaigns, logs, and SMTP config.
Uses the same data/eu_startups.db file for simplicity.
"""

import sqlite3
import uuid
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = str(ROOT_DIR / "data" / "eu_startups.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_email_tables() -> None:
    """Create all email-related tables if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    # Email Templates
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_templates (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            tags            TEXT DEFAULT '',
            attachment_path TEXT,
            attachment_name TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Bulk Campaigns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_campaigns (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            template_id     TEXT NOT NULL,
            template_name   TEXT,
            subject         TEXT,
            attachment_path TEXT,
            attachment_name TEXT,
            status          TEXT DEFAULT 'pending',
            total           INTEGER DEFAULT 0,
            sent            INTEGER DEFAULT 0,
            failed_count    INTEGER DEFAULT 0,
            audience_filter TEXT DEFAULT '{}',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at     TIMESTAMP
        )
    """)

    # Safe column migrations for existing databases
    for table in ["email_templates", "email_campaigns"]:
        cols = [col[1] for col in cur.execute(f"PRAGMA table_info({table})").fetchall()]
        if "attachment_path" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN attachment_path TEXT")
        if "attachment_name" not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN attachment_name TEXT")

    # Per-recipient delivery logs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_campaign_logs (
            id              TEXT PRIMARY KEY,
            campaign_id     TEXT NOT NULL,
            recipient_name  TEXT,
            recipient_email TEXT,
            company_name    TEXT,
            status          TEXT DEFAULT 'pending',
            error_message   TEXT,
            sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES email_campaigns(id) ON DELETE CASCADE
        )
    """)

    # Saved Audiences / Recipient Lists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_audiences (
            id                TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            description       TEXT DEFAULT '',
            sources           TEXT DEFAULT '["sqlite"]',
            filters           TEXT DEFAULT '{}',
            manual_recipients TEXT DEFAULT '[]',
            contact_count     INTEGER DEFAULT 0,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 1-by-1 Individual Review & Send Queue Items
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_queue_items (
            id              TEXT PRIMARY KEY,
            template_id     TEXT,
            template_name   TEXT,
            audience_id     TEXT,
            recipient_name  TEXT,
            recipient_email TEXT NOT NULL,
            company_name    TEXT,
            role            TEXT,
            website         TEXT,
            city            TEXT,
            country         TEXT,
            category        TEXT,
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            raw_body        TEXT,
            status          TEXT DEFAULT 'draft',
            error_message   TEXT,
            sent_at         TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # SMTP Configuration (single-row table)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS smtp_config (
            id          INTEGER PRIMARY KEY DEFAULT 1,
            smtp_host   TEXT,
            smtp_port   INTEGER DEFAULT 587,
            smtp_user   TEXT,
            smtp_pass   TEXT,
            from_name   TEXT DEFAULT 'LeadPulse AI',
            use_ssl     INTEGER DEFAULT 0,
            use_tls     INTEGER DEFAULT 1,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ensure default SMTP row exists
    cur.execute("INSERT OR IGNORE INTO smtp_config (id) VALUES (1)")

    conn.commit()
    conn.close()


def get_smtp_config() -> dict:
    """Return SMTP config from DB, falling back to .env values."""
    import os
    conn = get_connection()
    row = conn.execute("SELECT * FROM smtp_config WHERE id = 1").fetchone()
    conn.close()

    row = dict(row) if row else {}

    # Fall back to environment variables if DB fields are empty
    return {
        "smtp_host":  row.get("smtp_host") or os.environ.get("SMTP_HOST", ""),
        "smtp_port":  int(row.get("smtp_port") or os.environ.get("SMTP_PORT", 587)),
        "smtp_user":  row.get("smtp_user") or os.environ.get("SMTP_USER", ""),
        "smtp_pass":  row.get("smtp_pass") or os.environ.get("SMTP_PASS", ""),
        "from_name":  row.get("from_name") or os.environ.get("SMTP_FROM_NAME", "LeadPulse AI"),
        "use_ssl":    bool(row.get("use_ssl") or os.environ.get("SMTP_USE_SSL", "").lower() in ("true", "1")),
        "use_tls":    bool(row.get("use_tls", 1) or os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1")),
    }


def save_smtp_config(host: str, port: int, user: str, password: str,
                     from_name: str, use_ssl: bool, use_tls: bool) -> None:
    conn = get_connection()
    conn.execute("""
        UPDATE smtp_config SET
            smtp_host = ?, smtp_port = ?, smtp_user = ?, smtp_pass = ?,
            from_name = ?, use_ssl = ?, use_tls = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (host, port, user, password, from_name, int(use_ssl), int(use_tls)))
    conn.commit()
    conn.close()


# Run init on import so tables exist immediately
init_email_tables()
