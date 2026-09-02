"""Main CLI Entrypoint for the Lead Generation & Enrichment Engine."""

import sys
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config.settings import settings
from core.logging import logger
from db.sqlite import sqlite_manager
from db.models import RawJobPosting
from enrichment.agent import LeadEnrichmentAgent
from pipeline.orchestrator import LeadGenOrchestrator
from llm.registry import LLMProviderRegistry

console = Console()


@click.group()
def cli():
    """Lead Generation CLI: Automated Scraping, LLM Web Research & SQLite Storage."""
    pass


@cli.command("run")
@click.option("--search", "-s", required=True, help="Job title or search keyword (e.g. 'Python Backend Developer', 'Freelance AI Engineer')")
@click.option("--location", "-l", default="Remote", help="Job location or 'Remote'")
@click.option("--sites", default="linkedin,naukri", help="Comma-separated sites (e.g. 'linkedin,naukri,indeed')")
@click.option("--company-size", "-c", default="small", type=click.Choice(["small", "medium", "large", "all"], case_sensitive=False), help="Company size: 'small' (max 50 employees, default), 'medium' (51-500), 'large' (500+), 'all'")
@click.option("--job-type", "-t", default="all", type=click.Choice(["all", "contract", "fulltime", "parttime", "internship"], case_sensitive=False), help="Job type: 'all', 'contract' (freelance/C2C), 'fulltime', 'parttime'")
@click.option("--limit", "-n", default=10, type=int, help="Maximum jobs to scrape")
@click.option("--provider", "-p", default=None, help="LLM Provider: 'gemini' or 'nvidia'")
@click.option("--model", "-m", default=None, help="Specific model name to use")
@click.option("--min-score", default=30, type=int, help="Minimum lead relevance score (0-100)")
@click.option("--hours-old", default=72, type=int, help="Scrape jobs posted within last N hours")
def run_pipeline(search: str, location: str, sites: str, company_size: str, job_type: str, limit: int, provider: str, model: str, min_score: int, hours_old: int):
    """Run the complete scraping -> research agent -> SQLite pipeline."""
    target_sites = [s.strip() for s in sites.split(",") if s.strip()]
    
    console.print(Panel.fit(
        f"[bold cyan]Lead Generation Pipeline[/bold cyan]\n"
        f"[green]Search:[/green] {search}\n"
        f"[green]Location:[/green] {location}\n"
        f"[green]Target Company Size:[/green] [bold yellow]{company_size.upper()} (Max 50)[/bold yellow]\n"
        f"[green]Target Job Type:[/green] [bold yellow]{job_type.upper()}[/bold yellow]\n"
        f"[green]Sites:[/green] {', '.join(target_sites)}\n"
        f"[green]LLM Provider:[/green] {provider or settings.default_llm_provider}\n"
        f"[green]Limit:[/green] {limit}",
        title="[bold yellow]Pipeline Initializing[/bold yellow]"
    ))

    # Initialize agent with chosen provider
    agent = LeadEnrichmentAgent(provider_name=provider, model_name=model)
    orchestrator = LeadGenOrchestrator(
        agent=agent,
        min_relevance_score=min_score,
    )

    metrics = orchestrator.run(
        search_term=search,
        location=location,
        sites=target_sites,
        company_size=company_size,
        job_type=job_type,
        results_limit=limit,
        hours_old=hours_old,
    )

    # Display results summary
    table = Table(title="Pipeline Execution Summary", border_style="cyan")
    table.add_column("Metric", style="bold white")
    table.add_column("Value", style="green")

    table.add_row("Execution Time", f"{metrics.duration_seconds}s")
    table.add_row("Total Job Postings Scraped", str(metrics.total_scraped))
    table.add_row("Unique Companies Discovered", str(metrics.unique_companies_count))
    table.add_row("Target Company Size", f"{metrics.target_company_size.upper()} (Max 50)")
    table.add_row("Target Job Type", metrics.target_job_type.upper())
    table.add_row("Already in DB (Skipped)", str(metrics.already_existing))
    table.add_row("Processed by AI Agent", str(metrics.processed_by_agent))
    table.add_row("Qualified Leads Saved", f"{metrics.saved_to_db} ({(metrics.saved_to_db/max(1, metrics.processed_by_agent)*100):.1f}% yield)")
    table.add_row("Rejected / Non-Qualified", f"{metrics.rejected_by_llm} (Size Mismatch: {metrics.rejected_by_size})")
    table.add_row("Decision-Maker Contacts Found", str(metrics.total_contacts_discovered))

    console.print(table)


@cli.command("test-db")
def test_db():
    """Test SQLite connection and display database status."""
    try:
        sqlite_manager.connect()
        stats = sqlite_manager.get_stats()
        
        console.print(Panel.fit(
            f"[bold green]SQLite Database: OK[/bold green]\n"
            f"[cyan]Database File:[/cyan] {sqlite_manager.db_path}\n"
            f"[cyan]Enriched Leads Count:[/cyan] {stats.get('leads_count', 0)}\n"
            f"[cyan]Raw Jobs Count:[/cyan] {stats.get('raw_jobs_count', 0)}\n"
            f"[cyan]Total Contacts Discovered:[/cyan] {stats.get('total_contacts_discovered', 0)}\n"
            f"[cyan]Avg Relevance Score:[/cyan] {stats.get('avg_relevance_score', 0.0)}/100",
            title="SQLite Status"
        ))
    except Exception as e:
        console.print(f"[bold red]SQLite Connection Failed:[/bold red] {e}")


