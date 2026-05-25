"""Unicode-safe text input over ADB.

`adb shell input text` cannot type accented characters reliably,
so we fall back to clipboard paste. Order tried:
  1) `cmd clipboard set-text` (Android 13+, requires shell perm)
  2) ADBKeyboard broadcast (com.android.adbkeyboard installed + IME enabled)
  3) Plain `input text` with %s/space escaping (loses diacritics)
"""
from __future__ import annotations

import base64
import shlex
from typing import Optional

from .adb import ADB
from .human import enter, key


ADB_KEYBOARD_PKG = "com.android.adbkeyboard"
ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"


def _has_adb_keyboard(adb: ADB, serial: str) -> bool:
    r = adb.shell(serial, f"pm list packages {ADB_KEYBOARD_PKG}")
    return bool(r.ok and r.out and ADB_KEYBOARD_PKG in r.out)


def _current_ime(adb: ADB, serial: str) -> Optional[str]:
    r = adb.shell(serial, "settings get secure default_input_method")
    if r.ok and r.out:
        return r.out.strip()
    return None


def _switch_to_adb_ime(adb: ADB, serial: str) -> bool:
    adb.shell(serial, f"ime enable {ADB_KEYBOARD_IME}")
    r = adb.shell(serial, f"ime set {ADB_KEYBOARD_IME}")
    return r.ok


def _restore_ime(adb: ADB, serial: str, previous: Optional[str]) -> None:
    if not previous or previous == ADB_KEYBOARD_IME:
        return
    adb.shell(serial, f"ime set {previous}")


def _set_clipboard(adb: ADB, serial: str, text: str) -> bool:
    quoted = shlex.quote(text)
    r = adb.shell(serial, f"cmd clipboard set-text {quoted}")
    if r.ok:
        return True
    r2 = adb.shell(serial, f"service call clipboard 2 i32 1 i32 0 i32 1 s16 {shlex.quote(text)}")
    return r2.ok


def _paste_via_keyevent(adb: ADB, serial: str) -> bool:
    return key(adb, serial, "279")  # KEYCODE_PASTE


def _type_via_adb_keyboard(adb: ADB, serial: str, text: str) -> bool:
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    r = adb.shell(
        serial,
        f"am broadcast -a ADB_INPUT_B64 --es msg {payload}",
    )
    if r.ok and "result=-1" in (r.out or ""):
        return True
    r2 = adb.shell(
        serial,
        f"am broadcast -a ADB_INPUT_TEXT --es msg {shlex.quote(text)}",
    )
    return r2.ok and "result=-1" in (r2.out or "")


def _type_via_input_text(adb: ADB, serial: str, text: str) -> bool:
    safe = text.replace(" ", "%s")
    safe = safe.replace("'", "")
    r = adb.shell(serial, f"input text {shlex.quote(safe)}")
    return r.ok


def type_text(
    adb: ADB,
    serial: str,
    text: str,
    submit: bool = False,
    prefer_clipboard: bool = True,
) -> tuple[bool, str]:
    """Type unicode text into the focused field. Returns (ok, method_used)."""
    text = text or ""
    if not text:
        return True, "empty"

    if prefer_clipboard:
        if _set_clipboard(adb, serial, text) and _paste_via_keyevent(adb, serial):
            if submit:
                enter(adb, serial)
            return True, "clipboard"

    if _has_adb_keyboard(adb, serial):
        prev = _current_ime(adb, serial)
        if _switch_to_adb_ime(adb, serial):
            ok = _type_via_adb_keyboard(adb, serial, text)
            if ok and submit:
                enter(adb, serial)
            _restore_ime(adb, serial, prev)
            if ok:
                return True, "adb_keyboard"

    ok = _type_via_input_text(adb, serial, text)
    if ok and submit:
        enter(adb, serial)
    return ok, "input_text"


def clear_field(adb: ADB, serial: str, max_chars: int = 80) -> None:
    """Best-effort: select-all + delete the currently focused text field."""
    adb.shell(serial, "input keyevent KEYCODE_MOVE_END")
    for _ in range(max_chars):
        adb.shell(serial, "input keyevent KEYCODE_DEL")
