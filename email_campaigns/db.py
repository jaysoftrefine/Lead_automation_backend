"""
Email DB — SQLite schema for templates, campaigns, logs, and SMTP config.
Uses the same data/eu_startups.db file for simplicity.
"""

import sqlite3
import uuid
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(DATA_DIR / "email_campaigns.db")


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
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            description         TEXT DEFAULT '',
            sources             TEXT DEFAULT '["sqlite"]',
            filters             TEXT DEFAULT '{}',
            manual_recipients   TEXT DEFAULT '[]',
            selected_recipients TEXT DEFAULT '[]',
            contact_count       INTEGER DEFAULT 0,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cur.execute("ALTER TABLE email_audiences ADD COLUMN selected_recipients TEXT DEFAULT '[]'")
    except Exception:
        pass

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

    # Multi-SMTP Configuration Accounts
    cur.execute("""
        CREATE TABLE IF NOT EXISTS smtp_accounts (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            smtp_host   TEXT NOT NULL,
            smtp_port   INTEGER DEFAULT 587,
            smtp_user   TEXT NOT NULL,
            smtp_pass   TEXT NOT NULL,
            from_name   TEXT DEFAULT 'HirePilot AI',
            use_ssl     INTEGER DEFAULT 0,
            use_tls     INTEGER DEFAULT 1,
            is_default  INTEGER DEFAULT 0,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Safe column migrations for campaigns, queue items, and logs
    c_cols = [col[1] for col in cur.execute("PRAGMA table_info(email_campaigns)").fetchall()]
    if "smtp_account_id" not in c_cols:
        cur.execute("ALTER TABLE email_campaigns ADD COLUMN smtp_account_id TEXT")

    q_cols = [col[1] for col in cur.execute("PRAGMA table_info(email_queue_items)").fetchall()]
    if "smtp_account_id" not in q_cols:
        cur.execute("ALTER TABLE email_queue_items ADD COLUMN smtp_account_id TEXT")

    l_cols = [col[1] for col in cur.execute("PRAGMA table_info(email_campaign_logs)").fetchall()]
    if "sender_email" not in l_cols:
        cur.execute("ALTER TABLE email_campaign_logs ADD COLUMN sender_email TEXT")

    # Legacy single-row SMTP Configuration (retained for backward compatibility)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS smtp_config (
            id          INTEGER PRIMARY KEY DEFAULT 1,
            smtp_host   TEXT,
            smtp_port   INTEGER DEFAULT 587,
            smtp_user   TEXT,
            smtp_pass   TEXT,
            from_name   TEXT DEFAULT 'HirePilot AI',
            use_ssl     INTEGER DEFAULT 0,
            use_tls     INTEGER DEFAULT 1,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("INSERT OR IGNORE INTO smtp_config (id) VALUES (1)")

    # Seed smtp_accounts from legacy smtp_config if smtp_accounts is currently empty
    accounts_count = cur.execute("SELECT COUNT(*) FROM smtp_accounts").fetchone()[0]
    if accounts_count == 0:
        legacy_row = cur.execute("SELECT * FROM smtp_config WHERE id = 1").fetchone()
        if legacy_row and legacy_row["smtp_user"] and legacy_row["smtp_host"]:
            cur.execute("""
                INSERT INTO smtp_accounts (
                    id, name, smtp_host, smtp_port, smtp_user, smtp_pass, from_name, use_ssl, use_tls, is_default
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                str(uuid.uuid4()),
                legacy_row["from_name"] or "Default Account",
                legacy_row["smtp_host"],
                int(legacy_row["smtp_port"] or 587),
                legacy_row["smtp_user"],
                legacy_row["smtp_pass"] or "",
                legacy_row["from_name"] or "HirePilot AI",
                int(legacy_row["use_ssl"] or 0),
                int(legacy_row["use_tls"] if legacy_row["use_tls"] is not None else 1),
            ))

    conn.commit()
    conn.close()


