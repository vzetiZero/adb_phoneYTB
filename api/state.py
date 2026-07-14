"""Shared application state — avoids circular imports between main.py and routers."""

from __future__ import annotations

import asyncio
import threading
from typing import Optional, Callable


# ---------------------------------------------------------------------------
# WebSocket log hub — all workers push messages here, UI subscribes
# ---------------------------------------------------------------------------
class LogHub:
    def __init__(self):
        self._clients: list = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, ws):
        await ws.accept()
        with self._lock:
            self._clients.append(ws)

    def disconnect(self, ws):
        with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)

    async def broadcast(self, message: str):
        dead: list = []
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            with self._lock:
                for ws in dead:
                    if ws in self._clients:
                        self._clients.remove(ws)

    def sync_log(self, msg: str):
        """Thread-safe log push from sync workers into async broadcast."""
        if not self._loop or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self._loop)
        except Exception:
            pass

    def make_logger(self) -> Callable[[str], None]:
        """Return a sync callable suitable for passing as log_cb."""
        return self.sync_log


log_hub = LogHub()


# ---------------------------------------------------------------------------
# App state — tracks running workflow
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self):
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.is_running = False

    def cancel(self):
        self.stop_event.set()
        self.is_running = False

    def reset(self):
        """Force cancel + wait for old thread to finish."""
        self.stop_event.set()
        self.is_running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=3)
        self.stop_event.clear()


app_state = AppState()
