"""Production-grade structured logging configuration with live terminal streaming."""

import sys
import time
import logging
import asyncio
import threading
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from fastapi import WebSocket

from config.settings import settings


class LogStreamManager:
    """Thread-safe circular log buffer & WebSocket broadcasting manager for real-time frontend streaming."""

    def __init__(self, maxlen: int = 2000):
        self.buffer: deque = deque(maxlen=maxlen)
        self.listeners: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.RLock()

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        with self._lock:
            self.loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        with self._lock:
            self.listeners.add(websocket)
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def disconnect(self, websocket: WebSocket):
        with self._lock:
            self.listeners.discard(websocket)

    def broadcast(self, entry: Dict[str, Any]):
        with self._lock:
            self.buffer.append(entry)
            if not self.listeners:
                return
            listeners = list(self.listeners)
            loop = self.loop
            if not loop or loop.is_closed():
                try:
                    loop = asyncio.get_running_loop()
                    self.loop = loop
                except RuntimeError:
                    return

        try:
            asyncio.run_coroutine_threadsafe(self._send_to_all(listeners, entry), loop)
        except Exception:
            pass

    async def _send_to_all(self, listeners: List[WebSocket], data: Dict[str, Any]):
        dead = []
        for ws in listeners:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        if dead:
            with self._lock:
                for d in dead:
                    self.listeners.discard(d)

    def get_recent(
        self,
        limit: int = 200,
        level: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            logs = list(self.buffer)

        if level and level.upper() != "ALL":
            lvl = level.upper()
            logs = [l for l in logs if l.get("level", "").upper() == lvl]

        if search:
            s = search.lower()
            logs = [
                l for l in logs
                if s in l.get("message", "").lower() or s in l.get("module", "").lower()
            ]

        if limit and limit > 0:
            logs = logs[-limit:]

        return logs

    def clear(self):
        with self._lock:
            self.buffer.clear()


log_stream_manager = LogStreamManager(maxlen=2000)


def _loguru_stream_sink(message):
    """Custom Loguru sink to feed real-time structured logs into LogStreamManager."""
    try:
        record = message.record
        t: datetime = record["time"]
        level_name = record["level"].name
        raw_msg = str(record["message"])

        entry = {
            "id": int(time.time() * 1000000),
            "timestamp": t.strftime("%Y-%m-%d %H:%M:%S"),
            "time_short": t.strftime("%H:%M:%S"),
            "level": level_name,
            "module": record["name"] or "root",
            "function": record["function"] or "",
            "line": record["line"] or 0,
            "message": raw_msg,
            "raw": f"{t.strftime('%Y-%m-%d %H:%M:%S')} | {level_name:<8} | {record['name']}:{record['function']}:{record['line']} - {raw_msg}",
        }
        log_stream_manager.broadcast(entry)
    except Exception:
        pass


class InterceptHandler(logging.Handler):
    """Intercept standard Python logging and redirect to Loguru."""

    def emit(self, record: logging.LogRecord):
        try:
            level = logger.level(record.levelname).name
        except Exception:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and (depth == 2 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


try:
    from loguru import logger as _logger

    # Remove default loguru handler
    _logger.remove()

    # 1. Add stdout handler with rich formatting
    _logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.log_level.upper(),
    )

    # 2. Add file handler for error logging
    _logger.add(
        "logs/lead_gen_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    # 3. Add live streaming sink for frontend terminal
    _logger.add(
        _loguru_stream_sink,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )

    logger = _logger

    # Intercept standard library logging (uvicorn, httpx, etc.)
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for _logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        _l = logging.getLogger(_logger_name)
        _l.handlers = [InterceptHandler()]
        _l.propagate = False

except ImportError:
    # Standard library fallback
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("lead_gen")
