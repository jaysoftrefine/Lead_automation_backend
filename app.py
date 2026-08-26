"""Main FastAPI Web Application & Server for Autonomous Lead Generation Engine."""

import os
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as api_router
from config.settings import settings
from core.logging import logger
from db.mongo import mongo_manager

# Base directory
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Create directories if needed
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

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

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include API routes
app.include_router(api_router)


@app.on_event("startup")
async def startup_event():
    """Attempt initial MongoDB connection on startup."""
    try:
        mongo_manager.connect()
        logger.info("FastAPI Web Server started & MongoDB initialized.")
    except Exception as e:
        logger.warning(f"Startup MongoDB connection warning (will retry on demand): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up MongoDB connection on shutdown."""
    mongo_manager.close()
    logger.info("FastAPI Web Server shut down.")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve the single-page frontend application dashboard."""
    index_path = TEMPLATES_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Lead Generation Engine</h1><p>Frontend template not found.</p>")


if __name__ == "__main__":
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting server on http://localhost:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=True)
