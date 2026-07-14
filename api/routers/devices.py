"""Device CRUD endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import db
from adb_time_sync.adb import ADB

router = APIRouter()


def _load_config() -> dict:
    try:
        return json.loads(
            Path(__file__).resolve().parent.parent.parent.joinpath("config.json").read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {}


def _load_adb_path() -> str:
    return _load_config().get("adb_path", "adb")


def _adb_connect_known(adb: ADB):
    """Auto-connect to known network devices on custom port."""
    config = _load_config()
    custom_port = config.get("adb_server_port")
    if custom_port:
        for d in db.list_devices():
            ip = d.get("ip", "")
            if ip and ("." in ip or ":" in ip):
                try:
                    adb.cmd("connect", ip)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None


class AccountImport(BaseModel):
    accounts: list[dict]  # [{email, password}, ...] assigned by order


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("")
async def list_devices():
    return db.list_devices()


@router.get("/status")
async def check_adb_status():
    """Check ADB connection and return device online/offline status."""
    adb = ADB(adb_path=_load_adb_path())
    _adb_connect_known(adb)

    try:
        adb_devices = adb.devices_all()
    except Exception as e:
        return {"adb_ok": False, "error": str(e), "devices": []}

    # Build status map: serial -> state
    status_map = {serial: state for serial, state in adb_devices}

    # Get devices from DB and merge with ADB status
    db_devices = db.list_devices()
    result = []
    for d in db_devices:
        ip = d.get("ip", "")
        adb_state = status_map.get(ip, "offline")
        is_online = adb_state == "device"
        result.append({
            **d,
            "adb_state": adb_state,
            "online": is_online,
        })

    # Also include devices found in ADB but not in DB
    db_ips = {d.get("ip") for d in db_devices}
    for serial, state in adb_devices:
        if serial not in db_ips:
            result.append({
                "ip": serial,
                "name": None,
                "email": None,
                "password": None,
                "adb_state": state,
                "online": state == "device",
            })

    return {"adb_ok": True, "devices": result}


@router.get("/{ip}")
async def get_device(ip: str):
    d = db.get_device(ip)
    if not d:
        raise HTTPException(404, "Device not found")
    return d


@router.post("/import")
async def import_from_adb():
    """Scan ADB and upsert all connected devices."""
    adb = ADB(adb_path=_load_adb_path())
    _adb_connect_known(adb)

    try:
        devices = adb.devices_all()
    except Exception as e:
        raise HTTPException(500, f"ADB error: {e}")

    imported = 0
    for serial, state in devices:
        db.upsert_device(serial)
        imported += 1

    return {"imported": imported, "devices": db.list_devices()}


@router.put("/{ip}")
async def update_device(ip: str, body: DeviceUpdate):
    d = db.get_device(ip)
    if not d:
        raise HTTPException(404, "Device not found")

    if body.name is not None:
        db.update_device_name(ip, body.name)
    if body.email is not None or body.password is not None:
        email = body.email if body.email is not None else d.get("email")
        password = body.password if body.password is not None else d.get("password")
        db.update_device_account(ip, email, password)

    return db.get_device(ip)


@router.delete("/{ip}")
async def delete_device(ip: str):
    db.delete_device(ip)
    return {"ok": True}


@router.post("/auto-number")
async def auto_number():
    devices = sorted(db.list_devices(), key=lambda d: str(d.get("ip") or ""))
    for i, d in enumerate(devices, start=1):
        ip = d.get("ip")
        if ip:
            db.update_device_name(ip, f"{i:02d}")
    return {"ok": True, "devices": db.list_devices()}


@router.post("/import-accounts")
async def import_accounts(body: AccountImport):
    """Import accounts and assign to devices by order."""
    devices = db.list_devices()
    if not devices:
        raise HTTPException(400, "No devices. Import from ADB first.")

    count = 0
    for i, acc in enumerate(body.accounts):
        if i >= len(devices):
            break
        ip = devices[i].get("ip")
        if ip:
            db.update_device_account(ip, acc.get("email"), acc.get("password"))
            count += 1

    return {"assigned": count}


@router.get("/{ip}/check-account")
async def check_account_on_device(ip: str):
    """Check if a Google account is already logged in on this device."""
    d = db.get_device(ip)
    if not d:
        raise HTTPException(404, "Device not found")

    email = d.get("email")
    if not email:
        return {"has_account": False, "email": None}

    adb = ADB(adb_path=_load_adb_path())
    adb_path = _load_adb_path()
    already_logged = False

    try:
        import subprocess
        # Method 1: dumpsys account
        res = subprocess.run(
            [adb_path, "-s", ip, "shell", "dumpsys", "account"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
        if email.lower() in (res.stdout or "").lower():
            already_logged = True
        # Method 2: content query
        if not already_logged:
            res2 = subprocess.run(
                [adb_path, "-s", ip, "shell", "content", "query",
                 "--uri", "content://com.android.accounts/authenticator",
                 "--projection", "name"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
            )
            if email.lower() in (res2.stdout or "").lower():
                already_logged = True
        # Method 3: dumpsys package google
        if not already_logged:
            res3 = subprocess.run(
                [adb_path, "-s", ip, "shell", "dumpsys", "package",
                 "com.google.android.gms"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
            )
            if email.lower() in (res3.stdout or "").lower():
                already_logged = True
    except Exception:
        pass

    return {"has_account": already_logged, "email": email}


# ---------------------------------------------------------------------------
# Database management
# ---------------------------------------------------------------------------

@router.post("/reset-db")
async def reset_database():
    """Delete all data from all tables."""
    db.reset_db()
    return {"ok": True, "message": "Database reset complete"}


@router.get("/export-db")
async def export_database():
    """Export all tables as JSON (downloadable file)."""
    data = db.export_db()
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": "attachment; filename=app_backup.json"},
    )


@router.post("/import-db")
async def import_database(body: dict):
    """Restore database from exported JSON (clears existing data)."""
    if not body.get("devices") and not body.get("task_runs"):
        raise HTTPException(400, "Invalid backup data")
    db.import_db(body)
    return {"ok": True, "message": "Database restored"}
