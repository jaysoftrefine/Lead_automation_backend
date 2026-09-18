-- ==============================================================================
-- Supabase / PostgreSQL Central Schema for Autonomous Lead Generation Engine
-- Run this in your Supabase Project -> SQL Editor to initialize all tables & indexes.
-- ==============================================================================

-- 1. Enriched Leads (Core output of scraping + AI research & contact discovery)
CREATE TABLE IF NOT EXISTS enriched_leads (
    id                      BIGSERIAL PRIMARY KEY,
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
    contacts                JSONB DEFAULT '[]'::jsonb,
    key_technologies        JSONB DEFAULT '[]'::jsonb,
    hiring_urgency          TEXT,
    lead_summary            TEXT,
    agent_thinking_process  TEXT,
    search_queries_used     JSONB DEFAULT '[]'::jsonb,
    status                  TEXT DEFAULT 'new',
    lead_type               TEXT DEFAULT 'others',
    date_posted             TEXT,
    scraped_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    scheduled_job_id        TEXT,
    outreach_mode           TEXT DEFAULT 'auto',
    outreach_state          TEXT DEFAULT 'open',
    outreach_stage          INTEGER DEFAULT 1,
    next_send_at            TEXT,
    last_sent_at            TEXT,
    resolved_timezone       TEXT
);

