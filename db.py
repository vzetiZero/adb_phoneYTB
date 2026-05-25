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
