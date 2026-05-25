"""Chrome automation flow.

Per task:
  1. Open Chrome on a Google search for the keyword
     (intent URL avoids fighting the omnibox).
  2. Scroll the SERP 1-3 times, then tap a random non-ad
     result in the top 5.
  3. Browse the landing page for `watch_min_sec..watch_max_sec`,
     alternating scroll-up / scroll-down with reading pauses.
  4. Return so the caller can loop.
"""
from __future__ import annotations

import random
import threading
import time
import urllib.parse
from typing import Callable, Optional

from .adb import ADB
from .human import (
    Size,
    back,
    ensure_app_foreground,
    get_size,
    interruptible_sleep,
    is_foreground,
    lognormal_sleep,
    swipe,
    swipe_down,
    swipe_up,
    tap,
)
from .screen import ensure_portrait
from .ui_state import dump_ui, find_by_text, iter_nodes


CHROME_PKG = "com.android.chrome"
CHROME_MAIN = "com.android.chrome/com.google.android.apps.chrome.Main"

# Ad / sponsored labels Google SERP uses across locales. All lowercase
# because we compare against `.lower()` of the node text+desc.
AD_LABELS = (
    # Korean (target device)
    "광고", "스폰서",
    # English
    "sponsored", "ad ·", "ads ·", " ad ", "promoted", "ad ", "ad-",
    # Vietnamese
    "quảng cáo", "được tài trợ",
    # Chinese (Simplified / Traditional)
    "广告", "赞助商", "贊助",
    # Japanese
    "広告", "スポンサー",
    # German / French / Spanish / Indonesian / Thai
    "werbung", "anzeige", "gesponsert",
    "annonce", "sponsorisé",
    "anuncio", "patrocinado",
    "iklan",
    "โฆษณา",
)


def _log(log_cb: Optional[Callable[[str], None]], msg: str) -> None:
    if log_cb:
        log_cb(msg)


def _cancelled(stop_event: Optional[threading.Event]) -> bool:
    return bool(stop_event and stop_event.is_set())


def _open_chrome_url(adb: ADB, serial: str, url: str, log_cb=None) -> bool:
    """Open `url` in Chrome via a VIEW intent.

    --activity-clear-task ensures we land on a fresh tab so the
    omnibox state from a previous task can't leak.
    """
    cmd = (
        f'am start -a android.intent.action.VIEW '
        f'-d "{url}" '
        f'--activity-clear-task -n {CHROME_MAIN}'
    )
    r = adb.shell(serial, cmd)
    if not r.ok:
        r = adb.shell(serial, f'am start -a android.intent.action.VIEW -d "{url}"')
    if not r.ok:
        _log(log_cb, f"[CHR] Không mở được Chrome: {r.err or r.out}")
        return False
    time.sleep(lognormal_sleep(1.0, 0.5, 2.0, 5.0))
    return is_foreground(adb, serial, CHROME_PKG)


def _open_search_url(adb: ADB, serial: str, keyword: str, log_cb=None) -> bool:
    q = urllib.parse.quote_plus(keyword)
    return _open_chrome_url(adb, serial, f"https://www.google.com/search?q={q}", log_cb)


def _normalize_domain(raw: str) -> str:
    """Strip scheme / path / 'www.' for clean substring comparison."""
    d = (raw or "").strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def _scroll_serp(adb: ADB, serial: str, size: Size, log_cb=None) -> None:
    n = random.randint(1, 3)
    for i in range(n):
        swipe_up(adb, serial, size)
        time.sleep(lognormal_sleep(0.6, 0.4, 0.6, 2.5))
    if random.random() < 0.35:
        swipe_down(adb, serial, size)
        time.sleep(lognormal_sleep(0.5, 0.3, 0.4, 1.6))


def _collect_serp_candidates(xml: str, size: Size) -> list:
    """Return clickable SERP nodes (non-ad, sized, below omnibox), sorted top-down."""
    out = []
    if not xml:
        return out
    for node in iter_nodes(xml):
        if not node.clickable:
            continue
        if node.class_name not in (
            "android.view.View",
            "android.widget.LinkTextView",
            "android.widget.TextView",
        ):
            continue
        text = (node.text or "") + " " + (node.desc or "")
        low = text.lower()
        if len(text.strip()) < 15:
            continue
        if any(bad in low for bad in AD_LABELS):
            continue
        x1, y1, x2, y2 = node.bounds
        if (y2 - y1) < 30:
            continue
        if y1 < size.height * 0.12:
            continue  # omnibox area
        out.append(node)
    out.sort(key=lambda n: n.bounds[1])
    return out


