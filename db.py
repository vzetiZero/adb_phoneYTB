"""SQLite store for device aliases and task run history.

Simpler than the legacy GoLike schema — we only track:
  - devices (serial → human-friendly name + last seen)
  - task_runs (per-task per-device outcome for stats)
  - coords_cache (per-resolution UI coord cache, optional)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path("app.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                ip TEXT PRIMARY KEY,
                name TEXT,
                email TEXT,
                password TEXT,
                width INTEGER,
                height INTEGER,
                last_seen TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT CURRENT_TIMESTAMP,
                serial TEXT,
                app TEXT,
                keyword TEXT,
                requested_loops INTEGER,
                done_loops INTEGER,
                status TEXT,
                note TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coords_cache (
                width INTEGER,
                height INTEGER,
                role TEXT,
                x INTEGER,
                y INTEGER,
                PRIMARY KEY (width, height, role)
            )
            """
        )
        # Migrate: add email/password columns if missing (for existing DB)
        for col, typ in [("email", "TEXT"), ("password", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {typ}")
            except Exception:
                pass  # column already exists
        conn.commit()


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def list_devices() -> list[dict[str, Any]]:
    with _connect() as conn:
        return _rows(conn.execute("SELECT * FROM devices ORDER BY name, ip"))


def get_device(ip: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE ip = ?", (ip,)).fetchone()
        return dict(row) if row else None


def upsert_device(ip: str, name: Optional[str] = None) -> None:
    with _connect() as conn:
        row = conn.execute("SELECT ip FROM devices WHERE ip = ?", (ip,)).fetchone()
        if row:
            if name is not None:
                conn.execute("UPDATE devices SET name = ? WHERE ip = ?", (name, ip))
        else:
            conn.execute("INSERT INTO devices (ip, name) VALUES (?, ?)", (ip, name))
        conn.commit()


def update_device_name(ip: str, name: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO devices (ip, name) VALUES (?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET name = excluded.name",
            (ip, name),
        )
        conn.commit()


def update_device_account(ip: str, email: Optional[str], password: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO devices (ip, email, password) VALUES (?, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET email = excluded.email, password = excluded.password",
            (ip, email, password),
        )
        conn.commit()


def touch_device(ip: str, width: Optional[int] = None, height: Optional[int] = None) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        row = conn.execute("SELECT ip FROM devices WHERE ip = ?", (ip,)).fetchone()
        if row:
            conn.execute(
                "UPDATE devices SET last_seen = ?, "
                "width = COALESCE(?, width), height = COALESCE(?, height) WHERE ip = ?",
                (now, width, height, ip),
            )
        else:
            conn.execute(
                "INSERT INTO devices (ip, name, width, height, last_seen) VALUES (?, ?, ?, ?, ?)",
                (ip, None, width, height, now),
            )
        conn.commit()


def delete_device(ip: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM devices WHERE ip = ?", (ip,))
        conn.commit()


def log_task_run(
    serial: str,
    app: str,
    keyword: str,
    requested_loops: int,
    done_loops: int,
    status: str,
    note: str = "",
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO task_runs (serial, app, keyword, requested_loops, done_loops, status, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (serial, app, keyword, requested_loops, done_loops, status, note),
        )
        conn.commit()


def recent_runs(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM task_runs ORDER BY id DESC LIMIT ?", (limit,))
        return _rows(cur)


def get_coord(width: int, height: int, role: str) -> Optional[tuple[int, int]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT x, y FROM coords_cache WHERE width = ? AND height = ? AND role = ?",
            (width, height, role),
        ).fetchone()
        return (int(row["x"]), int(row["y"])) if row else None


def set_coord(width: int, height: int, role: str, x: int, y: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO coords_cache (width, height, role, x, y) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(width, height, role) DO UPDATE SET x = excluded.x, y = excluded.y",
            (width, height, role, x, y),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Database management — reset / export / import
# ---------------------------------------------------------------------------

def reset_db() -> None:
    """Delete all data from all tables."""
    with _connect() as conn:
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM task_runs")
        conn.execute("DELETE FROM coords_cache")
        conn.commit()


def export_db() -> dict[str, Any]:
    """Export all tables as a JSON-serialisable dict."""
    with _connect() as conn:
        devices = _rows(conn.execute("SELECT * FROM devices"))
        task_runs = _rows(conn.execute("SELECT * FROM task_runs"))
        coords = _rows(conn.execute("SELECT * FROM coords_cache"))
    return {"devices": devices, "task_runs": task_runs, "coords_cache": coords}


def import_db(data: dict[str, Any]) -> None:
    """Clear all tables and restore from exported data."""
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM task_runs")
        conn.execute("DELETE FROM coords_cache")

        for d in data.get("devices", []):
            conn.execute(
                "INSERT OR REPLACE INTO devices (ip, name, email, password, width, height, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (d.get("ip"), d.get("name"), d.get("email"), d.get("password"),
                 d.get("width"), d.get("height"), d.get("last_seen")),
            )
        for t in data.get("task_runs", []):
            conn.execute(
                "INSERT OR REPLACE INTO task_runs (id, ts, serial, app, keyword, requested_loops, done_loops, status, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t.get("id"), t.get("ts"), t.get("serial"), t.get("app"),
                 t.get("keyword"), t.get("requested_loops"), t.get("done_loops"),
                 t.get("status"), t.get("note")),
            )
        for c in data.get("coords_cache", []):
            conn.execute(
                "INSERT OR REPLACE INTO coords_cache (width, height, role, x, y) VALUES (?, ?, ?, ?, ?)",
                (c.get("width"), c.get("height"), c.get("role"), c.get("x"), c.get("y")),
            )
        conn.commit()
