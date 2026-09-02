#!/usr/bin/env python3
"""
Database Clearing Script for Lead Pulse AI.
Clears all enriched leads and raw job postings from the centralized SQLite database.

Usage:
    python clear_db.py            # Prompt for confirmation before clearing
    python clear_db.py --force    # Clear without confirmation prompt
"""

import sys
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from config.settings import settings
from db.sqlite import sqlite_manager
from core.logging import logger

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Clear leads and raw jobs from the centralized SQLite database.")
    parser.add_argument(
        "-f", "--force", action="store_true", help="Bypass confirmation prompt and clear immediately"
    )
    args = parser.parse_args()

    console.print(Panel.fit(
        f"[bold cyan]Lead Pulse AI — Database Reset Tool[/bold cyan]\n"
        f"[green]Target Database:[/green] {sqlite_manager.db_path}\n"
        f"[green]Table 1:[/green] enriched_leads\n"
        f"[green]Table 2:[/green] raw_jobs",
        title="[bold yellow]SQLite Status Check[/bold yellow]"
    ))

    try:
        sqlite_manager.connect()
        stats_before = sqlite_manager.get_stats()
        leads_count_before = stats_before.get("leads_count", 0)
        raw_count_before = stats_before.get("raw_jobs_count", 0)

        console.print(f"[bold white]Current Database Contents:[/bold white]")
        console.print(f"  • Enriched Leads: [yellow]{leads_count_before}[/yellow]")
        console.print(f"  • Raw Job Postings: [yellow]{raw_count_before}[/yellow]\n")

        if leads_count_before == 0 and raw_count_before == 0:
            console.print("[bold green]Database tables are already completely empty! No action needed.[/bold green]")
            return

        if not args.force:
            confirmed = Confirm.ask(
                f"[bold red]Are you sure you want to CLEAR all leads and raw jobs in '{sqlite_manager.db_path}'?[/bold red]",
                default=False
            )
            if not confirmed:
                console.print("[yellow]Database clear operation cancelled by user.[/yellow]")
                sys.exit(0)

        console.print("[yellow]Clearing database tables...[/yellow]")
        result = sqlite_manager.clear_database()
        stats_after = sqlite_manager.get_stats()
        leads_count_after = stats_after.get("leads_count", 0)
        raw_count_after = stats_after.get("raw_jobs_count", 0)

        console.print(Panel.fit(
            f"[bold green]Database cleared successfully![/bold green]\n"
            f"[cyan]Deleted Enriched Leads:[/cyan] {result['leads_deleted']}\n"
            f"[cyan]Deleted Raw Job Postings:[/cyan] {result['raw_jobs_deleted']}\n\n"
            f"[bold white]Remaining Records:[/bold white]\n"
            f"  • Enriched Leads: [green]{leads_count_after}[/green]\n"
            f"  • Raw Job Postings: [green]{raw_count_after}[/green]",
            title="[bold green]Operation Summary[/bold green]"
        ))

    except Exception as e:
        console.print(f"[bold red]Error during database clear operation:[/bold red] {e}")
        logger.exception("Database clear failed")
        sys.exit(1)
    finally:
        sqlite_manager.close()


if __name__ == "__main__":
    main()
