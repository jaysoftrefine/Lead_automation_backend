"""FastAPI Router for Pipeline operations & WebSocket streaming."""

import asyncio
import threading
import time
from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect

from config.settings import settings
from core.logging import logger
from db.models import EnrichedLead, RawJobPosting
from db.sqlite import sqlite_manager
from enrichment.agent import LeadEnrichmentAgent
from schemas import RunPipelineRequest, TestEnrichmentRequest
from api.pipeline.runner import execute_pipeline_task
from api.pipeline.state import pipeline_state, ws_manager

router = APIRouter()


@router.post("/pipeline/run")
@router.post("/pipeline/start")
def trigger_pipeline(req: RunPipelineRequest, background_tasks: BackgroundTasks):
    """Trigger the scraping and enrichment pipeline asynchronously."""
    # Safety: Auto-clear stale running state if > 3 minutes
    if pipeline_state.is_running and pipeline_state.started_at and (time.time() - pipeline_state.started_at) > 180:
        logger.warning("Auto-clearing stale running pipeline state.")
        pipeline_state.is_running = False

    if pipeline_state.is_running:
        raise HTTPException(status_code=409, detail="A pipeline task is already currently running. Click Stop or wait a moment.")

    target_goal = req.limit or req.results_wanted or 10
    pipeline_state.reset(total=target_goal)
    pipeline_state.add_log(f"🚀 Initializing autonomous pipeline for '{req.search_term}'...", "info")

    thread = threading.Thread(target=execute_pipeline_task, args=(req,), daemon=True)
    thread.start()

    return {
        "success": True,
        "message": "Pipeline started in background.",
        "search_term": req.search_term,
        "limit": target_goal,
    }


@router.websocket("/pipeline/ws")
async def websocket_pipeline_status(websocket: WebSocket):
    """Real-time WebSocket endpoint streaming pipeline status, progress, and terminal logs."""
    await ws_manager.connect(websocket)
    try:
        # Immediately send current state snapshot upon connection
        await websocket.send_json(pipeline_state.to_dict())
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if msg == "ping":
                    await websocket.send_text("pong")
                elif msg in ("status", "get_status"):
                    await websocket.send_json(pipeline_state.to_dict())
            except asyncio.TimeoutError:
                # Keep-alive heartbeat and state sync while running
                if pipeline_state.is_running:
                    await websocket.send_json(pipeline_state.to_dict())
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        ws_manager.disconnect(websocket)


@router.get("/pipeline/status")
def get_pipeline_status():
    """Get the current live status and logs of the pipeline."""
    return pipeline_state.to_dict()


@router.post("/pipeline/stop")
def stop_pipeline():
    """Request pipeline to cancel current run."""
    if not pipeline_state.is_running:
        return {"success": False, "message": "No active pipeline running."}
    pipeline_state._stop_requested = True
    pipeline_state.add_log("Stop request received, winding down...", "warning")
    return {"success": True, "message": "Stop requested."}


@router.post("/pipeline/test-enrichment")
def test_enrichment_direct(req: TestEnrichmentRequest):
    """Directly test the autonomous LLM thinking agent & Tavily research on a single custom job without scraping."""
    try:
        sample_job = RawJobPosting(
            title=req.title,
            company=req.company,
            location=req.location or "Remote",
            job_url=req.job_url or f"https://example.com/jobs/{int(time.time())}",
            site="direct_test",
            description=req.job_description or f"Job opening for {req.title} at {req.company}.",
        )

        agent = LeadEnrichmentAgent(
            provider_name=req.provider or settings.default_llm_provider,
            model_name=req.model,
        )

        lead: EnrichedLead = agent.enrich_job(
            sample_job,
            target_company_size=req.target_company_size or "small",
            target_job_type=req.target_job_type or "all",
        )

        # Save to DB if requested
        if req.save_to_db:
            try:
                sqlite_manager.connect()
                sqlite_manager.upsert_enriched_lead(lead)
            except Exception as db_err:
                logger.warning(f"Could not persist test lead to SQLite: {db_err}")

        lead_dict = lead.model_dump()
        lead_dict["created_at"] = lead.created_at.isoformat()
        lead_dict["updated_at"] = lead.updated_at.isoformat()

        return {
            "success": True,
            "lead": lead_dict,
        }
    except Exception as e:
        logger.exception("Direct test enrichment failed")
        raise HTTPException(status_code=500, detail=str(e))