def list_smtp_accounts() -> list:
    """Return all configured SMTP accounts."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM smtp_accounts ORDER BY is_default DESC, created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_smtp_account(account_id: str) -> Optional[dict]:
    """Retrieve an SMTP account by ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM smtp_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_smtp_account(data: dict) -> dict:
    """Create a new SMTP account. If marked as default or first account, sets is_default=1."""
    conn = get_connection()
    cur = conn.cursor()

    account_id = str(uuid.uuid4())
    count = cur.execute("SELECT COUNT(*) FROM smtp_accounts").fetchone()[0]
    is_default = 1 if (data.get("is_default") or count == 0) else 0

    if is_default:
        cur.execute("UPDATE smtp_accounts SET is_default = 0")

    cur.execute("""
        INSERT INTO smtp_accounts (
            id, name, smtp_host, smtp_port, smtp_user, smtp_pass,
            from_name, use_ssl, use_tls, is_default, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        account_id,
        data.get("name") or data.get("smtp_user") or "SMTP Account",
        data.get("smtp_host", "").strip(),
        int(data.get("smtp_port") or 587),
        data.get("smtp_user", "").strip(),
        data.get("smtp_pass", "").strip(),
        data.get("from_name", "HirePilot AI").strip(),
        1 if data.get("use_ssl") else 0,
        1 if data.get("use_tls", True) else 0,
        is_default,
    ))

    # Also keep legacy single-row config in sync if default
    if is_default:
        cur.execute("""
            UPDATE smtp_config SET
                smtp_host = ?, smtp_port = ?, smtp_user = ?, smtp_pass = ?,
                from_name = ?, use_ssl = ?, use_tls = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            data.get("smtp_host", "").strip(),
            int(data.get("smtp_port") or 587),
            data.get("smtp_user", "").strip(),
            data.get("smtp_pass", "").strip(),
            data.get("from_name", "HirePilot AI").strip(),
            1 if data.get("use_ssl") else 0,
            1 if data.get("use_tls", True) else 0,
        ))

    conn.commit()
    row = cur.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def update_smtp_account(account_id: str, data: dict) -> Optional[dict]:
    """Update an existing SMTP account. Retains existing password if omitted or masked."""
    conn = get_connection()
    cur = conn.cursor()

    row = cur.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        conn.close()
        return None

    existing = dict(row)
    password = data.get("smtp_pass")
    if not password or password == "••••••••":
        password = existing["smtp_pass"]

    is_default = data.get("is_default")
    if is_default is not None:
        is_default = 1 if is_default else 0
        if is_default:
            cur.execute("UPDATE smtp_accounts SET is_default = 0")
    else:
        is_default = existing["is_default"]

    cur.execute("""
        UPDATE smtp_accounts SET
            name = ?,
            smtp_host = ?,
            smtp_port = ?,
            smtp_user = ?,
            smtp_pass = ?,
            from_name = ?,
            use_ssl = ?,
            use_tls = ?,
            is_default = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        data.get("name") or existing["name"],
        data.get("smtp_host", existing["smtp_host"]).strip(),
        int(data.get("smtp_port") or existing["smtp_port"]),
        data.get("smtp_user", existing["smtp_user"]).strip(),
        password,
        data.get("from_name", existing["from_name"]).strip(),
        1 if data.get("use_ssl", bool(existing["use_ssl"])) else 0,
        1 if data.get("use_tls", bool(existing["use_tls"])) else 0,
        is_default,
        account_id,
    ))

    # Keep legacy smtp_config in sync if default
    if is_default:
        cur.execute("""
            UPDATE smtp_config SET
                smtp_host = ?, smtp_port = ?, smtp_user = ?, smtp_pass = ?,
                from_name = ?, use_ssl = ?, use_tls = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (
            data.get("smtp_host", existing["smtp_host"]).strip(),
            int(data.get("smtp_port") or existing["smtp_port"]),
            data.get("smtp_user", existing["smtp_user"]).strip(),
            password,
            data.get("from_name", existing["from_name"]).strip(),
            1 if data.get("use_ssl", bool(existing["use_ssl"])) else 0,
            1 if data.get("use_tls", bool(existing["use_tls"])) else 0,
        ))

    conn.commit()
    updated = cur.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    return dict(updated) if updated else None


