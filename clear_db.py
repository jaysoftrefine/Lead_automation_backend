#!/usr/bin/env python3
"""
Database Clearing Script for Lead Pulse AI.
Clears all enriched leads and raw job postings from the MongoDB database.

Usage:
    python clear_db.py            # Prompt for confirmation before clearing
    python clear_db.py --force    # Clear without confirmation prompt
    python clear_db.py --drop     # Drop the entire database completely
"""

import sys
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from config.settings import settings
from db.mongo import mongo_manager
from core.logging import logger

console = Console()


def main():
    parser = argparse.ArgumentParser(description="Clear all data from the MongoDB database.")
    parser.add_argument(
        "-f", "--force", action="store_true", help="Bypass confirmation prompt and clear immediately"
    )
    parser.add_argument(
        "--drop", action="store_true", help="Drop the entire database instead of deleting collection documents"
    )
    args = parser.parse_args()

    console.print(Panel.fit(
        f"[bold cyan]Lead Pulse AI — Database Reset Tool[/bold cyan]\n"
        f"[green]Target Database:[/green] {settings.mongodb_db_name}\n"
        f"[green]Collection 1:[/green] {settings.mongodb_collection_name} (Enriched Leads)\n"
        f"[green]Collection 2:[/green] {settings.mongodb_raw_collection_name} (Raw Jobs)",
        title="[bold yellow]MongoDB Status Check[/bold yellow]"
    ))

    try:
        mongo_manager.connect()
        leads_count_before = mongo_manager.leads_collection.count_documents({})
        raw_count_before = mongo_manager.raw_jobs_collection.count_documents({})

        console.print(f"[bold white]Current Database Contents:[/bold white]")
        console.print(f"  • Enriched Leads: [yellow]{leads_count_before}[/yellow]")
        console.print(f"  • Raw Job Postings: [yellow]{raw_count_before}[/yellow]\n")

        if leads_count_before == 0 and raw_count_before == 0:
            console.print("[bold green]Database is already completely empty! No action needed.[/bold green]")
            return

        if not args.force:
            action_desc = "DROP the whole database" if args.drop else "CLEAR all documents in database"
            confirmed = Confirm.ask(
                f"[bold red]Are you sure you want to {action_desc} ('{settings.mongodb_db_name}')?[/bold red]",
                default=False
            )
            if not confirmed:
                console.print("[yellow]Database clear operation cancelled by user.[/yellow]")
                sys.exit(0)

        console.print("[yellow]Clearing database...[/yellow]")
        if args.drop:
            mongo_manager.drop_database()
            console.print(Panel.fit(
                f"[bold green]Database '{settings.mongodb_db_name}' dropped successfully![/bold green]",
                title="[bold green]Success[/bold green]"
            ))
        else:
            result = mongo_manager.clear_database()
            leads_count_after = mongo_manager.leads_collection.count_documents({})
            raw_count_after = mongo_manager.raw_jobs_collection.count_documents({})

            console.print(Panel.fit(
                f"[bold green]Database cleared successfully![/bold green]\n"
                f"[cyan]Deleted Enriched Leads:[/cyan] {result['leads_deleted']}\n"
                f"[cyan]Deleted Raw Job Postings:[/cyan] {result['raw_jobs_deleted']}\n\n"
                f"[bold white]Remaining Documents:[/bold white]\n"
                f"  • Enriched Leads: [green]{leads_count_after}[/green]\n"
                f"  • Raw Job Postings: [green]{raw_count_after}[/green]",
                title="[bold green]Operation Summary[/bold green]"
            ))

    except Exception as e:
        console.print(f"[bold red]Error during database clear operation:[/bold red] {e}")
        logger.exception("Database clear failed")
        sys.exit(1)
    finally:
        mongo_manager.close()


if __name__ == "__main__":
    main()