def _pick_serp_result(
    adb: ADB,
    serial: str,
    size: Size,
    prefer_domain: Optional[str] = None,
    log_cb=None,
) -> bool:
    """Tap a non-ad SERP result.

    If `prefer_domain` is set, only tap a candidate whose visible text/desc
    contains that domain (e.g. "news.naver.com"). Returns True if we tapped
    a domain match, False if the preferred domain wasn't found (so the caller
    can fall back to opening the URL directly).

    Without `prefer_domain`: tap the topmost non-ad result; final fallback
    tap-at-y-ratio for canvas-rendered SERPs.
    """
    xml = dump_ui(adb, serial)
    candidates = _collect_serp_candidates(xml or "", size)

    if prefer_domain:
        needle = _normalize_domain(prefer_domain)
        for node in candidates:
            text = ((node.text or "") + " " + (node.desc or "")).lower()
            if needle in text:
                _log(log_cb, f"[CHR] Tap kết quả khớp domain {needle!r}: {node.text[:60]!r} @{node.center}")
                tap(adb, serial, *node.center)
                return True
        _log(log_cb, f"[CHR] Không thấy domain {needle!r} trong SERP — sẽ fallback mở thẳng URL")
        return False

    if candidates:
        pick = candidates[0]
        _log(log_cb, f"[CHR] Tap kết quả #1: {pick.text[:60]!r} @{pick.center}")
        tap(adb, serial, *pick.center)
        return True

    # Y-ratio fallback for canvas-rendered results — always the first slot.
    y = int(size.height * 0.30)
    x = random.randint(int(size.width * 0.10), int(size.width * 0.35))
    _log(log_cb, f"[CHR] SERP không parse được, fallback tap kết quả #1 @({x},{y})")
    tap(adb, serial, x, y)
    return True


def _browse_landing(
    adb: ADB,
    serial: str,
    size: Size,
    seconds: float,
    stop_event: Optional[threading.Event],
    log_cb=None,
) -> None:
    """Pretend-read the article page for `seconds`.

    Watchdog ensure_portrait() runs each iter because Naver / news pages
    embed HTML5 video players that auto-fullscreen on tap and request
    landscape. The mid-page tap target is pinned to 35% height instead
    of dead-centre so a stray tap is less likely to land on a video
    poster (typically the upper third of an article).
    """
    end = time.time() + seconds
    _log(log_cb, f"[CHR] Lướt trang ~{seconds:.0f}s")
    last_was_up = True
    while time.time() < end:
        # Watchdog: if the landing page rotated us, snap back to portrait
        # before swipe coordinates become meaningless.
        ensure_portrait(adb, serial, log_cb=log_cb)
        # Recover from ad-redirect: Naver / SERP ads occasionally launch
        # Google Play or an external browser via intent:// scheme.
        ensure_app_foreground(adb, serial, CHROME_PKG, log_cb=log_cb)

        # Reading pause
        nap = min(end - time.time(), random.uniform(5.0, 18.0))
        if interruptible_sleep(stop_event, max(0.5, nap)):
            return
        if random.random() < 0.75:
            swipe_up(adb, serial, size)
            last_was_up = True
        else:
            swipe_down(adb, serial, size)
            last_was_up = False
        if interruptible_sleep(stop_event, lognormal_sleep(0.6, 0.4, 0.5, 2.0)):
            return
        # Occasionally tap mid-page (like reaching for a link, then back).
        # 35% height keeps the tap below most article hero-images / video
        # posters but above ads injected at the fold.
        if random.random() < 0.10:
            tap(adb, serial, int(size.width * 0.5), int(size.height * 0.35))
            if interruptible_sleep(stop_event, lognormal_sleep(0.6, 0.4, 0.6, 2.0)):
                return
            back(adb, serial)
            if interruptible_sleep(stop_event, lognormal_sleep(0.5, 0.3, 0.4, 1.5)):
                return


def run_chrome_task(
    adb: ADB,
    serial: str,
    keyword: str,
    *,
    prefer_domain: Optional[str] = None,
    watch_min_sec: float = 60.0,
    watch_max_sec: float = 300.0,
    stop_event: Optional[threading.Event] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """One Chrome cycle for `keyword`.

    If `prefer_domain` is set (e.g. "news.naver.com"), the SERP-result picker
    only taps a result whose visible text contains that domain. If we scroll
    the SERP twice and still can't find it, we open `https://<domain>/`
    directly so the loop still ends with the user "viewing" that site.
    """
    if _cancelled(stop_event):
        return False
    if not _open_search_url(adb, serial, keyword, log_cb):
        return False
    size = get_size(adb, serial)
    _log(
        log_cb,
        f"[CHR] Bắt đầu task keyword={keyword!r} size={size.width}x{size.height}"
        + (f" prefer={prefer_domain}" if prefer_domain else ""),
    )
    _scroll_serp(adb, serial, size, log_cb)
    if _cancelled(stop_event):
        return True

    picked = _pick_serp_result(adb, serial, size, prefer_domain=prefer_domain, log_cb=log_cb)

    # When prefer_domain doesn't match on first SERP view, scroll more and retry once.
    if not picked and prefer_domain and not _cancelled(stop_event):
        _scroll_serp(adb, serial, size, log_cb)
        picked = _pick_serp_result(adb, serial, size, prefer_domain=prefer_domain, log_cb=log_cb)

    # Still nothing → open the domain directly so the session is meaningful.
    if not picked and prefer_domain:
        domain = _normalize_domain(prefer_domain)
        url = f"https://{domain}/"
        _log(log_cb, f"[CHR] Mở trực tiếp {url} (không tìm thấy {domain!r} trên Google)")
        if not _open_chrome_url(adb, serial, url, log_cb):
            return False

    time.sleep(lognormal_sleep(1.0, 0.5, 1.8, 4.5))
    seconds = random.uniform(watch_min_sec, watch_max_sec)
    _browse_landing(adb, serial, size, seconds, stop_event, log_cb)
    back(adb, serial)  # back to SERP
    time.sleep(lognormal_sleep(0.5, 0.4, 0.5, 1.8))
    return True