def delete_smtp_account(account_id: str) -> bool:
    """Delete an SMTP account. If deleted account was default, elect another one as default."""
    conn = get_connection()
    cur = conn.cursor()

    row = cur.execute("SELECT is_default FROM smtp_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        conn.close()
        return False

    was_default = bool(row[0])
    cur.execute("DELETE FROM smtp_accounts WHERE id = ?", (account_id,))

    if was_default:
        first = cur.execute("SELECT id FROM smtp_accounts ORDER BY created_at ASC LIMIT 1").fetchone()
        if first:
            cur.execute("UPDATE smtp_accounts SET is_default = 1 WHERE id = ?", (first[0],))

    conn.commit()
    conn.close()
    return True


def set_default_smtp_account(account_id: str) -> bool:
    """Set the specified account as the default account."""
    conn = get_connection()
    cur = conn.cursor()

    row = cur.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        conn.close()
        return False

    cur.execute("UPDATE smtp_accounts SET is_default = 0")
    cur.execute("UPDATE smtp_accounts SET is_default = 1 WHERE id = ?", (account_id,))

    # Update legacy table
    r = dict(row)
    cur.execute("""
        UPDATE smtp_config SET
            smtp_host = ?, smtp_port = ?, smtp_user = ?, smtp_pass = ?,
            from_name = ?, use_ssl = ?, use_tls = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (
        r["smtp_host"], r["smtp_port"], r["smtp_user"], r["smtp_pass"],
        r["from_name"], r["use_ssl"], r["use_tls"],
    ))

    conn.commit()
    conn.close()
    return True


def get_smtp_config(account_id: Optional[str] = None) -> dict:
    """
    Return SMTP config from DB for a given account_id or the default account,
    falling back to legacy smtp_config and .env values.
    """
    import os
    conn = get_connection()

    row = None
    if account_id:
        row = conn.execute("SELECT * FROM smtp_accounts WHERE id = ?", (account_id,)).fetchone()

    # If no specific account found or requested, find default account
    if not row:
        row = conn.execute("SELECT * FROM smtp_accounts WHERE is_default = 1 LIMIT 1").fetchone()

    # If still no default, pick any account
    if not row:
        row = conn.execute("SELECT * FROM smtp_accounts ORDER BY created_at ASC LIMIT 1").fetchone()

    # Fall back to legacy smtp_config
    if not row:
        row = conn.execute("SELECT * FROM smtp_config WHERE id = 1").fetchone()

    conn.close()
    row = dict(row) if row else {}

    # Fall back to environment variables if fields are empty
    return {
        "id":         row.get("id"),
        "name":       row.get("name") or "Primary SMTP",
        "smtp_host":  row.get("smtp_host") or os.environ.get("SMTP_HOST", ""),
        "smtp_port":  int(row.get("smtp_port") or os.environ.get("SMTP_PORT", 587)),
        "smtp_user":  row.get("smtp_user") or os.environ.get("SMTP_USER", ""),
        "smtp_pass":  row.get("smtp_pass") or os.environ.get("SMTP_PASS", ""),
        "from_name":  row.get("from_name") or os.environ.get("SMTP_FROM_NAME", "HirePilot AI"),
        "use_ssl":    bool(row.get("use_ssl") or os.environ.get("SMTP_USE_SSL", "").lower() in ("true", "1")),
        "use_tls":    bool(row.get("use_tls", 1) or os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1")),
        "is_default": bool(row.get("is_default", 1)),
    }


def save_smtp_config(host: str, port: int, user: str, password: str,
                     from_name: str, use_ssl: bool, use_tls: bool) -> None:
    """Legacy helper: saves to smtp_config and creates/updates default in smtp_accounts."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE smtp_config SET
            smtp_host = ?, smtp_port = ?, smtp_user = ?, smtp_pass = ?,
            from_name = ?, use_ssl = ?, use_tls = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (host, port, user, password, from_name, int(use_ssl), int(use_tls)))

    # Also update or insert default account in smtp_accounts
    default_acc = cur.execute("SELECT id FROM smtp_accounts WHERE is_default = 1 LIMIT 1").fetchone()
    if default_acc:
        cur.execute("""
            UPDATE smtp_accounts SET
                name = ?, smtp_host = ?, smtp_port = ?, smtp_user = ?, smtp_pass = ?,
                from_name = ?, use_ssl = ?, use_tls = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (from_name or "Default Account", host, port, user, password, from_name, int(use_ssl), int(use_tls), default_acc[0]))
    else:
        acc_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO smtp_accounts (
                id, name, smtp_host, smtp_port, smtp_user, smtp_pass, from_name, use_ssl, use_tls, is_default
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (acc_id, from_name or "Default Account", host, port, user, password, from_name, int(use_ssl), int(use_tls)))

    conn.commit()
    conn.close()


# Run init on import so tables exist immediately
init_email_tables()
