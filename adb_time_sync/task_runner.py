"""Task config parser and per-device runner.

tasks.txt format (one task per line, '#' for comments):
    youtube|baomoi|5
    chrome|tin nóng hôm nay|3
    # optional opts: key=value joined by ','  (watch min/max seconds, reels min/max)
    youtube|nhạc trẻ 2026|10|watch_min=60,watch_max=240,reels_min=3,reels_max=8

If you provide a single keyword in the GUI (e.g. "baomoi|5"),
the GUI prepends the app name from the dropdown before parsing.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .adb import ADB
from .chrome_flow import CHROME_PKG, run_chrome_task
from .human import home, interruptible_sleep, lognormal_sleep, wait_for_foreground
from .screen import apply_screen_config, try_disable_orientation_requests
from .wake import wake_device
from .youtube_flow import YOUTUBE_PKG, run_youtube_task


SUPPORTED_APPS = ("youtube", "chrome")

# Map task.app -> Android package needed.
_APP_PKG = {
    "youtube": YOUTUBE_PKG,
    "chrome": CHROME_PKG,
}


def _preflight(
    adb: ADB,
    serial: str,
    needed_pkgs: set[str],
    log_cb: Optional[Callable[[str], None]],
) -> tuple[bool, list[str]]:
    """Verify the device is usable before running any task.

    Checks:
      - `adb devices` lists it as state=device (not offline/unauthorized)
      - every required Android package is installed
      - wake the device once (best-effort, no-op if already awake)

    Returns (ok, list_of_issues). When ok=False, caller should skip the device.
    """
    issues: list[str] = []
    try:
        states = {s: st for s, st in adb.devices_all()}
    except Exception as e:
        return False, [f"adb devices fail: {e}"]
    state = states.get(serial)
    if state != "device":
        issues.append(f"trạng thái ADB = {state or 'không thấy'}")
        return False, issues

    for pkg in needed_pkgs:
        r = adb.shell(serial, f"pm list packages {pkg}")
        if not r.ok or pkg not in (r.out or ""):
            issues.append(f"chưa cài {pkg}")

    # Wake screen — non-fatal if it fails.
    try:
        wake_device(adb, serial)
    except Exception:
        pass

    return (len(issues) == 0), issues


@dataclass
class Task:
    app: str
    keyword: str
    loops: int
    opts: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.app}|{self.keyword}|{self.loops}"


def _parse_opts(raw: str) -> dict:
    out: dict = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        try:
            out[k] = float(v) if "." in v else int(v)
        except ValueError:
            out[k] = v
    return out


def parse_task_line(line: str) -> Optional[Task]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        raise ValueError(f"Cần dạng app|keyword|loops, nhận: {line!r}")
    app, keyword, loops_raw, *rest = parts
    app = app.lower()
    if app not in SUPPORTED_APPS:
        raise ValueError(f"App không hỗ trợ: {app!r}. Hỗ trợ: {SUPPORTED_APPS}")
    if not keyword:
        raise ValueError(f"Thiếu keyword ở dòng: {line!r}")
    try:
        loops = int(loops_raw)
    except ValueError as e:
        raise ValueError(f"Loops không phải số nguyên: {loops_raw!r}") from e
    if loops <= 0:
        raise ValueError(f"Loops phải > 0, nhận: {loops}")
    opts = _parse_opts(rest[0]) if rest else {}
    return Task(app=app, keyword=keyword, loops=loops, opts=opts)


def load_tasks(path: str = "tasks.txt") -> list[Task]:
    p = Path(path)
    if not p.exists():
        return []
    tasks: list[Task] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        t = parse_task_line(ln)
        if t:
            tasks.append(t)
    return tasks


def _effective_watch(task: Task, watch_seconds: Optional[float], key_min: str, key_max: str,
                     default_min: float, default_max: float) -> tuple[float, float]:
    """Resolve watch-time range for a task.

    Priority:
      1. Explicit per-task opts (key_min/key_max in tasks.txt) — always win.
      2. Global `watch_seconds` from GUI/CLI — used as both min and max.
      3. Hard-coded defaults.
    """
    if key_min in task.opts or key_max in task.opts:
        return (
            float(task.opts.get(key_min, default_min)),
            float(task.opts.get(key_max, default_max)),
        )
    if watch_seconds is not None and watch_seconds > 0:
        return (float(watch_seconds), float(watch_seconds))
    return (default_min, default_max)


def _run_one_task(
    adb: ADB,
    serial: str,
    task: Task,
    stop_event: Optional[threading.Event],
    log_cb: Optional[Callable[[str], None]],
    watch_seconds: Optional[float] = None,
) -> int:
    """Run a task `loops` times on one device. Returns successful loop count."""
    succeeded = 0
    for i in range(task.loops):
        if stop_event and stop_event.is_set():
            return succeeded
        if log_cb:
            log_cb(f"[TASK] {task.app}|{task.keyword} lần {i+1}/{task.loops}")
        try:
            if task.app == "youtube":
                d_min, d_max = _effective_watch(task, watch_seconds, "delay_min", "delay_max", 10.0, 15.0)
                w_min, w_max = _effective_watch(task, watch_seconds, "watch_min", "watch_max", 60.0, 300.0)

                # Resolve the keyword list for this cycle. Two sources:
                #   GUI: opts["all_keywords"] is already a Python list
                #        (primary = element 0).
                #   CLI: opts["extra_keywords"] is a ";"-separated string
                #        (primary = task.keyword, extras = the split).
                all_kws = task.opts.get("all_keywords")
                if isinstance(all_kws, list) and all_kws:
                    extras = [str(k).strip() for k in all_kws[1:] if str(k).strip()]
                else:
                    extra_raw = task.opts.get("extra_keywords")
                    if isinstance(extra_raw, str) and extra_raw.strip():
                        extras = [k.strip() for k in extra_raw.split(";") if k.strip()]
                    else:
                        extras = []

                ok = run_youtube_task(
                    adb,
                    serial,
                    task.keyword,
                    extra_keywords=extras,
                    reels_min=int(task.opts.get("reels_min", 5)),
                    reels_max=int(task.opts.get("reels_max", 10)),
                    delay_min=d_min,
                    delay_max=d_max,
                    like_rate=float(task.opts.get("like_rate", 1.0)),
                    shorts_time_limit_sec=float(task.opts.get("shorts_time_limit", 0.0)),
                    watch_min_sec=w_min,
                    watch_max_sec=w_max,
                    stop_event=stop_event,
                    log_cb=log_cb,
                )
            elif task.app == "chrome":
                w_min, w_max = _effective_watch(task, watch_seconds, "watch_min", "watch_max", 60.0, 300.0)
                prefer_raw = task.opts.get("prefer_domain")
                prefer = str(prefer_raw).strip() if prefer_raw else None
                ok = run_chrome_task(
                    adb,
                    serial,
                    task.keyword,
                    prefer_domain=prefer or None,
                    watch_min_sec=w_min,
                    watch_max_sec=w_max,
                    stop_event=stop_event,
                    log_cb=log_cb,
                )
            else:
                if log_cb:
                    log_cb(f"[TASK] App không hỗ trợ: {task.app}")
                return succeeded
        except Exception as e:
            if log_cb:
                log_cb(f"[TASK] Lỗi loop {i+1}: {e}")
            ok = False
        if ok:
            succeeded += 1
        # small breather between loops (interruptible so Cancel feels instant)
        if interruptible_sleep(stop_event, lognormal_sleep(0.7, 0.5, 1.0, 4.0)):
            return succeeded
    return succeeded


def run_tasks_on_device(
    adb: ADB,
    serial: str,
    tasks: list[Task],
    *,
    stop_event: Optional[threading.Event] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    watch_seconds: Optional[float] = None,
) -> dict:
    """Run all tasks sequentially on one device.

    Args:
        watch_seconds: if set (>0), used as the watch time for BOTH each Shorts reel
            and each search-result video, unless the task line overrides delay_*/watch_*.
    Returns {"<task_str>": successful_loops} for stats reporting.
    """
    results: dict = {}

    # ---- Preflight: bail early on bad devices instead of running ghost tasks.
    needed_pkgs = {_APP_PKG[t.app] for t in tasks if t.app in _APP_PKG}
    ok, issues = _preflight(adb, serial, needed_pkgs, log_cb)
    if not ok:
        if log_cb:
            log_cb(f"[PREFLIGHT] SKIP — {', '.join(issues)}")
        return {"_skipped": 0}
    if log_cb:
        if issues:
            log_cb(f"[PREFLIGHT] OK (warnings: {', '.join(issues)})")
        else:
            log_cb(f"[PREFLIGHT] OK ({len(needed_pkgs)} app, state=device)")

    # Lock the device to portrait once so YouTube fullscreen video / Chrome
    # video pages can't flip the screen mid-flow (the root cause of the
    # "thiết bị tự xoay ngang" bug reported by the operator).
    #
    # Two-layer defense:
    #   1) apply_screen_config(lock_portrait=True) — system-level lock
    #      (accelerometer_rotation=0, user_rotation=0, wm lock).
    #   2) try_disable_orientation_requests() — Android 12+ flag that makes
    #      WindowManager IGNORE every app's setRequestedOrientation() call.
    #      This is the real fix for YouTube/Chrome auto-rotating to landscape
    #      when a 16:9 video plays. On Android < 12 this is a no-op; the
    #      per-loop ensure_portrait() watchdog handles those ROMs instead.
    try:
        apply_screen_config(adb, serial, lock_portrait=True)
        if log_cb:
            log_cb("[SETUP] Đã khoá portrait (chống xoay ngang)")
    except Exception as e:
        if log_cb:
            log_cb(f"[SETUP] Không khoá được portrait (bỏ qua): {e}")
    if try_disable_orientation_requests(adb, serial):
        if log_cb:
            log_cb("[SETUP] WindowManager đã bỏ qua mọi yêu cầu xoay từ app (Android 12+)")
    else:
        if log_cb:
            log_cb("[SETUP] ROM không hỗ trợ ignore-orientation-request — sẽ dùng watchdog ensure_portrait")

    for task in tasks:
        if stop_event and stop_event.is_set():
            break
        # Go home before each new task so we always start from a known place.
        home(adb, serial)
        if interruptible_sleep(stop_event, lognormal_sleep(0.5, 0.4, 0.6, 2.0)):
            break
        n = _run_one_task(adb, serial, task, stop_event, log_cb, watch_seconds)
        results[str(task)] = n
        if log_cb:
            log_cb(f"[TASK] Xong {task} -> {n}/{task.loops}")
        # Operator requirement: every program (YT / Chrome / mixed) ends at
        # the Android home screen so the device is left in a known clean
        # state for whatever the operator does next.
        home(adb, serial)

    # Final reset — also home after the whole batch (covers cancel-mid-loop).
    home(adb, serial)
    if log_cb:
        log_cb("[TASK] Tất cả task kết thúc, đã về Home screen")
    return results
