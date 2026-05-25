from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .adb import ADB, CmdResult


@dataclass
class ResetReport:
    went_home: bool
    removed_tasks: int
    removed_task_ids: List[int]
    fallback_kill_all_used: bool
    notes: List[str]


def _try(adb: ADB, serial: str, what: str, cmd: str) -> CmdResult:
    r = adb.shell(serial, cmd)
    return r


def go_home(adb: ADB, serial: str) -> bool:
    # HOME
    r = _try(adb, serial, "go_home", "input keyevent KEYCODE_HOME")
    return r.ok


def _parse_task_ids_from_dumpsys(text: str, limit: int = 50) -> List[int]:
    """
    Parse task IDs from dumpsys activity recents output.
    This is heuristic because output differs by Android version/OEM.
    """
    ids: List[int] = []

    # Common patterns seen across versions:
    # - "taskId=123"
    # - "TaskRecord{... #123 ...}" (older)
    # - "RecentTaskInfo{... id=123 ...}" / "id=123"
    patterns = [
        r"\btaskId=(\d+)\b",
        r"TaskRecord\{[^}]*\s#(\d+)\b",
        r"\bid=(\d+)\b",
    ]

    for pat in patterns:
        for m in re.finditer(pat, text):
            try:
                tid = int(m.group(1))
                if tid not in ids:
                    ids.append(tid)
                if len(ids) >= limit:
                    return ids
            except Exception:
                pass
    return ids


def close_all_recents(adb: ADB, serial: str, max_tasks: int = 50) -> ResetReport:
    notes: List[str] = []
    removed: List[int] = []
    fallback_used = False

    went_home = go_home(adb, serial)

    # Get recents dump
    dump = adb.shell(serial, "dumpsys activity recents")
    if not dump.ok:
        notes.append(f"dumpsys recents failed: {dump.err or dump.out}")
        # fallback
        ka = adb.shell(serial, "am kill-all")
        fallback_used = ka.ok
        return ResetReport(went_home, 0, [], fallback_used, notes)

    task_ids = _parse_task_ids_from_dumpsys(dump.out, limit=max_tasks)
    if not task_ids:
        # Nothing parsed; fallback kill-all
        notes.append("No task IDs parsed from dumpsys; using fallback am kill-all")
        ka = adb.shell(serial, "am kill-all")
        fallback_used = ka.ok
        return ResetReport(went_home, 0, [], fallback_used, notes)

    # Try remove-task for each id
    for tid in task_ids:
        r = adb.shell(serial, f"cmd activity remove-task {tid}")
        if r.ok:
            removed.append(tid)

    if not removed:
        # remove-task may not exist/allowed → fallback kill-all
        notes.append("cmd activity remove-task did not remove any tasks; fallback am kill-all")
        ka = adb.shell(serial, "am kill-all")
        fallback_used = ka.ok

    # Return home again (nice to ensure)
    go_home(adb, serial)

    return ResetReport(went_home, len(removed), removed, fallback_used, notes)
