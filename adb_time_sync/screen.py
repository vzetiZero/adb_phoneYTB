from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
