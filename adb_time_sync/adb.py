from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional


# On Windows, when this process has no console (PyInstaller --windowed),
# every subprocess call would otherwise pop up a fresh console window.
# CREATE_NO_WINDOW suppresses that.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass(frozen=True)
class CmdResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class ADB:
    def __init__(self, adb_path: str = "adb", timeout_sec: int = 20, verbose: bool = False):
        self.adb_path = adb_path
        self.timeout_sec = timeout_sec
        self.verbose = verbose

    def _run(self, args: List[str]) -> CmdResult:
        if self.verbose:
            print("[ADB]", " ".join(args))
        try:
            # adb output is UTF-8 (or arbitrary bytes from `dumpsys`/`uiautomator dump`
            # on locale-foreign devices). Windows default cp1252 cannot decode 0x80-0x9F,
            # so force utf-8 with errors=replace to never crash on stray bytes.
            p = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=self.timeout_sec,
                creationflags=NO_WINDOW,
            )
            return CmdResult(p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip())
        except subprocess.TimeoutExpired as e:
            return CmdResult(124, "", f"Timeout after {self.timeout_sec}s: {e}")
        except Exception as e:
            return CmdResult(1, "", f"Exception: {e}")

    def cmd(self, *args: str) -> CmdResult:
        return self._run([self.adb_path, *args])

    def shell(self, serial: str, command: str) -> CmdResult:
        # note: pass as one argument after "shell" (same behavior as `adb shell "<command>"`)
        return self._run([self.adb_path, "-s", serial, "shell", command])

    def devices_all(self) -> List[tuple[str, str]]:
        """
        Return list of (serial, state) for ALL entries in `adb devices`.
        state can be: device / unauthorized / offline / etc.
        """
        r = self.cmd("devices")
        if not r.ok:
            raise RuntimeError(r.err or r.out or "adb devices failed")

        lines = r.out.splitlines()
        if not lines:
            return []

        res: List[tuple[str, str]] = []
        for line in lines[1:]:  # skip header
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                res.append((parts[0], parts[1]))
        return res

    def connect_range(self, ip: str, start_port: int, end_port: int, delay_sec: float = 0.1) -> None:
        import time

        for port in range(start_port, end_port + 1):
            self.cmd("connect", f"{ip}:{port}")
            time.sleep(delay_sec)

    def disconnect_all(self) -> CmdResult:
        return self.cmd("disconnect")

    def disconnect(self, target: str) -> CmdResult:
        return self.cmd("disconnect", target)

    def get_global_setting(self, serial: str, key: str) -> Optional[str]:
        r = self.shell(serial, f"settings get global {key}")
        if not r.ok:
            return None
        return r.out.strip() if r.out else None

    def put_global_setting(self, serial: str, key: str, value: str) -> CmdResult:
        return self.shell(serial, f"settings put global {key} {value}")

    def getprop(self, serial: str, key: str) -> Optional[str]:
        r = self.shell(serial, f"getprop {key}")
        if not r.ok:
            return None
        return r.out.strip() if r.out else None

    def setprop(self, serial: str, key: str, value: str) -> CmdResult:
        return self.shell(serial, f"setprop {key} {value}")
