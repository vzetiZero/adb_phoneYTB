"""Task execution endpoints — start / cancel Google Login workflows."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import db
from adb_time_sync.adb import ADB
from api.state import app_state, log_hub

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SUCCESS_FILE = PROJECT_ROOT / "account_success.txt"
ERROR_FILE = PROJECT_ROOT / "account_error.txt"


def _write_login_results(results: dict, active_credentials: list, log) -> None:
    """Separate success/failed results, save to DB and write to files."""
    success_list = []
    failed_list = []

    for serial, email, pw in active_credentials:
        r = results.get(serial, {})
        if r.get("success"):
            success_list.append((serial, email, pw))
        else:
            msg = r.get("message", "Unknown error")
            failed_list.append((email, pw, msg))

    # Write success file
    if success_list:
        lines = [f"{email}|{pw}" for _, email, pw in success_list]
        SUCCESS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"[FILE] Saved {len(success_list)} successful accounts to account_success.txt")
    else:
        SUCCESS_FILE.write_text("", encoding="utf-8")

    # Write error file
    if failed_list:
        lines = [f"{email}|{pw}|{msg}" for email, pw, msg in failed_list]
        ERROR_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"[FILE] Saved {len(failed_list)} failed accounts to account_error.txt")
    else:
        ERROR_FILE.write_text("", encoding="utf-8")

    # Summary
    log(f"[SUMMARY] Success: {len(success_list)} | Failed: {len(failed_list)} | Total: {len(active_credentials)}")


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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class GoogleLoginRequest(BaseModel):
    credentials: list[dict]  # [{serial, email, password}, ...]
    workers: int = 4
    per_device: bool = True  # True = each device gets its own credentials


class HomeRequest(BaseModel):
    serials: list[str]


class GoogleLogoutRequest(BaseModel):
    serials: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/status")
async def task_status():
    return {"running": app_state.is_running}


@router.post("/google-login")
async def start_google_login(body: GoogleLoginRequest):
    # Auto-cancel old workflow if running
    if app_state.is_running:
        app_state.reset()

    import main as core

    config = _load_config()
    adb_path = _load_adb_path()
    log = log_hub.make_logger()

    app_state.stop_event.clear()
    app_state.is_running = True

    def runner():
        try:
            adb = ADB(adb_path=adb_path)

            # Auto-connect to known network devices
            custom_port = config.get("adb_server_port")
            if custom_port:
                for d in db.list_devices():
                    ip = d.get("ip", "")
                    if ip and ("." in ip or ":" in ip):
                        adb.cmd("connect", ip)

            if body.per_device:
                # Each credential entry has its own serial
                credentials = [
                    (c["serial"], c["email"], c.get("password", ""))
                    for c in body.credentials
                    if c.get("serial") and c.get("email")
                ]
                if not credentials:
                    log("[ERROR] No devices with credentials provided")
                    return

                log(f"[API] Starting per-device Google Login: {len(credentials)} devices")

                # Check which devices already have the account
                active_credentials = []
                adb_path_val = config.get("adb_path", "adb")

                for serial, email, pw in credentials:
                    if app_state.stop_event.is_set():
                        break
                    already_logged = False
                    try:
                        import subprocess
                        # Method 1: dumpsys account
                        res = subprocess.run(
                            [adb_path_val, "-s", serial, "shell", "dumpsys", "account"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=10,
                        )
                        if email.lower() in (res.stdout or "").lower():
                            already_logged = True
                        # Method 2: content query
                        if not already_logged:
                            res2 = subprocess.run(
                                [adb_path_val, "-s", serial, "shell", "content", "query",
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
                                [adb_path_val, "-s", serial, "shell", "dumpsys", "package",
                                 "com.google.android.gms"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=10,
                            )
                            if email.lower() in (res3.stdout or "").lower():
                                already_logged = True
                    except Exception:
                        pass

                    if already_logged:
                        log(f"[DA DANG NHAP] {serial} da dang nhap {email} -> Bo qua")
                    else:
                        active_credentials.append((serial, email, pw))

                if not active_credentials:
                    log("[OK] Khong co thiet bi nao can chay login (tat ca da dang nhap).")
                    return

                log(f"[API] Running login on {len(active_credentials)} devices (workers={body.workers})")
                results = core.run_google_login_per_device(
                    adb, active_credentials, config, app_state.stop_event, log,
                    workers=body.workers,
                )

                # Collect success/failed and write to files
                _write_login_results(results, active_credentials, log)
            else:
                # Shared credentials for all selected devices
                serials = [c.get("serial") for c in body.credentials if c.get("serial")]
                if not serials:
                    log("[ERROR] No devices selected")
                    return
                creds = [(c["email"], c["password"]) for c in body.credentials if c.get("email")]
                if not creds:
                    log("[ERROR] No credentials provided")
                    return

                log(f"[API] Starting shared Google Login: {len(serials)} devices, {len(creds)} accounts")

                results = core.run_tasks(
                    adb,
                    [],
                    serials,
                    workers=body.workers,
                    stop_event=app_state.stop_event,
                    log_cb=log,
                    google_login_credentials=creds,
                    google_login_config=config,
                )

            log("[XONG] Workflow completed")
        except Exception as e:
            log(f"[ERROR] {e}")
        finally:
            app_state.is_running = False

    app_state.worker_thread = threading.Thread(target=runner, daemon=True)
    app_state.worker_thread.start()

    return {"ok": True, "message": "Workflow started"}


@router.post("/cancel")
async def cancel_workflow():
    if not app_state.is_running:
        return {"ok": True, "message": "Nothing running"}
    app_state.cancel()
    return {"ok": True, "message": "Cancel requested"}


@router.post("/home")
async def go_home(body: HomeRequest):
    """Force-stop apps and press Home on selected devices."""
    adb = ADB(adb_path=_load_adb_path())
    log = log_hub.make_logger()

    # Auto-connect
    config = _load_config()
    custom_port = config.get("adb_server_port")
    if custom_port:
        for d in db.list_devices():
            ip = d.get("ip", "")
            if ip and ("." in ip or ":" in ip):
                adb.cmd("connect", ip)

    results = []
    for serial in body.serials:
        try:
            adb.shell(serial, "am force-stop com.google.android.youtube")
            adb.shell(serial, "am force-stop com.android.chrome")
            adb.shell(serial, "am kill-all")
            adb.shell(serial, "input keyevent KEYCODE_HOME")
            log(f"[HOME] {serial} -> da ve Home")
            results.append({"serial": serial, "ok": True})
        except Exception as e:
            log(f"[HOME] {serial} LOI: {e}")
            results.append({"serial": serial, "ok": False, "error": str(e)})

    return {"results": results}


@router.post("/google-logout")
async def google_logout(body: GoogleLogoutRequest):
    """Remove ALL Google accounts from selected Samsung devices."""
    # Auto-cancel old workflow if running
    if app_state.is_running:
        app_state.reset()

    from adb_time_sync.google_login import GoogleLoginAutomation

    config = _load_config()
    adb_path = _load_adb_path()
    log = log_hub.make_logger()

    app_state.stop_event.clear()
    app_state.is_running = True

    def runner():
        try:
            adb = ADB(adb_path=adb_path)

            # Auto-connect to known network devices
            custom_port = config.get("adb_server_port")
            if custom_port:
                for d in db.list_devices():
                    ip = d.get("ip", "")
                    if ip and ("." in ip or ":" in ip):
                        adb.cmd("connect", ip)

            log(f"[LOGOUT] Starting ALL Google account removal on {len(body.serials)} devices")

            for serial in body.serials:
                if app_state.stop_event.is_set():
                    log("[LOGOUT] Cancelled by user")
                    break

                log(f"[LOGOUT] Processing {serial}...")
                try:
                    automation = GoogleLoginAutomation(
                        serial=serial,
                        config=config,
                        logger=type("Logger", (), {
                            "info": lambda self, msg: log(msg),
                            "warning": lambda self, msg: log(f"[WARN] {msg}"),
                            "debug": lambda self, msg: None,
                            "error": lambda self, msg: log(f"[ERROR] {msg}"),
                        })(),
                        stop_event=app_state.stop_event,
                    )
                    result = automation.logout_all_google_accounts()
                    removed = result.get("removed", [])
                    failed = result.get("failed", [])
                    if removed:
                        log(f"[LOGOUT] {serial}: Removed {len(removed)} accounts: {', '.join(removed)}")
                        # Clear email/password from device in DB
                        db.update_device_account(serial, None, None)
                        log(f"[LOGOUT] {serial}: Cleared account from Devices tab")
                    if failed:
                        log(f"[LOGOUT] {serial}: Failed {len(failed)} accounts")
                    if not removed and not failed:
                        log(f"[LOGOUT] {serial}: {result.get('message')}")
                except Exception as e:
                    log(f"[LOGOUT] {serial} ERROR: {e}")

            log("[LOGOUT] All done")
        except Exception as e:
            log(f"[ERROR] Logout workflow failed: {e}")
        finally:
            app_state.is_running = False

    app_state.worker_thread = threading.Thread(target=runner, daemon=True)
    app_state.worker_thread.start()

    return {"ok": True, "message": "Logout workflow started"}


# ---------------------------------------------------------------------------
# Login result files
# ---------------------------------------------------------------------------

@router.get("/login-results")
async def get_login_results():
    """Return success and failed accounts from last login run."""
    success = []
    error = []

    if SUCCESS_FILE.exists():
        for line in SUCCESS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                success.append({"email": parts[0], "password": parts[1]})

    if ERROR_FILE.exists():
        for line in ERROR_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                error.append({"email": parts[0], "password": parts[1], "error": parts[2] if len(parts) > 2 else ""})

    return {"success": success, "error": error}


@router.get("/export-success")
async def export_success():
    """Download account_success.txt"""
    if not SUCCESS_FILE.exists():
        return PlainTextResponse("", media_type="text/plain",
                                headers={"Content-Disposition": "attachment; filename=account_success.txt"})
    content = SUCCESS_FILE.read_text(encoding="utf-8")
    return PlainTextResponse(content, media_type="text/plain",
                            headers={"Content-Disposition": "attachment; filename=account_success.txt"})


@router.get("/export-error")
async def export_error():
    """Download account_error.txt"""
    if not ERROR_FILE.exists():
        return PlainTextResponse("", media_type="text/plain",
                                headers={"Content-Disposition": "attachment; filename=account_error.txt"})
    content = ERROR_FILE.read_text(encoding="utf-8")
    return PlainTextResponse(content, media_type="text/plain",
                            headers={"Content-Disposition": "attachment; filename=account_error.txt"})
