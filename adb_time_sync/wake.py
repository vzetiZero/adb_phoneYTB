from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .adb import ADB, CmdResult


@dataclass
class SleepState:
    is_asleep: Optional[bool]
    reason: str
    raw: str


def read_sleep_state(adb: ADB, serial: str) -> SleepState:
    r = adb.shell(serial, "dumpsys power")
    if not r.ok or not r.out:
        return SleepState(None, "dumpsys power failed", r.err or r.out or "")

    text = r.out
    lower = text.lower()

    if "mwakefulness=asleep" in lower or "wakefulness=asleep" in lower:
        return SleepState(True, "wakefulness=asleep", text)
    if "mwakefulness=awake" in lower or "wakefulness=awake" in lower:
        return SleepState(False, "wakefulness=awake", text)

    # Fallback: check display power state
    if "display power" in lower and "state=off" in lower:
        return SleepState(True, "display_state=off", text)
    if "display power" in lower and "state=on" in lower:
        return SleepState(False, "display_state=on", text)

    return SleepState(None, "unknown", text)


def wake_device(adb: ADB, serial: str) -> CmdResult:
    # Best-effort wake: WAKEUP then HOME.
    adb.shell(serial, "input keyevent KEYCODE_WAKEUP")
    return adb.shell(serial, "input keyevent KEYCODE_HOME")
