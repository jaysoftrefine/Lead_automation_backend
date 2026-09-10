"""SQLite Database Schema & Table Definitions for HirePilot."""

import sqlite3


def ensure_database_schema(conn: sqlite3.Connection) -> None:
    """Create enriched_leads, raw_jobs, and job_leads tables and indexes if they do not exist."""
    cur = conn.cursor()

    # 1. Enriched Leads Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS enriched_leads (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            job_url                 TEXT UNIQUE NOT NULL,
            title                   TEXT NOT NULL,
            company                 TEXT NOT NULL,
            site                    TEXT,
            location                TEXT,
            job_type                TEXT,
            job_description         TEXT,
            is_valid_lead           INTEGER DEFAULT 1,
            relevance_score         INTEGER DEFAULT 50,
            company_domain          TEXT,
            company_summary         TEXT,
            company_size            TEXT,
            contacts                TEXT DEFAULT '[]',
            key_technologies        TEXT DEFAULT '[]',
            hiring_urgency          TEXT,
            lead_summary            TEXT,
            agent_thinking_process  TEXT,
            search_queries_used     TEXT DEFAULT '[]',
            status                  TEXT DEFAULT 'new',
            lead_type               TEXT DEFAULT 'others',
            date_posted             TEXT,
            scraped_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ensure date_posted, scraped_at, and lead_type columns exist on existing enriched_leads table
    for col, col_type in (("date_posted", "TEXT"), ("scraped_at", "TIMESTAMP"), ("lead_type", "TEXT DEFAULT 'others'")):
        try:
            cur.execute(f"ALTER TABLE enriched_leads ADD COLUMN {col} {col_type}")
        except Exception:
            pass

    # Per-lead outreach automation (auto/manual mail fire + open/close + drip stage)
    for col, col_type in (
        ("outreach_mode", "TEXT DEFAULT 'auto'"),
        ("outreach_state", "TEXT DEFAULT 'open'"),
        ("outreach_stage", "INTEGER DEFAULT 1"),
        ("next_send_at", "TEXT"),
        ("last_sent_at", "TEXT"),
    ):
        try:
            cur.execute(f"ALTER TABLE enriched_leads ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    try:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_enriched_leads_outreach_due "
            "ON enriched_leads(outreach_mode, outreach_state, next_send_at)"
        )
    except Exception:
        pass

    # Defaults for never-sent open tickets (delay/mode/state from env settings)
    try:
        from config.settings import settings
        delay = max(0, int(settings.outreach_first_send_delay_days))
        mode = (settings.outreach_default_mode or "auto").strip().lower()
        state = (settings.outreach_default_state or "open").strip().lower()
        if mode not in ("auto", "manual"):
            mode = "auto"
        if state not in ("open", "closed"):
            state = "open"
        cur.execute(
            f"""
            UPDATE enriched_leads
            SET
                outreach_mode = ?,
                outreach_state = COALESCE(NULLIF(TRIM(outreach_state), ''), ?),
                outreach_stage = COALESCE(outreach_stage, 1),
                next_send_at = CASE
                    WHEN next_send_at IS NULL OR TRIM(next_send_at) = ''
                    THEN date(substr(COALESCE(scraped_at, created_at, CURRENT_TIMESTAMP), 1, 10), '+{delay} day')
                    ELSE next_send_at
                END
            WHERE COALESCE(LOWER(outreach_state), 'open') = 'open'
              AND (last_sent_at IS NULL OR TRIM(last_sent_at) = '')
              AND (
                outreach_mode IS NULL OR TRIM(outreach_mode) = ''
                OR next_send_at IS NULL OR TRIM(next_send_at) = ''
              )
            """,
            (mode, state),
        )
    except Exception:
        pass

    # 2. Raw Scraped Jobs Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS raw_jobs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id                  TEXT,
            job_url                 TEXT UNIQUE NOT NULL,
            title                   TEXT NOT NULL,
            company                 TEXT NOT NULL,
            location                TEXT,
            site                    TEXT,
            description             TEXT,
            job_type                TEXT,
            salary_min              REAL,
            salary_max              REAL,
            salary_currency         TEXT,
            date_posted             TEXT,
            scraped_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            raw_metadata            TEXT DEFAULT '{}'
        )
    """)

    # 3. Job Leads Table (Historical / Legacy Scraped LinkedIn Leads)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS job_leads (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            job_url                 TEXT UNIQUE NOT NULL,
            title                   TEXT,
            company                 TEXT,
            location                TEXT,
            site                    TEXT,
            company_website         TEXT,
            company_url             TEXT,
            company_phone           TEXT,
            company_email           TEXT,
            company_industry        TEXT,
            company_num_employees   TEXT,
            emails                  TEXT,
            phones                  TEXT,
            recruiter_name          TEXT,
            ai_notes                TEXT,
            is_remote               INTEGER DEFAULT 0,
            enriched_by_ai          INTEGER DEFAULT 0,
            date_posted             TEXT,
            description             TEXT,
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 4. Scheduled Scraping Jobs Table (CSV/Excel uploaded and scheduled tasks)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_scraping_jobs (
            id                      TEXT PRIMARY KEY,
            job_title               TEXT NOT NULL,
            target_location         TEXT,
            company_size            TEXT,
            scraping_limit          INTEGER DEFAULT 10,
            scheduled_date          TEXT NOT NULL,
            status                  TEXT DEFAULT 'pending',
            result_count            INTEGER DEFAULT 0,
            last_run_at             TIMESTAMP,
            error_message           TEXT,
            created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Ensure scheduled_job_id column exists on enriched_leads and raw_jobs
    for tbl in ("enriched_leads", "raw_jobs"):
        try:
            cur.execute(f"ALTER TABLE {tbl} ADD COLUMN scheduled_job_id TEXT")
        except Exception:
            pass

    # Indexes for high performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_job_url ON enriched_leads(job_url)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_company ON enriched_leads(company)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_site ON enriched_leads(site)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_score ON enriched_leads(relevance_score)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_status ON enriched_leads(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_created ON enriched_leads(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_scraped ON enriched_leads(scraped_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_enriched_leads_sched_id ON enriched_leads(scheduled_job_id)")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_jobs_job_url ON raw_jobs(job_url)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_jobs_scraped ON raw_jobs(scraped_at)")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_job_leads_job_url ON job_leads(job_url)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_job_leads_company ON job_leads(company)")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_date ON scheduled_scraping_jobs(scheduled_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status ON scheduled_scraping_jobs(status)")

    # Backfill date_posted and scraped_at for any enriched leads missing them
    try:
        cur.execute("""
            UPDATE enriched_leads
            SET 
                date_posted = COALESCE(
                    date_posted, 
                    (SELECT rj.date_posted FROM raw_jobs rj WHERE rj.job_url = enriched_leads.job_url),
                    (SELECT jl.date_posted FROM job_leads jl WHERE jl.job_url = enriched_leads.job_url)
                ),
                scraped_at = COALESCE(
                    scraped_at,
                    (SELECT rj.scraped_at FROM raw_jobs rj WHERE rj.job_url = enriched_leads.job_url),
                    (SELECT jl.created_at FROM job_leads jl WHERE jl.job_url = enriched_leads.job_url),
                    created_at
                )
            WHERE date_posted IS NULL OR scraped_at IS NULL
        """)
    except Exception:
        pass

    # Baseline seed: if scheduled_scraping_jobs is empty and we have existing enriched_leads, link them to JOB-001
    try:
        cur.execute("SELECT COUNT(*) FROM scheduled_scraping_jobs")
        count_jobs = cur.fetchone()[0]
        if count_jobs == 0:
            cur.execute("SELECT COUNT(*) FROM enriched_leads")
            leads_cnt = cur.fetchone()[0]
            if leads_cnt > 0:
                cur.execute("""
                    INSERT INTO scheduled_scraping_jobs (
                        id, job_title, target_location, company_size, scraping_limit,
                        scheduled_date, status, result_count, last_run_at, created_at, updated_at
                    ) VALUES (
                        'JOB-001', 'Software Engineer Freelancer', 'Worldwide (Remote)', 'Small / Startup (1-50)',
                        ?, date('now'), 'completed', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """, (leads_cnt, leads_cnt))
                cur.execute("UPDATE enriched_leads SET scheduled_job_id = 'JOB-001' WHERE scheduled_job_id IS NULL")
    except Exception:
        pass

    conn.commit()
