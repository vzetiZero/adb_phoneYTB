from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from .adb import ADB, CmdResult


@dataclass
class ScreenState:
    screen_off_timeout: Optional[str] = None
    stay_on_while_plugged_in: Optional[str] = None


def _must_ok(serial: str, r: CmdResult, what: str) -> None:
    if not r.ok:
        raise RuntimeError(f"[{serial}] {what} failed: {r.err or r.out}".strip())


def read_screen_state(adb: ADB, serial: str) -> ScreenState:
    st = ScreenState()
    r1 = adb.shell(serial, "settings get system screen_off_timeout")
    st.screen_off_timeout = r1.out.strip() if r1.ok and r1.out else None

    r2 = adb.shell(serial, "settings get global stay_on_while_plugged_in")
    st.stay_on_while_plugged_in = r2.out.strip() if r2.ok and r2.out else None
    return st


def apply_screen_config(
    adb: ADB,
    serial: str,
    screen_timeout_ms: int | None = None,
    stay_awake_when_plugged: bool | None = None,
    lock_portrait: bool | None = None,
) -> ScreenState:
    """
    screen_timeout_ms: screen-off timeout in ms.
      - Example: 2147483647 (max int 32-bit) ~ 24.8 days
      - 86400000 = 24 hours

    stay_awake_when_plugged:
      - True: keep screen on while charging (AC/USB/Wireless) => stay_on_while_plugged_in=7
      - False: disable stay-awake => 0
      - None: no change

    lock_portrait:
      - True: force portrait and disable auto-rotate
      - False: enable auto-rotate
      - None: no change
    """
    if screen_timeout_ms is not None:
        if screen_timeout_ms <= 0:
            raise ValueError("screen_timeout_ms must be > 0")
        r = adb.shell(serial, f"settings put system screen_off_timeout {screen_timeout_ms}")
        _must_ok(serial, r, f"settings put system screen_off_timeout {screen_timeout_ms}")

    if stay_awake_when_plugged is True:
        r = adb.shell(serial, "settings put global stay_on_while_plugged_in 7")
        _must_ok(serial, r, "settings put global stay_on_while_plugged_in 7")
    elif stay_awake_when_plugged is False:
        r = adb.shell(serial, "settings put global stay_on_while_plugged_in 0")
        _must_ok(serial, r, "settings put global stay_on_while_plugged_in 0")

    if lock_portrait is True:
        r1 = adb.shell(serial, "settings put system accelerometer_rotation 0")
        _must_ok(serial, r1, "settings put system accelerometer_rotation 0")
        r2 = adb.shell(serial, "settings put system user_rotation 0")
        _must_ok(serial, r2, "settings put system user_rotation 0")
        # Best-effort for newer Android versions
        adb.shell(serial, "cmd display set-user-rotation 0")
        adb.shell(serial, "wm set-user-rotation lock 0")
        adb.shell(serial, "wm set-fix-to-user-rotation enabled")
    elif lock_portrait is False:
        r1 = adb.shell(serial, "settings put system accelerometer_rotation 1")
        _must_ok(serial, r1, "settings put system accelerometer_rotation 1")

    return read_screen_state(adb, serial)


# ---------------------------------------------------------------------------
# Rotation watchdog — detect & recover from app-driven landscape flips.
# ---------------------------------------------------------------------------

_ROT_INPUT_RE = re.compile(r"SurfaceOrientation:\s*(\d)")
_ROT_WINDOW_RE = re.compile(r"mCurRotation=ROTATION_(\d+)")


def get_current_rotation(adb: ADB, serial: str) -> Optional[int]:
    """Return current display rotation as Surface.ROTATION_* (0/1/2/3).

    0 = portrait (natural), 1 = landscape (rotated 90° CCW),
    2 = upside-down portrait, 3 = landscape (rotated 90° CW).
    Returns None when we couldn't parse — caller should leave state alone.

    Tries 3 methods so it works from Android 8 (BoxPhone Hàn cũ) through 14.
    """
    # Method 1 — `dumpsys input` ships SurfaceOrientation on every Android
    # release we've seen. Cheap and stable. (~100ms)
    r = adb.shell(serial, "dumpsys input")
    if r.ok and r.out:
        m = _ROT_INPUT_RE.search(r.out)
        if m:
            return int(m.group(1))

    # Method 2 — `settings get system user_rotation`. This is what we WROTE
    # earlier; reading it back tells us if an app overrode it.
    r = adb.shell(serial, "settings get system user_rotation")
    if r.ok and r.out:
        v = r.out.strip()
        if v.isdigit():
            return int(v)

    # Method 3 — fall back to WindowManager state ("mCurRotation=ROTATION_90"...)
    r = adb.shell(serial, "dumpsys window displays")
    if r.ok and r.out:
        m = _ROT_WINDOW_RE.search(r.out)
        if m:
            return int(m.group(1)) // 90

    return None


def try_disable_orientation_requests(adb: ADB, serial: str) -> bool:
    """Ask the WindowManager to IGNORE every app's setRequestedOrientation()
    call. This is the cleanest fix for the YouTube/Chrome auto-fullscreen
    landscape problem, but it only works on Android 12+ (introduced in API 31).

    On older ROMs the command returns non-zero or prints "Unknown command";
    we treat both as "feature unavailable" and return False — the watchdog
    still runs as fallback.
    """
    r = adb.shell(serial, "cmd window set-ignore-orientation-request true")
    if not r.ok:
        return False
    blob = (r.out + " " + r.err).lower()
    if "unknown" in blob or "error" in blob or "exception" in blob:
        return False
    return True


def ensure_portrait(
    adb: ADB,
    serial: str,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """Watchdog: if the device has rotated out of portrait, re-apply the lock.

    Call this periodically inside long-running loops (`_watch_video`,
    `_browse_landing`, between Shorts reels). It's cheap when already
    portrait (one `dumpsys input` ~100ms) and corrective when not.

    Returns True if the device is portrait when we return, False if we
    couldn't detect/fix.
    """
    rot = get_current_rotation(adb, serial)
    if rot is None:
        return False  # unknown — don't fight what we can't measure
    if rot == 0:
        return True
    if log_cb:
        log_cb(f"[ORI] Phát hiện rotation={rot}, ép lại portrait")
    try:
        apply_screen_config(adb, serial, lock_portrait=True)
    except Exception as e:
        if log_cb:
            log_cb(f"[ORI] apply_screen_config fail: {e}")
        return False
    # Try the modern flag again in case app re-enabled it
    try_disable_orientation_requests(adb, serial)
    # Re-verify; some BoxPhone ROMs need a beat to settle.
    rot2 = get_current_rotation(adb, serial)
    if rot2 == 0:
        return True
    if log_cb:
        log_cb(f"[ORI] Sau khi ép vẫn rotation={rot2} — chấp nhận, tiếp tục")
    return False
