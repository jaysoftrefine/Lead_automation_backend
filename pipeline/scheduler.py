"""Daily Autonomous Scraping Scheduler.

Checks every minute for scheduled scraping tasks due today and triggers
the autonomous scraping & enrichment pipeline at the configured daily time
(default 10:00 PM / 22:00, configurable via SCRAPING_SCHEDULE_DAILY_TIME in .env).
"""

import threading
import time
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from config.settings import settings
from core.logging import logger
from db.sqlite import sqlite_manager
from schemas import RunPipelineRequest
from api.pipeline.runner import execute_pipeline_task
from api.pipeline.state import pipeline_state

_scheduler_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_last_daily_run_date: Optional[date] = None


def parse_schedule_time(time_str: str) -> tuple[int, int]:
    """Parse time string like '22:00', '10:00 PM', or '22:00:00' into (hour, minute)."""
    cleaned = (time_str or "22:00").strip().upper()
    try:
        if "PM" in cleaned or "AM" in cleaned:
            dt = datetime.strptime(cleaned, "%I:%M %p")
            return dt.hour, dt.minute
        parts = cleaned.split(":")
        return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.warning(f"Failed to parse SCRAPING_SCHEDULE_DAILY_TIME '{time_str}', falling back to 22:00: {e}")
        return 22, 0


def run_scheduled_job_now(job_id: str) -> Dict[str, Any]:
    """Immediately trigger a scheduled job in a background worker thread."""
    sqlite_manager.connect()
    job = sqlite_manager.get_scheduled_job(job_id)
    if not job:
        raise ValueError(f"Scheduled job '{job_id}' not found")

    if pipeline_state.is_running:
        raise RuntimeError("Autonomous pipeline is already currently running. Please wait or stop the current run.")

    target_goal = int(job.get("scraping_limit") or 10)
    pipeline_state.reset(total=target_goal)
    pipeline_state.add_log(
        f"🚀 Manually triggering scheduled job '{job['job_title']}' (ID: {job_id}, Goal: {target_goal})...",
        "info"
    )

    req = RunPipelineRequest(
        search_term=job["job_title"],
        location=job.get("target_location") or "Worldwide (Remote)",
        company_size=job.get("company_size") or "small",
        limit=target_goal,
        sites=["linkedin"],
        is_remote=True,
        provider=settings.default_llm_provider,
        scheduled_job_id=job_id,
    )

    thread = threading.Thread(target=execute_pipeline_task, args=(req,), daemon=True)
    thread.start()

    return {
        "success": True,
        "message": f"Autonomous pipeline started for job {job_id}.",
        "job": job,
    }


def _execute_due_batch(jobs: List[Dict[str, Any]]) -> None:
    """Execute a batch of due jobs sequentially."""
    for job in jobs:
        job_id = job["id"]
        logger.info(f"⏰ [Daily Scheduler] Launching autonomous engine for scheduled job: {job_id} ('{job['job_title']}')")

        # Wait if another run is active (up to 30 mins)
        wait_seconds = 0
        while pipeline_state.is_running and wait_seconds < 1800:
            time.sleep(10)
            wait_seconds += 10
            if _stop_event.is_set():
                return

        target_goal = int(job.get("scraping_limit") or 10)
        pipeline_state.reset(total=target_goal)
        pipeline_state.add_log(
            f"⏰ [Daily Scheduler] Executing due task: '{job['job_title']}' (ID: {job_id})",
            "info"
        )

        req = RunPipelineRequest(
            search_term=job["job_title"],
            location=job.get("target_location") or "Worldwide (Remote)",
            company_size=job.get("company_size") or "small",
            limit=target_goal,
            sites=["linkedin"],
            is_remote=True,
            provider=settings.default_llm_provider,
            scheduled_job_id=job_id,
        )

        try:
            execute_pipeline_task(req)
        except Exception as e:
            logger.error(f"Error executing scheduled job {job_id}: {e}")
            sqlite_manager.update_scheduled_job_status(job_id, status="failed", error_message=str(e))


def _scheduler_loop() -> None:
    """Background loop that checks every 30 seconds for scheduled daily time."""
    global _last_daily_run_date
    logger.info(f"⏰ Daily Scraping Scheduler thread started. Target time: {settings.scraping_schedule_daily_time} daily.")

    while not _stop_event.is_set():
        try:
            now = datetime.now()
            target_hour, target_minute = parse_schedule_time(settings.scraping_schedule_daily_time)

            # Check if current time matches scheduled time and hasn't run yet today
            if (now.hour == target_hour and now.minute == target_minute) and (_last_daily_run_date != now.date()):
                today_str = now.strftime("%Y-%m-%d")
                logger.info(f"🔔 Target time reached ({target_hour:02d}:{target_minute:02d})! Checking for due jobs on {today_str}...")

                sqlite_manager.connect()
                due_jobs = sqlite_manager.get_due_pending_jobs(today_str)
                if due_jobs:
                    logger.info(f"Found {len(due_jobs)} due scheduled jobs to run today.")
                    _execute_due_batch(due_jobs)
                else:
                    logger.info(f"No pending jobs scheduled for {today_str}.")

                _last_daily_run_date = now.date()

        except Exception as e:
            logger.error(f"Error in scraping scheduler loop: {e}", exc_info=True)

        # Sleep in short intervals so we can wake or stop gracefully
        for _ in range(30):
            if _stop_event.is_set():
                break
            time.sleep(1)


def start_scraping_scheduler() -> None:
    """Start the background scraping scheduler thread if not already running."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, name="ScrapingSchedulerThread", daemon=True)
    _scheduler_thread.start()


def stop_scraping_scheduler() -> None:
    """Signal background scraping scheduler to stop and join."""
    global _scheduler_thread
    _stop_event.set()
    if _scheduler_thread and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=2.0)
    _scheduler_thread = None