-- Indexes for fast queries & filtering
CREATE INDEX IF NOT EXISTS idx_enriched_leads_job_url ON enriched_leads(job_url);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_company ON enriched_leads(company);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_site ON enriched_leads(site);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_score ON enriched_leads(relevance_score);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_status ON enriched_leads(status);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_created ON enriched_leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_scraped ON enriched_leads(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_sched_id ON enriched_leads(scheduled_job_id);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_outreach_due ON enriched_leads(outreach_mode, outreach_state, next_send_at);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_contacts_gin ON enriched_leads USING GIN (contacts);
CREATE INDEX IF NOT EXISTS idx_enriched_leads_tech_gin ON enriched_leads USING GIN (key_technologies);

-- 2. Raw Scraped Jobs (Unprocessed jobs from JobSpy)
CREATE TABLE IF NOT EXISTS raw_jobs (
    id                      BIGSERIAL PRIMARY KEY,
    raw_id                  TEXT,
    job_url                 TEXT UNIQUE NOT NULL,
    title                   TEXT NOT NULL,
    company                 TEXT NOT NULL,
    location                TEXT,
    site                    TEXT,
    description             TEXT,
    job_type                TEXT,
    salary_min              NUMERIC,
    salary_max              NUMERIC,
    salary_currency         TEXT,
    date_posted             TEXT,
    scraped_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    scheduled_job_id        TEXT,
    raw_metadata            JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_raw_jobs_job_url ON raw_jobs(job_url);
CREATE INDEX IF NOT EXISTS idx_raw_jobs_scraped ON raw_jobs(scraped_at DESC);

-- 3. Job Leads (Scraped LinkedIn / Direct leads)
CREATE TABLE IF NOT EXISTS job_leads (
    id                      BIGSERIAL PRIMARY KEY,
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
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_leads_job_url ON job_leads(job_url);
CREATE INDEX IF NOT EXISTS idx_job_leads_company ON job_leads(company);

-- 4. Scheduled Scraping Jobs
CREATE TABLE IF NOT EXISTS scheduled_scraping_jobs (
    id                      TEXT PRIMARY KEY,
    job_title               TEXT NOT NULL,
    target_location         TEXT,
    company_size            TEXT,
    scraping_limit          INTEGER DEFAULT 10,
    scheduled_date          TEXT NOT NULL,
    status                  TEXT DEFAULT 'pending',
    result_count            INTEGER DEFAULT 0,
    last_run_at             TIMESTAMPTZ,
    error_message           TEXT,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_date ON scheduled_scraping_jobs(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status ON scheduled_scraping_jobs(status);

-- 5. Email Templates
CREATE TABLE IF NOT EXISTS email_templates (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    subject                 TEXT NOT NULL,
    body                    TEXT NOT NULL,
    tags                    TEXT DEFAULT '',
    cc                      TEXT DEFAULT '',
    attachment_path         TEXT,
    attachment_name         TEXT,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 6. Email Campaigns
CREATE TABLE IF NOT EXISTS email_campaigns (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    template_id             TEXT NOT NULL,
    template_name           TEXT,
    subject                 TEXT,
    attachment_path         TEXT,
    attachment_name         TEXT,
    status                  TEXT DEFAULT 'pending',
    total                   INTEGER DEFAULT 0,
    sent                    INTEGER DEFAULT 0,
    failed_count            INTEGER DEFAULT 0,
    audience_filter         JSONB DEFAULT '{}'::jsonb,
    cc                      TEXT DEFAULT '',
    campaign_type           TEXT DEFAULT 'one_shot',
    start_date              TIMESTAMPTZ,
    reminder_email          TEXT,
    reminder_hours_before   INTEGER DEFAULT 24,
    smtp_account_id         TEXT,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    finished_at             TIMESTAMPTZ
);

-- 7. Email Delivery Logs
CREATE TABLE IF NOT EXISTS email_campaign_logs (
    id                      TEXT PRIMARY KEY,
    campaign_id             TEXT NOT NULL,
    recipient_name          TEXT,
    recipient_email         TEXT,
    company_name            TEXT,
    sender_email            TEXT,
    status                  TEXT DEFAULT 'pending',
    error_message           TEXT,
    sent_at                 TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_email_logs_campaign_id ON email_campaign_logs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_email_logs_status ON email_campaign_logs(status);

-- 8. Saved Audiences / Recipient Lists
CREATE TABLE IF NOT EXISTS email_audiences (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    description             TEXT DEFAULT '',
    sources                 JSONB DEFAULT '["sqlite"]'::jsonb,
    filters                 JSONB DEFAULT '{}'::jsonb,
    manual_recipients       JSONB DEFAULT '[]'::jsonb,
    selected_recipients     JSONB DEFAULT '[]'::jsonb,
    contact_count           INTEGER DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 9. Drip Sequence Steps
CREATE TABLE IF NOT EXISTS campaign_sequences (
    id                      TEXT PRIMARY KEY,
    campaign_id             TEXT NOT NULL,
    step_number             INTEGER NOT NULL,
    template_id             TEXT NOT NULL,
    template_name           TEXT,
    subject                 TEXT,
    days_after              INTEGER DEFAULT 0,
    status                  TEXT DEFAULT 'pending',
    scheduled_at            TIMESTAMPTZ,
    fired_at                TIMESTAMPTZ,
    sent_count              INTEGER DEFAULT 0,
    failed_count            INTEGER DEFAULT 0,
    reminder_sent           INTEGER DEFAULT 0,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_campaign_sequences_camp_id ON campaign_sequences(campaign_id);

-- 10. Sequence Recipients
CREATE TABLE IF NOT EXISTS campaign_sequence_recipients (
    id                      TEXT PRIMARY KEY,
    sequence_id             TEXT NOT NULL,
    campaign_id             TEXT NOT NULL,
    recipient_name          TEXT,
    recipient_email         TEXT NOT NULL,
    company_name            TEXT,
    role                    TEXT,
    website                 TEXT,
    city                    TEXT,
    country                 TEXT,
    category                TEXT
);

-- 11. Individual Review & Send Queue
CREATE TABLE IF NOT EXISTS email_queue_items (
    id                      TEXT PRIMARY KEY,
    template_id             TEXT,
    template_name           TEXT,
    audience_id             TEXT,
    recipient_name          TEXT,
    recipient_email         TEXT NOT NULL,
    company_name            TEXT,
    role                    TEXT,
    website                 TEXT,
    city                    TEXT,
    country                 TEXT,
    category                TEXT,
    subject                 TEXT NOT NULL,
    body                    TEXT NOT NULL,
    raw_body                TEXT,
    cc                      TEXT DEFAULT '',
    status                  TEXT DEFAULT 'draft',
    error_message           TEXT,
    sent_at                 TIMESTAMPTZ,
    smtp_account_id         TEXT,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 12. Multi-SMTP Accounts
CREATE TABLE IF NOT EXISTS smtp_accounts (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    smtp_host               TEXT NOT NULL,
    smtp_port               INTEGER DEFAULT 587,
    smtp_user               TEXT NOT NULL,
    smtp_pass               TEXT NOT NULL,
    from_name               TEXT DEFAULT 'HirePilot AI',
    use_ssl                 BOOLEAN DEFAULT FALSE,
    use_tls                 BOOLEAN DEFAULT TRUE,
    is_default              BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 13. Legacy Single-Row SMTP Config
CREATE TABLE IF NOT EXISTS smtp_config (
    id                      INTEGER PRIMARY KEY DEFAULT 1,
    smtp_host               TEXT,
    smtp_port               INTEGER DEFAULT 587,
    smtp_user               TEXT,
    smtp_pass               TEXT,
    from_name               TEXT DEFAULT 'HirePilot AI',
    use_ssl                 BOOLEAN DEFAULT FALSE,
    use_tls                 BOOLEAN DEFAULT TRUE,
    company_smtp_account_id TEXT,
    freelancer_smtp_account_id TEXT,
    company_smtp_account_ids TEXT DEFAULT '[]',
    freelancer_smtp_account_ids TEXT DEFAULT '[]',
    sending_mode            TEXT DEFAULT 'both',
    updated_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ==============================================================================
-- Row Level Security (RLS) - Secure all public tables
-- ==============================================================================
ALTER TABLE IF EXISTS enriched_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS raw_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS job_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scheduled_scraping_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS email_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS email_campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS email_campaign_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS email_audiences ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS campaign_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS campaign_sequence_recipients ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS email_queue_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS smtp_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS smtp_config ENABLE ROW LEVEL SECURITY;

