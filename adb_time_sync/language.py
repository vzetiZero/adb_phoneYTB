from __future__ import annotations

from typing import Optional

from .adb import ADB, CmdResult


def _normalize_locale(locale: str) -> str:
    return locale.strip()


def _build_locale_list(locale: str, current: Optional[str]) -> str:
    # Keep target locale first, preserve the rest.
    if current and current.lower() != "null":
        parts = [p.strip() for p in current.split(",") if p.strip()]
        parts = [p for p in parts if p.lower() != locale.lower()]
        return ",".join([locale] + parts)
    return locale


def apply_language_config(adb: ADB, serial: str, locale: str = "vi-VN") -> CmdResult:
    """
    Best-effort set device language/locale to target.
    """
    locale = _normalize_locale(locale)
    if not locale:
        raise ValueError("locale is empty")

    # Try cmd locale (Android 13+).
    r = adb.shell(serial, f"cmd locale set {locale}")
    if r.ok:
        return r

    # Try settings system/secure locales.
    current = adb.shell(serial, "settings get system system_locales").out or ""
    locale_list = _build_locale_list(locale, current)

    r2 = adb.shell(serial, f"settings put system system_locales {locale_list}")
    if r2.ok:
        return r2

    r3 = adb.shell(serial, f"settings put secure system_locales {locale_list}")
    if r3.ok:
        return r3

    # Last resort: setprop (may require root).
    r4 = adb.shell(serial, f"setprop persist.sys.locale {locale}")
    if r4.ok:
        return r4

    return r3 if r3.ok else (r2 if r2.ok else (r if r.ok else r4))
