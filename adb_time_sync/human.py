"""Human-like input primitives over ADB.

Avoid uiautomator2 — all interactions go through `adb shell`.
Adds jitter + log-normal pacing so that scroll / tap patterns
look less like a robot to engagement-scoring services.
"""
from __future__ import annotations

import asyncio
import random
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .adb import ADB, NO_WINDOW


@dataclass(frozen=True)
class Size:
    width: int
    height: int


def get_size(adb: ADB, serial: str) -> Size:
    r = adb.shell(serial, "wm size")
    if r.ok and r.out:
        for line in r.out.splitlines():
            if "Physical size:" in line or "Override size:" in line:
                try:
                    wh = line.split(":", 1)[1].strip()
                    w, h = [int(x) for x in wh.lower().split("x", 1)]
                    return Size(w, h)
                except Exception:
                    pass
    return Size(1080, 1920)


def lognormal_sleep(mu: float = 0.7, sigma: float = 0.5, lo: float = 0.2, hi: float = 6.0) -> float:
    """Return a log-normal-distributed delay seconds, clamped to [lo, hi].

    With mu=0.7 sigma=0.5 the median is ~2.0s, with a long tail.
    """
    v = random.lognormvariate(mu, sigma)
    return max(lo, min(hi, v))


async def human_sleep(mu: float = 0.7, sigma: float = 0.5, lo: float = 0.2, hi: float = 6.0) -> None:
    await asyncio.sleep(lognormal_sleep(mu, sigma, lo, hi))


def watch_duration_seconds(min_s: float, max_s: float) -> float:
    """Pick a watch duration roughly log-normal between min_s..max_s."""
    span = max(1.0, max_s - min_s)
    mu = 0.0
    sigma = 0.7
    v = random.lognormvariate(mu, sigma)
    v = min(v, 4.0)
    return min_s + (v / 4.0) * span


def tap(adb: ADB, serial: str, x: int, y: int, jitter: int = 8) -> bool:
    jx = x + random.randint(-jitter, jitter)
    jy = y + random.randint(-jitter, jitter)
    r = adb.shell(serial, f"input tap {jx} {jy}")
    return r.ok


def swipe(
    adb: ADB,
    serial: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 320,
    jitter: int = 25,
) -> bool:
    def jit(v: int) -> int:
        return v + random.randint(-jitter, jitter)
    dur = duration_ms + random.randint(-60, 80)
    if dur < 80:
        dur = 80
    r = adb.shell(serial, f"input swipe {jit(x1)} {jit(y1)} {jit(x2)} {jit(y2)} {dur}")
    return r.ok


def swipe_up(adb: ADB, serial: str, size: Optional[Size] = None, strong: bool = False) -> bool:
    s = size or get_size(adb, serial)
    x = int(s.width * (0.45 + random.uniform(0, 0.1)))
    y1 = int(s.height * (0.78 + random.uniform(-0.04, 0.04)))
    y2 = int(s.height * (0.22 + random.uniform(-0.04, 0.04)))
    dur = random.randint(180, 320) if strong else random.randint(280, 520)
    return swipe(adb, serial, x, y1, x, y2, duration_ms=dur)


def swipe_down(adb: ADB, serial: str, size: Optional[Size] = None) -> bool:
    s = size or get_size(adb, serial)
    x = int(s.width * (0.45 + random.uniform(0, 0.1)))
    y1 = int(s.height * (0.30 + random.uniform(-0.04, 0.04)))
    y2 = int(s.height * (0.75 + random.uniform(-0.04, 0.04)))
    dur = random.randint(280, 520)
    return swipe(adb, serial, x, y1, x, y2, duration_ms=dur)


def key(adb: ADB, serial: str, keycode: str) -> bool:
    r = adb.shell(serial, f"input keyevent {keycode}")
    return r.ok


def back(adb: ADB, serial: str) -> bool:
    return key(adb, serial, "KEYCODE_BACK")


def home(adb: ADB, serial: str) -> bool:
    return key(adb, serial, "KEYCODE_HOME")


def enter(adb: ADB, serial: str) -> bool:
    return key(adb, serial, "KEYCODE_ENTER")


