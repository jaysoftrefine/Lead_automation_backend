"""Email Campaigns WebSocket Manager for Real-Time Progress Streaming."""

import asyncio
import threading
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

from core.logging import logger


class CampaignWebSocketManager:
    """Thread-safe connection pool for streaming campaign progress in real-time."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        with self._lock:
            self.loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._lock:
            self.active_connections.append(websocket)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        logger.info(f"📧 WebSocket client connected to campaign progress stream. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"📧 WebSocket client disconnected from campaign progress stream. Remaining: {len(self.active_connections)}")

    def broadcast_campaign_update(self, campaign_data: Dict[str, Any]):
        """Broadcast updated campaign state from background threads or async handlers."""
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

        payload = {
            "type": "campaign_progress",
            "campaign_id": campaign_data.get("id"),
            "data": campaign_data,
        }
        try:
            asyncio.run_coroutine_threadsafe(self._send_all(payload), loop)
        except Exception as e:
            logger.debug(f"Campaign WebSocket broadcast error: {e}")

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


campaign_ws_manager = CampaignWebSocketManager()