@cli.command("clear-db")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def clear_db_command(force: bool):
    """Clear all enriched leads and raw jobs from SQLite."""
    try:
        sqlite_manager.connect()
        if not force:
            from rich.prompt import Confirm
            if not Confirm.ask(f"[bold red]Are you sure you want to clear all leads in {sqlite_manager.db_path}?[/bold red]"):
                console.print("[yellow]Cancelled.[/yellow]")
                return

        result = sqlite_manager.clear_database()
        console.print(f"[bold green]Successfully cleared database:[/bold green] Deleted {result['leads_deleted']} leads and {result['raw_jobs_deleted']} raw jobs.")
    except Exception as e:
        console.print(f"[bold red]Error clearing database:[/bold red] {e}")


@cli.command("list-leads")
@click.option("--limit", "-n", default=10, type=int, help="Number of leads to list")
@click.option("--min-score", default=0, type=int, help="Filter by minimum relevance score")
@click.option("--company-size", "-c", default=None, help="Filter by company size ('small', 'medium', 'large')")
@click.option("--job-type", "-t", default=None, help="Filter by job type ('contract', 'fulltime', 'parttime')")
@click.option("--hours-old", "-h", default=None, type=int, help="Filter leads found within last N hours (e.g. 24, 72, 168, 720)")
def list_leads(limit: int, min_score: int, company_size: str, job_type: str, hours_old: int):
    """List enriched leads saved in SQLite."""
    try:
        sqlite_manager.connect()
        res = sqlite_manager.get_leads(
            company_size=company_size,
            job_type=job_type,
            hours_old=hours_old,
            min_score=min_score,
            limit=limit,
        )
        leads = res.get("leads", [])

        if not leads:
            console.print("[yellow]No leads found matching criteria.[/yellow]")
            return

        table = Table(title=f"Enriched Leads in SQLite (Top {len(leads)})", border_style="blue")
        table.add_column("Company", style="bold white")
        table.add_column("Size", style="magenta")
        table.add_column("Type", style="yellow")
        table.add_column("Title", style="cyan")
        table.add_column("Score", justify="right", style="green")
        table.add_column("Contacts", style="cyan")
        table.add_column("Domain", style="yellow")

        for lead in leads:
            contacts_summary = ", ".join([
                f"{c.get('name', 'Unknown')} ({c.get('role', 'Recruiter')}: {c.get('email') or c.get('phone') or 'LinkedIn'})"
                for c in lead.get("contacts", [])[:2]
            ]) or "None found"
            
            table.add_row(
                lead.get("company", "N/A"),
                lead.get("company_size", "11-50 employees"),
                lead.get("job_type", "Contract"),
                lead.get("title", "N/A")[:25],
                f"{lead.get('relevance_score', 0)}/100",
                contacts_summary[:40],
                lead.get("company_domain", "N/A") or "N/A",
            )

        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Error querying SQLite:[/bold red] {e}")


@cli.command("test-enrichment")
@click.option("--provider", "-p", default=None, help="LLM Provider: 'gemini' or 'nvidia'")
@click.option("--company", "-c", default="Anthropic", help="Test company name")
@click.option("--title", "-t", default="Senior Python Backend Engineer", help="Test job title")
def test_enrichment(provider: str, company: str, title: str):
    """Test LLM agent reasoning and Tavily search on a mock job posting."""
    console.print(f"[yellow]Testing Enrichment Agent on mock job: '{title}' at '{company}'...[/yellow]")
    
    sample_job = RawJobPosting(
        title=title,
        company=company,
        location="San Francisco, CA / Remote",
        job_url=f"https://www.linkedin.com/jobs/view/mock-{company.lower()}-test",
        site="linkedin",
        description=(
            f"We are hiring a {title} at {company}. Key requirements include Python, FastAPI, distributed systems, "
            f"and LLM infrastructure. We offer competitive compensation and remote flexibility."
        ),
    )

    agent = LeadEnrichmentAgent(provider_name=provider)
    enriched = agent.enrich_job(sample_job)

    console.print(Panel.fit(
        f"[bold green]Company Domain:[/bold green] {enriched.company_domain}\n"
        f"[bold green]Relevance Score:[/bold green] {enriched.relevance_score}/100\n"
        f"[bold green]Hiring Urgency:[/bold green] {enriched.hiring_urgency}\n"
        f"[bold green]Summary:[/bold green] {enriched.lead_summary}\n"
        f"[bold green]Technologies:[/bold green] {', '.join(enriched.key_technologies)}\n"
        f"[bold green]Contacts Found ({len(enriched.contacts)}):[/bold green]\n" +
        "\n".join([f"  - {c.name} | {c.role} | Email: {c.email} | Phone: {c.phone} | Score: {c.confidence_score}" for c in enriched.contacts]) +
        f"\n\n[bold yellow]Agent Thinking Process:[/bold yellow]\n{enriched.agent_thinking_process}",
        title=f"Enrichment Result: {enriched.company}"
    ))


@cli.command("serve")
@click.option("--host", default="0.0.0.0", help="Host address to bind to")
@click.option("--port", default=8000, type=int, help="Port to run web server on")
@click.option("--reload/--no-reload", default=True, help="Enable auto-reload on code changes")
def serve(host: str, port: int, reload: bool):
    """Start the FastAPI Web Server and LeadPulse Dashboard UI."""
    import uvicorn
    console.print(Panel.fit(
        f"[bold cyan]LeadPulse AI Web Dashboard[/bold cyan]\n"
        f"[green]URL:[/green] http://localhost:{port}\n"
        f"[green]API Docs:[/green] http://localhost:{port}/docs\n"
        f"[cyan]Host:[/cyan] {host}:{port}",
        title="[bold yellow]Server Initializing[/bold yellow]"
    ))
    uvicorn.run("app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