def screencap_png(adb: ADB, serial: str, timeout: int = 20) -> Optional[bytes]:
    """Grab a screenshot as PNG bytes via exec-out (no temp file)."""
    try:
        p = subprocess.run(
            [adb.adb_path, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
        if p.returncode != 0 or not p.stdout:
            return None
        return p.stdout
    except Exception:
        return None


def interruptible_sleep(
    stop_event: Optional[threading.Event],
    seconds: float,
    chunk: float = 0.3,
) -> bool:
    """Sleep up to `seconds`, but bail out the moment `stop_event` is set.

    Returns True if the stop event fired (caller should abort), False otherwise.
    Use this instead of `time.sleep(N)` for any N > ~1s in worker code so the
    Cancel button feels responsive (latency ≤ `chunk`).
    """
    if seconds <= 0:
        return bool(stop_event and stop_event.is_set())
    end = time.time() + seconds
    while True:
        if stop_event and stop_event.is_set():
            return True
        remaining = end - time.time()
        if remaining <= 0:
            return False
        time.sleep(min(chunk, remaining))


import re as _re

_RESUMED_RE = _re.compile(r"(?:topResumedActivity|ResumedActivity)[=:].*?\s([\w\.]+)/([\w\.\$]+)")
_FOCUS_RE = _re.compile(r"(?:mCurrentFocus|mFocusedApp)=.*?\s([\w\.]+)/([\w\.\$]+)")


def current_focus(adb: ADB, serial: str) -> Optional[str]:
    """Return the foreground package name. Works on Android 10-13."""
    r = adb.shell(serial, "dumpsys activity activities")
    if r.ok and r.out:
        m = _RESUMED_RE.search(r.out)
        if m:
            return m.group(1)
    r2 = adb.shell(serial, "dumpsys window")
    if r2.ok and r2.out:
        m = _FOCUS_RE.search(r2.out)
        if m:
            return m.group(1)
    return None


def wait_for_foreground(
    adb: ADB,
    serial: str,
    package: str,
    timeout: float = 8.0,
    poll: float = 0.4,
) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if current_focus(adb, serial) == package:
            return True
        time.sleep(poll)
    return False


def is_foreground(adb: ADB, serial: str, package: str) -> bool:
    pkg = current_focus(adb, serial)
    return bool(pkg) and pkg == package


def ensure_app_foreground(
    adb: ADB,
    serial: str,
    target_pkg: str,
    log_cb=None,
    max_back: int = 3,
) -> bool:
    """Recover when an ad / popup hijacks the foreground.

    Real cases observed on BoxPhone Hàn:
      - Tapping a Shorts ad opens Google Play (com.android.vending) full-screen.
      - Tapping a Chrome SERP ad opens Samsung Internet / external app.
      - A YouTube "App-install" interstitial backed by Play Store.

    Strategy (least to most aggressive):
      1) target_pkg already foreground → done.
      2) Press BACK up to `max_back` times — handles most ad-popup redirects
         because the hijacker was launched with FLAG_ACTIVITY_NEW_TASK and
         BACK returns to the previous task (our target app).
      3) Still hijacked → `am force-stop <hijacker>` then bring target back
         via `monkey LAUNCHER`. We deliberately DO NOT force-stop the target,
         so its in-app state (current reel / current article tab) survives.

    Returns True iff target_pkg is foreground when we return.
    """
    if is_foreground(adb, serial, target_pkg):
        return True

    cur = current_focus(adb, serial)
    if log_cb:
        log_cb(f"[FG] Foreground={cur!r} ≠ {target_pkg!r}; back {max_back} lần để thoát")

    for i in range(max_back):
        adb.shell(serial, "input keyevent KEYCODE_BACK")
        time.sleep(0.4)
        if is_foreground(adb, serial, target_pkg):
            if log_cb:
                log_cb(f"[FG] Đã về {target_pkg} sau {i+1} lần back")
            return True

    # Hard reset: kill the hijacker (NOT the target), relaunch target.
    cur = current_focus(adb, serial)
    if cur and cur != target_pkg:
        if log_cb:
            log_cb(f"[FG] Vẫn bị giữ bởi {cur!r}; force-stop và relaunch {target_pkg}")
        adb.shell(serial, f"am force-stop {cur}")
    adb.shell(serial, f"monkey -p {target_pkg} -c android.intent.category.LAUNCHER 1")
    time.sleep(0.8)
    return is_foreground(adb, serial, target_pkg)
