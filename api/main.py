"""FastAPI backend for BoxPhone Automation — serves REST + WebSocket + static frontend."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Ensure project root is on sys.path so we can import db, main, adb_time_sync
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load ADB server port from config before importing adb modules
try:
    config_data = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
    custom_port = config_data.get("adb_server_port")
    if custom_port:
        os.environ["ANDROID_ADB_SERVER_PORT"] = str(custom_port)
except Exception:
    pass

import db
from api.state import log_hub, app_state

# Path to Vite static export
DIST_DIR = PROJECT_ROOT / "ui" / "dist"


def _load_adb_path() -> str:
    try:
        cfg = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
        return cfg.get("adb_path", "adb")
    except Exception:
        return "adb"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log_hub.set_loop(asyncio.get_event_loop())
    yield


app = FastAPI(title="BoxPhone API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Import routers
# ---------------------------------------------------------------------------
from api.routers import devices, tasks, history  # noqa: E402

app.include_router(devices.router, prefix="/api/devices", tags=["devices"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(history.router, prefix="/api/history", tags=["history"])


# ---------------------------------------------------------------------------
# WebSocket endpoint for real-time logs
# ---------------------------------------------------------------------------
@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await log_hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        log_hub.disconnect(websocket)


# ---------------------------------------------------------------------------
# Config & Status endpoints
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config():
    try:
        cfg = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
        return cfg
    except Exception:
        return {}


@app.put("/api/config")
async def update_config(body: dict):
    config_path = PROJECT_ROOT / "config.json"
    try:
        current = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    current.update(body)
    config_path.write_text(json.dumps(current, indent=4, ensure_ascii=False), encoding="utf-8")

    # Apply adb_server_port immediately so it takes effect without restart
    new_port = body.get("adb_server_port")
    if new_port:
        os.environ["ANDROID_ADB_SERVER_PORT"] = str(new_port)

    return {"ok": True, "message": "Config saved"}


@app.get("/api/status")
async def get_status():
    return {
        "running": app_state.is_running,
        "adb_path": _load_adb_path(),
    }


# ---------------------------------------------------------------------------
# Update check & apply
# ---------------------------------------------------------------------------
import subprocess as _sp

def _git(*args):
    return _sp.run(
        ["git"] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(PROJECT_ROOT), timeout=30,
    )

@app.get("/api/update/check")
async def check_update():
    """Check if there are new commits on remote."""
    try:
        _git("fetch", "origin")
        local = _git("rev-parse", "HEAD").stdout.strip()
        remote = _git("rev-parse", "origin/main").stdout.strip()
        if local != remote:
            ahead = _git("log", "--oneline", f"{local}..{remote}").stdout.strip().splitlines()
            return {"has_update": True, "commits": len(ahead), "message": f"{len(ahead)} commit moi"}
        return {"has_update": False, "commits": 0}
    except Exception as e:
        return {"has_update": False, "commits": 0, "error": str(e)}


@app.post("/api/update/apply")
async def apply_update():
    """Git pull + rebuild UI."""
    import threading

    def _do_update():
        try:
            _git("pull", "origin", "main")
            ui_dir = PROJECT_ROOT / "ui"
            _sp.run(["npm", "install"], cwd=str(ui_dir), capture_output=True, timeout=120)
            _sp.run(["npm", "run", "build"], cwd=str(ui_dir), capture_output=True, timeout=120)
        except Exception:
            pass

    threading.Thread(target=_do_update, daemon=True).start()
    return {"ok": True, "message": "Updating... app will refresh in 5 seconds"}


# ---------------------------------------------------------------------------
# Serve static frontend (Vite React SPA)
# ---------------------------------------------------------------------------
if DIST_DIR.is_dir():
    # Mount static assets (JS, CSS, images) from dist/assets
    assets_dir = DIST_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve React SPA — all non-API routes return index.html."""
        # Try to serve exact file first (favicon, etc.)
        file_path = DIST_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(str(file_path))
        # Fallback to index.html for SPA routing
        index_file = DIST_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return {"error": "UI not built. Run: cd ui && npm run build"}
