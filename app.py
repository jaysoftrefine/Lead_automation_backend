"""Main FastAPI Web Application & Server for Autonomous Lead Generation Engine."""

import os
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from api.eu_startups_routes import router as eu_startups_router
from api.email_routes import router as email_router
from config.settings import settings
from core.logging import logger
from db.sqlite import sqlite_manager
from eu_startups.db import create_database as create_eu_database
from email_campaigns.db import init_email_tables

# Base directory
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOADS_DIR = BASE_DIR / "uploads"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# Create directories if needed
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Autonomous B2B Lead Generation Engine",
    description="Intelligent Job Scraping, Autonomous LLM Research Agent, and Contact Discovery Dashboard",
    version="1.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files & uploads
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

# Include API routes
app.include_router(api_router)
app.include_router(eu_startups_router)
app.include_router(email_router)


@app.on_event("startup")
async def startup_event():
    """Verify and initialize all database schemas on startup without fail."""
    logger.info("Initializing and verifying database schemas...")
    try:
        # 1. Initialize core lead tables (enriched_leads, raw_jobs, job_leads)
        sqlite_manager.connect()

        # 2. Initialize EU startups tables (startups, people, contacts, crawl_status)
        create_eu_database()

        # 3. Initialize Email campaigns tables (templates, campaigns, logs, audiences, smtp)
        init_email_tables()

        # 4. Perform comprehensive integrity and schema verification
        health = sqlite_manager.verify_database_health()
        logger.info(
            f"✅ Database startup check PASSED! Path: {health['db_path']}, "
            f"Tables ({health['table_count']}): {list(health['tables'].keys())}"
        )
    except Exception as e:
        logger.critical(f"❌ DATABASE STARTUP CHECK FAILED: {e}", exc_info=True)
        raise RuntimeError(f"Database startup check failed: {e}") from e


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up SQLite connection on shutdown."""
    sqlite_manager.close()
    logger.info("FastAPI Web Server shut down.")


@app.get("/api/health")
async def health_check():
    """Health check endpoint verifying database readiness and tables."""
    try:
        health = sqlite_manager.verify_database_health()
        return {
            "status": "healthy",
            "database": health
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "message": str(e)}
        )


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve the single-page frontend application dashboard (React or Static)."""
    react_index = FRONTEND_DIST / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index))
    index_path = TEMPLATES_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Lead Generation Engine</h1><p>Frontend template not found.</p>")


@app.get("/eu-startups", response_class=HTMLResponse)
@app.get("/startups", response_class=HTMLResponse)
@app.get("/campaigns", response_class=HTMLResponse)
@app.get("/pipeline", response_class=HTMLResponse)
@app.get("/leads", response_class=HTMLResponse)
async def serve_spa_routes(request: Request):
    """Direct SPA route handler."""
    react_index = FRONTEND_DIST / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index))
    index_path = TEMPLATES_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>LeadPulse AI</h1><p>Template not found.</p>")


if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting server on http://localhost:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=True)
