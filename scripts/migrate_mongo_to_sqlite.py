#!/usr/bin/env python3
"""
Data Migration Script: MongoDB -> SQLite.
Extracts all enriched leads, raw jobs, and historical job leads from MongoDB
and persists them into the centralized SQLite database.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from pymongo import MongoClient

from config.settings import settings
from db.sqlite import sqlite_manager

console = Console()


def sanitize_val(val):
    """Serialize MongoDB BSON values into Python/SQLite compatible primitives."""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, (list, dict)):
        return json.dumps(val)
    return val


def migrate():
    console.print(Panel.fit(
        f"[bold cyan]MongoDB to SQLite Data Migration[/bold cyan]\n"
        f"[green]Source MongoDB URI:[/green] {settings.mongodb_uri}\n"
        f"[green]Source Database:[/green] {settings.mongodb_db_name}\n"
        f"[green]Target SQLite Database:[/green] {sqlite_manager.db_path}",
        title="[bold yellow]Migration Starting[/bold yellow]"
    ))

    # Connect to MongoDB
    console.print("[yellow]Connecting to MongoDB...[/yellow]")
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15000)
    client.admin.command("ping")
    db = client[settings.mongodb_db_name]
    console.print("[bold green]MongoDB connected successfully.[/bold green]")

    # Connect to SQLite
    sqlite_manager.connect()
    conn = sqlite_manager.get_connection()
    cur = conn.cursor()

    counts = {
        "enriched_leads_mongo": 0,
        "enriched_leads_sqlite": 0,
        "raw_jobs_mongo": 0,
        "raw_jobs_sqlite": 0,
        "job_leads_mongo": 0,
        "job_leads_sqlite": 0,
    }

    # 1. Migrate Enriched Leads
    console.print("\n[cyan]Migrating 'enriched_leads'...[/cyan]")
    enriched_cursor = db.enriched_leads.find({})
    for doc in enriched_cursor:
        counts["enriched_leads_mongo"] += 1
        job_url = doc.get("job_url")
        if not job_url:
            continue

        contacts = doc.get("contacts") or []
        if isinstance(contacts, str):
            try:
                contacts = json.loads(contacts)
            except Exception:
                contacts = []

        contacts_json = json.dumps(contacts)
        tech_json = json.dumps(doc.get("key_technologies") or [])
        queries_json = json.dumps(doc.get("search_queries_used") or [])

        created_at = doc.get("created_at")
        created_at_iso = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or datetime.utcnow().isoformat())
        updated_at = doc.get("updated_at")
        updated_at_iso = updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at or datetime.utcnow().isoformat())

        cur.execute("""
            INSERT INTO enriched_leads (
                job_url, title, company, site, location, job_type, job_description,
                is_valid_lead, relevance_score, company_domain, company_summary,
                company_size, contacts, key_technologies, hiring_urgency,
                lead_summary, agent_thinking_process, search_queries_used,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_url) DO UPDATE SET
                title=excluded.title,
                company=excluded.company,
                site=excluded.site,
                location=excluded.location,
                job_type=excluded.job_type,
                job_description=excluded.job_description,
                is_valid_lead=excluded.is_valid_lead,
                relevance_score=excluded.relevance_score,
                company_domain=excluded.company_domain,
                company_summary=excluded.company_summary,
                company_size=excluded.company_size,
                contacts=excluded.contacts,
                key_technologies=excluded.key_technologies,
                hiring_urgency=excluded.hiring_urgency,
                lead_summary=excluded.lead_summary,
                agent_thinking_process=excluded.agent_thinking_process,
                search_queries_used=excluded.search_queries_used,
                status=excluded.status,
                updated_at=excluded.updated_at
        """, (
            job_url,
            doc.get("title") or "Unknown Title",
            doc.get("company") or "Unknown Company",
            doc.get("site") or "linkedin",
            doc.get("location") or "",
            doc.get("job_type") or "",
            doc.get("job_description") or "",
            1 if doc.get("is_valid_lead", True) else 0,
            int(doc.get("relevance_score") or 50),
            doc.get("company_domain") or "",
            doc.get("company_summary") or "",
            doc.get("company_size") or "",
            contacts_json,
            tech_json,
            doc.get("hiring_urgency") or "",
            doc.get("lead_summary") or "",
            doc.get("agent_thinking_process") or "",
            queries_json,
            doc.get("status") or "new",
            created_at_iso,
            updated_at_iso,
        ))

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM enriched_leads")
    counts["enriched_leads_sqlite"] = cur.fetchone()[0]
    console.print(f"[green]Migrated {counts['enriched_leads_sqlite']} enriched leads.[/green]")

    # 2. Migrate Raw Jobs
    console.print("\n[cyan]Migrating 'raw_jobs'...[/cyan]")
    raw_cursor = db.raw_jobs.find({})
    for doc in raw_cursor:
        counts["raw_jobs_mongo"] += 1
        job_url = doc.get("job_url")
        if not job_url:
            continue

        raw_meta = doc.get("raw_metadata") or {}
        raw_meta_json = json.dumps(raw_meta, default=str) if isinstance(raw_meta, dict) else str(raw_meta)

        scraped_at = doc.get("scraped_at")
        scraped_at_iso = scraped_at.isoformat() if isinstance(scraped_at, datetime) else str(scraped_at or datetime.utcnow().isoformat())

        sal_min = doc.get("salary_min")
        sal_max = doc.get("salary_max")
        try:
            sal_min = float(sal_min) if sal_min is not None else None
        except Exception:
            sal_min = None
        try:
            sal_max = float(sal_max) if sal_max is not None else None
        except Exception:
            sal_max = None

        cur.execute("""
            INSERT INTO raw_jobs (
                raw_id, job_url, title, company, location, site, description,
                job_type, salary_min, salary_max, salary_currency, date_posted,
                scraped_at, raw_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_url) DO UPDATE SET
                raw_id=excluded.raw_id,
                title=excluded.title,
                company=excluded.company,
                location=excluded.location,
                site=excluded.site,
                description=excluded.description,
                job_type=excluded.job_type,
                salary_min=excluded.salary_min,
                salary_max=excluded.salary_max,
                salary_currency=excluded.salary_currency,
                date_posted=excluded.date_posted,
                raw_metadata=excluded.raw_metadata
        """, (
            str(doc.get("id") or ""),
            job_url,
            doc.get("title") or "Unknown Title",
            doc.get("company") or "Unknown Company",
            doc.get("location") or "",
            doc.get("site") or "linkedin",
            doc.get("description") or "",
            doc.get("job_type") or "",
            sal_min,
            sal_max,
            doc.get("salary_currency") or "",
            doc.get("date_posted") or "",
            scraped_at_iso,
            raw_meta_json,
        ))

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM raw_jobs")
    counts["raw_jobs_sqlite"] = cur.fetchone()[0]
    console.print(f"[green]Migrated {counts['raw_jobs_sqlite']} raw jobs.[/green]")

    # 3. Migrate Historical Job Leads
    console.print("\n[cyan]Migrating 'job_leads'...[/cyan]")
    job_leads_cursor = db.job_leads.find({})
    for doc in job_leads_cursor:
        counts["job_leads_mongo"] += 1
        job_url = doc.get("job_url")
        if not job_url:
            continue

        created_at = doc.get("created_at")
        created_at_iso = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or datetime.utcnow().isoformat())
        updated_at = doc.get("updated_at")
        updated_at_iso = updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at or datetime.utcnow().isoformat())

        emails = doc.get("emails")
        if isinstance(emails, list):
            emails = ", ".join(emails)
        elif emails is None:
            emails = ""

        phones = doc.get("phones")
        if isinstance(phones, list):
            phones = ", ".join(phones)
        elif phones is None:
            phones = ""

        cur.execute("""
            INSERT INTO job_leads (
                job_url, title, company, location, site, company_website,
                company_url, company_phone, company_email, company_industry,
                company_num_employees, emails, phones, recruiter_name, ai_notes,
                is_remote, enriched_by_ai, date_posted, description,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_url) DO UPDATE SET
                title=excluded.title,
                company=excluded.company,
                location=excluded.location,
                site=excluded.site,
                company_website=excluded.company_website,
                company_url=excluded.company_url,
                company_phone=excluded.company_phone,
                company_email=excluded.company_email,
                company_industry=excluded.company_industry,
                company_num_employees=excluded.company_num_employees,
                emails=excluded.emails,
                phones=excluded.phones,
                recruiter_name=excluded.recruiter_name,
                ai_notes=excluded.ai_notes,
                is_remote=excluded.is_remote,
                enriched_by_ai=excluded.enriched_by_ai,
                date_posted=excluded.date_posted,
                description=excluded.description,
                updated_at=excluded.updated_at
        """, (
            job_url,
            doc.get("title") or "",
            doc.get("company") or "",
            doc.get("location") or "",
            doc.get("site") or "",
            doc.get("company_website") or "",
            doc.get("company_url") or "",
            doc.get("company_phone") or "",
            doc.get("company_email") or "",
            doc.get("company_industry") or "",
            doc.get("company_num_employees") or "",
            str(emails),
            str(phones),
            doc.get("recruiter_name") or "",
            doc.get("ai_notes") or "",
            1 if doc.get("is_remote") else 0,
            1 if doc.get("enriched_by_ai") else 0,
            str(doc.get("date_posted") or ""),
            doc.get("description") or "",
            created_at_iso,
            updated_at_iso,
        ))

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM job_leads")
    counts["job_leads_sqlite"] = cur.fetchone()[0]
    console.print(f"[green]Migrated {counts['job_leads_sqlite']} historical job leads.[/green]")

    conn.close()
    client.close()

    # Print Summary Table
    table = Table(title="MongoDB to SQLite Migration Summary", border_style="green")
    table.add_column("Collection / Table", style="bold cyan")
    table.add_column("MongoDB Count", style="yellow")
    table.add_column("SQLite Count", style="bold green")
    table.add_column("Status", style="bold white")

    for col in ("enriched_leads", "raw_jobs", "job_leads"):
        m_cnt = counts[f"{col}_mongo"]
        s_cnt = counts[f"{col}_sqlite"]
        status = "MATCH [OK]" if m_cnt == s_cnt else f"PROCESSED ({s_cnt})"
        table.add_row(col, str(m_cnt), str(s_cnt), status)

    console.print(table)
    console.print(Panel.fit("[bold green]All MongoDB data migrated to SQLite successfully![/bold green]"))


if __name__ == "__main__":
    migrate()
