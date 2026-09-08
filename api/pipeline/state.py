"""Pipeline State Management & WebSocket Broadcasting for Real-Time Streaming."""

import asyncio
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

from core.logging import logger
from pipeline.orchestrator import PipelineMetrics


class WebSocketManager:
    """Thread-safe connection pool for pipeline real-time status streaming."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._lock:
            self.active_connections.append(websocket)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def disconnect(self, websocket: WebSocket):
        with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    def broadcast(self, data: Dict[str, Any]):
        with self._lock:
            if not self.active_connections:
                return
            loop = self.loop
            if not loop or loop.is_closed():
                try:
                    loop = asyncio.get_running_loop()
                    self.loop = loop
                except RuntimeError:
                    return
        try:
            asyncio.run_coroutine_threadsafe(self._send_all(data), loop)
        except Exception as e:
            logger.debug(f"WebSocket broadcast error: {e}")

    async def _send_all(self, data: Dict[str, Any]):
        with self._lock:
            conns = list(self.active_connections)
        dead = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        if dead:
            with self._lock:
                for d in dead:
                    if d in self.active_connections:
                        self.active_connections.remove(d)


ws_manager = WebSocketManager()


class PipelineState:
    """Thread-safe pipeline execution state tracker."""

    def __init__(self):
        self.is_running: bool = False
        self.status: str = "idle"  # idle, scraping, enriching, completed, error
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.current_job_title: Optional[str] = None
        self.current_company: Optional[str] = None
        self.processed_count: int = 0
        self.total_count: int = 0
        self.logs: List[Dict[str, Any]] = []
        self.metrics: Optional[Dict[str, Any]] = None
        self.error_message: Optional[str] = None
        self._stop_requested: bool = False
        self._lock = threading.RLock()

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "is_running": self.is_running,
                "status": self.status,
                "processed_count": self.processed_count,
                "total_count": self.total_count,
                "current_job_title": self.current_job_title,
                "current_company": self.current_company,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "logs": list(self.logs),
                "metrics": self.metrics,
                "error_message": self.error_message,
            }

    def notify(self):
        ws_manager.broadcast(self.to_dict())

    def add_log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.logs.append({"time": timestamp, "message": message, "level": level})
            if len(self.logs) > 200:
                self.logs.pop(0)
        self.notify()

    def reset(self, total: int = 0):
        with self._lock:
            self.is_running = True
            self.status = "initializing"
            self.started_at = time.time()
            self.finished_at = None
            self.current_job_title = None
            self.current_company = None
            self.processed_count = 0
            self.total_count = total
            self.logs = []
            self.metrics = None
            self.error_message = None
            self._stop_requested = False
        self.notify()

    def finish(self, metrics: Optional[PipelineMetrics] = None, error: Optional[str] = None):
        with self._lock:
            self.is_running = False
            self.finished_at = time.time()
            if error:
                self.status = "error"
                self.error_message = error
                self.logs.append({"time": datetime.now().strftime("%H:%M:%S"), "message": f"Pipeline error: {error}", "level": "error"})
            else:
                self.status = "completed"
                self.logs.append({"time": datetime.now().strftime("%H:%M:%S"), "message": "Pipeline completed successfully!", "level": "success"})
            if metrics:
                self.metrics = {
                    "search_term": metrics.search_term,
                    "location": metrics.location,
                    "sites": metrics.sites,
                    "target_company_size": getattr(metrics, "target_company_size", "small"),
                    "total_scraped": metrics.total_scraped,
                    "already_existing": metrics.already_existing,
                    "processed_by_agent": metrics.processed_by_agent,
                    "saved_to_db": metrics.saved_to_db,
                    "rejected_by_llm": metrics.rejected_by_llm,
                    "total_contacts_discovered": metrics.total_contacts_discovered,
                    "duration_seconds": metrics.duration_seconds,
                }
        self.notify()


pipeline_state = PipelineState()
