"""
Migration Script: SQLite -> Supabase (Cloud PostgreSQL)

Transfers all enriched leads, raw jobs, scheduled tasks, and email campaign data
from local SQLite databases to your Supabase PostgreSQL cloud database.

Usage:
    python scripts/migrate_sqlite_to_supabase.py
    python scripts/migrate_sqlite_to_supabase.py --db-url "postgresql://postgres.xxx:pass@aws-0-region.pooler.supabase.com:6543/postgres"
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from db.postgres import PostgresManager
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

console = Console()


def get_sqlite_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def migrate_data(database_url: str):
    console.print(Panel.fit(
        "[bold cyan]HirePilot AI: SQLite -> Supabase Migration Engine[/bold cyan]\n"
        "[dim]Migrating local SQLite records to your Supabase PostgreSQL cloud database[/dim]",
        border_style="cyan"
    ))

    data_dir = PROJECT_ROOT / "data"
    leads_db_path = data_dir / "leads.db"
    if not leads_db_path.exists() and (data_dir / "eu_startups.db").exists():
        leads_db_path = data_dir / "eu_startups.db"
    email_db_path = data_dir / "email_campaigns.db"

    if not leads_db_path.exists():
        console.print(f"[yellow]Notice: {leads_db_path} does not exist. Checking data directory...[/yellow]")

    # 1. Connect to Supabase
    with console.status("[bold green]Connecting to Supabase PostgreSQL...[/bold green]"):
        try:
            pg = PostgresManager(database_url)
            pg.connect()
            console.print("[green]✔ Successfully connected to Supabase PostgreSQL & verified schema![/green]\n")
        except Exception as e:
            console.print(f"[bold red]✘ Connection to Supabase failed:[/bold red] {e}")
            sys.exit(1)

    pg_conn = pg.get_connection()
    pg_cur = pg_conn.cursor()

    stats_summary = {}
    start_time = time.time()

    # 2. Migrate Enriched Leads
    if leads_db_path.exists():
        sq_conn = get_sqlite_conn(leads_db_path)
        sq_cur = sq_conn.cursor()

        # Check if enriched_leads table exists in sqlite
        sq_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='enriched_leads'")
        if sq_cur.fetchone():
            sq_cur.execute("SELECT * FROM enriched_leads")
            leads = sq_cur.fetchall()
            console.print(f"[cyan]Found {len(leads)} enriched leads in {leads_db_path.name}...[/cyan]")

            inserted_leads = 0
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("[green]Migrating enriched leads...", total=len(leads))
                for r in leads:
                    d = dict(r)
                    # Convert JSON fields
                    for f in ("contacts", "key_technologies", "search_queries_used"):
                        val = d.get(f)
                        if isinstance(val, str):
                            try:
                                json.loads(val)
                            except Exception:
                                d[f] = "[]"
                        elif val is None:
                            d[f] = "[]"

                    try:
                        pg_cur.execute("""
                            INSERT INTO enriched_leads (
                                job_url, title, company, site, location, job_type, job_description,
                                is_valid_lead, relevance_score, company_domain, company_summary,
                                company_size, contacts, key_technologies, hiring_urgency,
                                lead_summary, agent_thinking_process, search_queries_used,
                                status, lead_type, date_posted, scraped_at, created_at, updated_at,
                                scheduled_job_id, outreach_mode, outreach_state, outreach_stage, next_send_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s::jsonb, %s::jsonb, %s,
                                %s, %s, %s::jsonb,
                                %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            ON CONFLICT(job_url) DO UPDATE SET
                                title=EXCLUDED.title,
                                company=EXCLUDED.company,
                                contacts=EXCLUDED.contacts,
                                relevance_score=EXCLUDED.relevance_score,
                                status=EXCLUDED.status,
                                updated_at=EXCLUDED.updated_at
                        """, (
                            d.get("job_url"),
                            d.get("title") or "Unknown Title",
                            d.get("company") or "Unknown Company",
                            d.get("site"),
                            d.get("location"),
                            d.get("job_type"),
                            d.get("job_description"),
                            1 if d.get("is_valid_lead", 1) else 0,
                            d.get("relevance_score", 50),
                            d.get("company_domain"),
                            d.get("company_summary"),
                            d.get("company_size"),
                            d.get("contacts", "[]"),
                            d.get("key_technologies", "[]"),
                            d.get("hiring_urgency"),
                            d.get("lead_summary"),
                            d.get("agent_thinking_process"),
                            d.get("search_queries_used", "[]"),
                            d.get("status", "new"),
                            d.get("lead_type", "others"),
                            str(d.get("date_posted")) if d.get("date_posted") else None,
                            str(d.get("scraped_at")) if d.get("scraped_at") else None,
                            str(d.get("created_at")) if d.get("created_at") else None,
                            str(d.get("updated_at")) if d.get("updated_at") else None,
                            d.get("scheduled_job_id"),
                            d.get("outreach_mode", "auto"),
                            d.get("outreach_state", "open"),
                            d.get("outreach_stage", 1),
                            d.get("next_send_at"),
                        ))
                        inserted_leads += 1
                    except Exception as err:
                        console.print(f"[dim yellow]Warning migrating lead {d.get('job_url')}: {err}[/dim yellow]")
                    progress.advance(task)

            pg_conn.commit()
            stats_summary["Enriched Leads"] = inserted_leads

        # 3. Migrate Raw Jobs
        sq_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_jobs'")
        if sq_cur.fetchone():
            sq_cur.execute("SELECT * FROM raw_jobs")
            raw_jobs = sq_cur.fetchall()
            inserted_raw = 0
            for r in raw_jobs:
                d = dict(r)
                meta = d.get("raw_metadata") or "{}"
                if not isinstance(meta, str):
                    meta = json.dumps(meta, default=str)
                try:
                    pg_cur.execute("""
                        INSERT INTO raw_jobs (
                            raw_id, job_url, title, company, location, site, description,
                            job_type, salary_min, salary_max, salary_currency, date_posted,
                            scraped_at, scheduled_job_id, raw_metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT(job_url) DO UPDATE SET title=EXCLUDED.title
                    """, (
                        d.get("raw_id"),
                        d.get("job_url"),
                        d.get("title") or "Unknown Title",
                        d.get("company") or "Unknown Company",
                        d.get("location"),
                        d.get("site"),
                        d.get("description"),
                        d.get("job_type"),
                        d.get("salary_min"),
                        d.get("salary_max"),
                        d.get("salary_currency"),
                        str(d.get("date_posted")) if d.get("date_posted") else None,
                        str(d.get("scraped_at")) if d.get("scraped_at") else None,
                        d.get("scheduled_job_id"),
                        meta,
                    ))
                    inserted_raw += 1
                except Exception:
                    pass
            pg_conn.commit()
            stats_summary["Raw Jobs"] = inserted_raw

        # 4. Migrate Scheduled Jobs
        sq_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_scraping_jobs'")
        if sq_cur.fetchone():
            sq_cur.execute("SELECT * FROM scheduled_scraping_jobs")
            sched_jobs = sq_cur.fetchall()
            inserted_sched = 0
            for r in sched_jobs:
                d = dict(r)
                try:
                    pg_cur.execute("""
                        INSERT INTO scheduled_scraping_jobs (
                            id, job_title, target_location, company_size, scraping_limit,
                            scheduled_date, status, result_count, last_run_at, error_message,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status
                    """, (
                        d.get("id"),
                        d.get("job_title"),
                        d.get("target_location"),
                        d.get("company_size"),
                        d.get("scraping_limit", 10),
                        d.get("scheduled_date"),
                        d.get("status", "pending"),
                        d.get("result_count", 0),
                        str(d.get("last_run_at")) if d.get("last_run_at") else None,
                        d.get("error_message"),
                        str(d.get("created_at")) if d.get("created_at") else None,
                        str(d.get("updated_at")) if d.get("updated_at") else None,
                    ))
                    inserted_sched += 1
                except Exception:
                    pass
            pg_conn.commit()
            stats_summary["Scheduled Scraping Jobs"] = inserted_sched

        sq_conn.close()

    # 5. Migrate Email Campaigns & Templates
    if email_db_path.exists():
        em_conn = get_sqlite_conn(email_db_path)
        em_cur = em_conn.cursor()

        # Templates
        em_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_templates'")
        if em_cur.fetchone():
            em_cur.execute("SELECT * FROM email_templates")
            tpls = em_cur.fetchall()
            inserted_tpls = 0
            for r in tpls:
                d = dict(r)
                try:
                    pg_cur.execute("""
                        INSERT INTO email_templates (
                            id, name, subject, body, tags, cc, attachment_path, attachment_name, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(id) DO UPDATE SET subject=EXCLUDED.subject, body=EXCLUDED.body
                    """, (
                        d.get("id"),
                        d.get("name"),
                        d.get("subject"),
                        d.get("body"),
                        d.get("tags", ""),
                        d.get("cc", ""),
                        d.get("attachment_path"),
                        d.get("attachment_name"),
                        str(d.get("created_at")) if d.get("created_at") else None,
                        str(d.get("updated_at")) if d.get("updated_at") else None,
                    ))
                    inserted_tpls += 1
                except Exception:
                    pass
            pg_conn.commit()
            stats_summary["Email Templates"] = inserted_tpls

        # Campaigns
        em_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_campaigns'")
        if em_cur.fetchone():
            em_cur.execute("SELECT * FROM email_campaigns")
            camps = em_cur.fetchall()
            inserted_camps = 0
            for r in camps:
                d = dict(r)
                try:
                    pg_cur.execute("""
                        INSERT INTO email_campaigns (
                            id, name, template_id, template_name, subject, attachment_path, attachment_name,
                            status, total, sent, failed_count, cc, campaign_type, smtp_account_id,
                            created_at, updated_at, finished_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status, sent=EXCLUDED.sent
                    """, (
                        d.get("id"),
                        d.get("name"),
                        d.get("template_id"),
                        d.get("template_name"),
                        d.get("subject"),
                        d.get("attachment_path"),
                        d.get("attachment_name"),
                        d.get("status", "pending"),
                        d.get("total", 0),
                        d.get("sent", 0),
                        d.get("failed_count", 0),
                        d.get("cc", ""),
                        d.get("campaign_type", "one_shot"),
                        d.get("smtp_account_id"),
                        str(d.get("created_at")) if d.get("created_at") else None,
                        str(d.get("updated_at")) if d.get("updated_at") else None,
                        str(d.get("finished_at")) if d.get("finished_at") else None,
                    ))
                    inserted_camps += 1
                except Exception:
                    pass
            pg_conn.commit()
            stats_summary["Email Campaigns"] = inserted_camps

        em_conn.close()

    pg_conn.close()
    pg.close()

    # Results Table
    table = Table(title="Migration Summary", border_style="green")
    table.add_column("Category", style="cyan", no_wrap=True)
    table.add_column("Records Migrated", justify="right", style="bold green")

    for cat, count in stats_summary.items():
        table.add_row(cat, str(count))

    console.print(table)
    elapsed = round(time.time() - start_time, 2)
    console.print(f"\n[bold green]✔ Migration to Supabase completed successfully in {elapsed}s![/bold green]")
    console.print("[dim]You can now open your Supabase Table Editor to view all your migrated leads.[/dim]\n")


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite databases to Supabase PostgreSQL.")
    parser.add_argument("--db-url", type=str, default=None, help="Supabase / PostgreSQL connection URI")
    args = parser.parse_args()

    db_url = args.db_url or settings.database_url
    if not db_url or not db_url.strip():
        console.print("[bold red]Error:[/bold red] No DATABASE_URL provided.")
        console.print("Pass via [cyan]--db-url <URL>[/cyan] or set [cyan]DATABASE_URL[/cyan] in your [cyan].env[/cyan] file.")
        sys.exit(1)

    migrate_data(db_url.strip())


if __name__ == "__main__":
    main()
