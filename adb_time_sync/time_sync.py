from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .adb import ADB, CmdResult


@dataclass
class TimeState:
    auto_time: Optional[str] = None
    auto_time_zone: Optional[str] = None
    time_zone_global: Optional[str] = None
    persist_tz: Optional[str] = None
    date: Optional[str] = None


def _must_ok(serial: str, r: CmdResult, what: str) -> None:
    if not r.ok:
        raise RuntimeError(f"[{serial}] {what} failed: {r.err or r.out}".strip())


def read_time_state(adb: ADB, serial: str) -> TimeState:
    st = TimeState()
    st.auto_time = adb.get_global_setting(serial, "auto_time")
    st.auto_time_zone = adb.get_global_setting(serial, "auto_time_zone")
    st.time_zone_global = adb.get_global_setting(serial, "time_zone")
    st.persist_tz = adb.getprop(serial, "persist.sys.timezone")
    st.date = adb.shell(serial, "date").out or None
    return st


def apply_time_config(
    adb: ADB,
    serial: str,
    tz_mode: str,
    timezone: str = "Asia/Ho_Chi_Minh",
    auto_time: bool = True,
) -> TimeState:
    """
    tz_mode:
      - "fixed": force timezone to `timezone` and prevent override by turning auto_time_zone OFF.
      - "auto": let Android/network/location pick timezone (auto_time_zone ON). No manual TZ set.

    auto_time:
      - when True, set auto_time=1 (recommended).
    """
    tz_mode = tz_mode.lower().strip()
    if tz_mode not in {"fixed", "auto"}:
        raise ValueError("tz_mode must be 'fixed' or 'auto'")

    # 1) Time sync mode
    if auto_time:
        r = adb.put_global_setting(serial, "auto_time", "1")
        _must_ok(serial, r, "settings put global auto_time 1")
    else:
        r = adb.put_global_setting(serial, "auto_time", "0")
        _must_ok(serial, r, "settings put global auto_time 0")

    # 2) Timezone strategy
    if tz_mode == "fixed":
        # IMPORTANT: disable auto_time_zone first, otherwise it will override manual timezone
        r = adb.put_global_setting(serial, "auto_time_zone", "0")
        _must_ok(serial, r, "settings put global auto_time_zone 0")

        # Set timezone (best-effort across ROMs):
        # Some ROMs accept only settings global time_zone, some prefer persist.sys.timezone.
        # We'll try both and verify after.
        adb.setprop(serial, "persist.sys.timezone", timezone)  # best-effort (may fail on some ROMs)
        r2 = adb.put_global_setting(serial, "time_zone", timezone)
        _must_ok(serial, r2, f"settings put global time_zone {timezone}")

    else:  # "auto"
        # Do not force timezone; allow system detector to decide.
        r = adb.put_global_setting(serial, "auto_time_zone", "1")
        _must_ok(serial, r, "settings put global auto_time_zone 1")

    # 3) Return state after changes
    return read_time_state(adb, serial)


def _format_android_datetime(dt: datetime) -> str:
    # Common Android/toolbox date formats.
    return dt.strftime("%Y%m%d.%H%M%S")


def _format_android_datetime_alt(dt: datetime) -> str:
    # Another common format: MMDDhhmmYYYY.ss
    return dt.strftime("%m%d%H%M%Y.%S")


def _format_android_datetime_set(dt: datetime) -> str:
    # Default SET format: MMDDhhmm[[CC]YY][.ss]
    return dt.strftime("%m%d%H%M%Y.%S")


def _format_android_datetime_iso(dt: datetime) -> str:
    # ISO-like format for -D usage.
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def set_time_from_pc(adb: ADB, serial: str, dt: Optional[datetime] = None) -> CmdResult:
    """
    Best-effort set device time to PC local time.
    Tries multiple date syntaxes; if permission denied, retries with su -c.
    """
    dt = dt or datetime.now()
    payload_alt = _format_android_datetime_alt(dt)
    payload_set = _format_android_datetime_set(dt)
    payload_iso = _format_android_datetime_iso(dt)
    payload_epoch = str(int(dt.timestamp()))
    payload_epoch_ms = str(int(dt.timestamp() * 1000))
    # Try several variants because different ROMs ship different date binaries.
    cmds = [
        (f"cmd alarm set-time {payload_epoch_ms}", False),
        (f"date @{payload_epoch}", False),
        (f"date -D \"%Y-%m-%d %H:%M:%S\" \"{payload_iso}\"", False),
        (f"date -D \"%m%d%H%M%Y.%S\" \"{payload_set}\"", False),
        (f"date {payload_set}", False),
        (f"date {payload_alt}", False),
        (f"toybox date @{payload_epoch}", True),
        (f"toybox date -D \"%Y-%m-%d %H:%M:%S\" \"{payload_iso}\"", True),
        (f"toybox date -D \"%m%d%H%M%Y.%S\" \"{payload_set}\"", True),
        (f"toybox date {payload_set}", True),
        (f"toybox date {payload_alt}", True),
        (f"busybox date @{payload_epoch}", True),
        (f"busybox date -D \"%Y-%m-%d %H:%M:%S\" \"{payload_iso}\"", True),
        (f"busybox date -D \"%m%d%H%M%Y.%S\" \"{payload_set}\"", True),
        (f"busybox date {payload_set}", True),
        (f"busybox date {payload_alt}", True),
    ]

    last = CmdResult(1, "", "date command failed")
    for cmd, optional in cmds:
        r = adb.shell(serial, cmd)
        if r.ok:
            return r
        err = (r.err or r.out or "").lower()

        if optional and ("not found" in err or "no such file" in err):
            # Optional binary missing; don't let it become the final error.
            continue

        last = r
        if "permission" in err or "not permitted" in err or "operation not permitted" in err:
            r2 = adb.shell(serial, f"su -c \"{cmd}\"")
            if r2.ok:
                return r2
            last = r2

    return last
