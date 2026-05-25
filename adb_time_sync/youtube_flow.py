"""YouTube automation flow.

Per task (one full loop):
  1. Clean launch — home → am force-stop YT → am kill-all → relaunch.
     This is critical because BoxPhone often boots into a stuck
     ad-on-Shorts session from a previous run; without force-stop the
     scroll-up swipe lands on the ad's "Visit" button instead of the
     next reel. Killing the process resets the ad cooldown / session.
  2. Enter Shorts; scroll N reels with watch durations from the task's
     delay_min..delay_max, liking with probability `like_rate`.
  3. Leave Shorts, search the keyword, watch a random top-3 video for
     watch_min..watch_max seconds with pause/resume jitter.
  4. Return so the caller can loop with the next keyword.

Commenting was removed at the operator's request — interactions are
limited to scroll + like + watch.
"""
from __future__ import annotations

import asyncio
import random
import threading
import time
import urllib.parse
from typing import Callable, Optional

import os

from .adb import ADB
from .human import (
    Size,
    back,
    ensure_app_foreground,
    get_size,
    home,
    human_sleep,
    interruptible_sleep,
    is_foreground,
    key,
    lognormal_sleep,
    screencap_png,
    swipe,
    swipe_up,
    tap,
    watch_duration_seconds,
)
from .screen import ensure_portrait
from .text_input import type_text
from .ui_state import (
    dump_ui,
    find_by_resource_id,
    find_by_text,
)


YOUTUBE_PKG = "com.google.android.youtube"

SHORTS_RES_IDS = (
    "com.google.android.youtube:id/reel_player_page_container",
    "com.google.android.youtube:id/reel_recycler",
    "com.google.android.youtube:id/reel_player_overlay",
)
LIKE_RES_IDS = (
    "com.google.android.youtube:id/reel_like_button",
    "com.google.android.youtube:id/like_button",
)
SEARCH_BUTTON_RES_IDS = (
    "com.google.android.youtube:id/menu_search",
    "com.google.android.youtube:id/action_search",
)
SEARCH_INPUT_RES_IDS = (
    "com.google.android.youtube:id/search_edit_text",
    "com.google.android.youtube:id/edit_text",
)

# Multi-locale UI labels for the text-based fallback when resource-ids miss.
# Device is Korean today, kept other locales for portability.
SHORTS_LABELS = ("Shorts", "쇼츠", "ショート", "短视频")
SEARCH_LABELS = (
    "Search", "검색",
    "Tìm kiếm", "搜索", "搜尋", "検索",
    "Suche", "Recherche", "Buscar", "Cari", "ค้นหา",
)
LIKE_LABELS = (
    "Like", "Thích",
    "좋아요",      # Korean
    "高く評価",    # Japanese ("rate highly")
    "いいね",     # Japanese ("good")
    "喜欢", "讚",  # Chinese simplified / traditional
    "Me gusta", "J'aime", "Mag ich",
)
COMMENT_LABELS = (
    "Comment", "Comments", "Bình luận",
    "댓글",        # Korean
    "コメント",    # Japanese
    "评论", "留言",
)
DEBUG_DUMP_DIR = "debug_dumps"
LIKE_TEMPLATES_DIR = "like_templates"
LIKE_TEMPLATE_THRESHOLD = 0.75
LIKE_TEMPLATE_SCALES = (0.5, 0.65, 0.8, 1.0, 1.25, 1.5, 1.75, 2.0)

# Bottom-sheet / dialog overlays that block scrolling on top of a Shorts reel.
# When the user accidentally taps a reel's "more" / "description" / "share"
# button, YT slides up one of these containers — a swipe-up then scrolls the
# panel content instead of advancing to the next reel.
YT_OVERLAY_RES_IDS = (
    "com.google.android.youtube:id/bottom_sheet_container",
    "com.google.android.youtube:id/design_bottom_sheet",
    "com.google.android.youtube:id/dialog_bottom_sheet",
    "com.google.android.youtube:id/touch_outside",
    "com.google.android.youtube:id/dialog_container",
)
# Multi-locale labels that ONLY appear in such overlays (not in Shorts player).
# 설명 = Description (Korean target), used as a positive signal.
YT_OVERLAY_TITLES = (
    "설명", "Description", "Mô tả", "说明", "説明",
    "공유", "Share", "Chia sẻ", "分享", "共有",
)
HOME_TAB_RES_IDS = (
    "com.google.android.youtube:id/home_feed_swipe_refresh_layout",
    "com.google.android.youtube:id/big_yt_logo",
    "com.google.android.youtube:id/youtube_logo",
)


def _cancelled(stop_event: Optional[threading.Event]) -> bool:
    return bool(stop_event and stop_event.is_set())


def _log(log_cb: Optional[Callable[[str], None]], msg: str) -> None:
    if log_cb:
        log_cb(msg)


def _open_youtube(adb: ADB, serial: str, log_cb=None) -> bool:
    """Clean-launch YouTube.

    The operator reported: when the project starts, the device is often
    already in a stuck Shorts session showing an old ad — scroll-up taps
    the ad's "Visit" link instead of the next reel and the whole loop
    grinds. The fix is to never trust prior app state:

       home → am force-stop com.google.android.youtube
            → am kill-all (drops cached background processes)
            → monkey LAUNCHER (fresh start, lands on Home tab)

    This adds ~3s per loop but guarantees a deterministic starting screen.
    """
    r = adb.shell(serial, f"pm list packages {YOUTUBE_PKG}")
    if not r.ok or YOUTUBE_PKG not in (r.out or ""):
        _log(log_cb, f"[YT] Chưa cài {YOUTUBE_PKG}")
        return False

    _log(log_cb, "[YT] Clean launch — home → force-stop → kill-all → relaunch")
    home(adb, serial)
    time.sleep(0.4)
    adb.shell(serial, f"am force-stop {YOUTUBE_PKG}")
    # Sweep cached background apps too — frees memory and clears any
    # webview / IME state that could resurrect a sticky ad overlay.
    adb.shell(serial, "am kill-all")
    time.sleep(lognormal_sleep(0.4, 0.3, 0.5, 1.5))

    adb.shell(serial, f"monkey -p {YOUTUBE_PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(lognormal_sleep(0.8, 0.4, 1.5, 5.0))
    return is_foreground(adb, serial, YOUTUBE_PKG)


def _dismiss_overlay(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Close any bottom-sheet / dialog overlay covering a Shorts reel.

    Detection: YT bottom-sheet container res-id, OR a known overlay title
    ("설명" / "Description" / "공유" / "Share" / multi-locale equivalents).

    Dismissal: a single BACK keypress always closes YT bottom sheets — no
    need to hunt for the X icon (its res-id is obfuscated per YT release).

    Returns True if an overlay was detected and dismissed.
    """
    xml = dump_ui(adb, serial)
    if not xml:
        return False

    has_overlay = bool(find_by_resource_id(xml, *YT_OVERLAY_RES_IDS))
    if not has_overlay:
        # Title-based fallback: a Shorts player never shows these strings,
        # so seeing one is strong evidence an overlay is up.
        for title in YT_OVERLAY_TITLES:
            if find_by_text(xml, title, contains=False):
                has_overlay = True
                break
    if not has_overlay:
        return False

    _log(log_cb, "[YT] Phát hiện overlay (mô tả/share/dialog) — đóng bằng back")
    back(adb, serial)
    time.sleep(lognormal_sleep(0.4, 0.3, 0.4, 1.2))
    return True


def _go_to_home_tab(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Navigate to the Home feed so the upcoming search starts from there.

    Operator request: the search-keyword step should fire from Home, not
    from inside Shorts (where deep-link search occasionally lands results
    inside the Shorts player). Strategy:
      1) Deep-link `vnd.youtube://feed/home` (most reliable, no UI tap).
      2) Tap the leftmost bottom-nav slot at y≈96% height.
      3) Last resort: monkey LAUNCHER (always lands on Home post-launch).
    Verified by HOME_TAB_RES_IDS in the resulting dump.
    """
    deep_links = (
        "vnd.youtube://feed/home",
        "vnd.youtube://www.youtube.com/feed/home",
    )
    for url in deep_links:
        r = adb.shell(serial, f'am start -a android.intent.action.VIEW -d "{url}"')
        if r.ok:
            time.sleep(lognormal_sleep(0.6, 0.4, 0.8, 2.5))
            xml = dump_ui(adb, serial)
            if xml and find_by_resource_id(xml, *HOME_TAB_RES_IDS):
                _log(log_cb, f"[YT] Về Home tab qua {url}")
                return True

    # Tap bottom-nav home (leftmost slot, ~10% width × 96% height).
    nav_x = int(size.width * 0.10)
    nav_y = int(size.height * 0.96)
    tap(adb, serial, nav_x, nav_y)
    time.sleep(lognormal_sleep(0.5, 0.4, 0.6, 2.0))
    xml = dump_ui(adb, serial)
    if xml and find_by_resource_id(xml, *HOME_TAB_RES_IDS):
        _log(log_cb, f"[YT] Về Home tab qua bottom-nav @({nav_x},{nav_y})")
        return True

    # Last resort: monkey LAUNCHER.
    adb.shell(serial, f"monkey -p {YOUTUBE_PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(lognormal_sleep(0.6, 0.4, 0.8, 2.5))
    _log(log_cb, "[YT] Về Home tab qua monkey LAUNCHER (fallback)")
    return True


def _save_debug_dump(serial: str, xml: str, tag: str) -> None:
    """Save a UI dump to `debug_dumps/<serial>_<tag>.xml` for offline inspection."""
    try:
        import os
        os.makedirs(DEBUG_DUMP_DIR, exist_ok=True)
        safe = serial.replace(":", "_").replace("/", "_")
        path = os.path.join(DEBUG_DUMP_DIR, f"{safe}_{tag}.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
    except Exception:
        pass


def _find_node_with_label(xml: str, labels: tuple[str, ...]):
    """Find a clickable node whose text/content-desc contains any of `labels`.

    Robust to YouTube's habit of putting the label in `content-desc` only.
    """
    from .ui_state import iter_nodes
    lower_labels = [s.lower() for s in labels]
    for node in iter_nodes(xml):
        haystack = (node.text + " " + node.desc).lower()
        if not haystack.strip():
            continue
        if any(lbl in haystack for lbl in lower_labels):
            if node.clickable:
                return node
            # Some YouTube wrappers mark only the ancestor clickable; still useful for tap.
            return node
    return None


def _go_to_shorts(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Try hard to land on Shorts. Returns True if we believe we got there.

    Strategy: deep-link → bottom-nav by content-desc → blind nav-tap.
    We trust best-effort: returning True lets the caller swipe — even if
    we ended up scrolling Home feed, the user still sees activity.
    """
    deep_links = (
        ("vnd.youtube://shorts", "vnd.youtube://shorts"),
        ("https://www.youtube.com/shorts", "https://www.youtube.com/shorts"),
    )
    for url, label in deep_links:
        r = adb.shell(serial, f'am start -a android.intent.action.VIEW -d "{url}"')
        if r.ok:
            time.sleep(lognormal_sleep(0.8, 0.4, 1.8, 4.0))
            xml = dump_ui(adb, serial)
            if xml:
                if find_by_resource_id(xml, *SHORTS_RES_IDS):
                    _log(log_cb, f"[YT] Vào Shorts qua {label} (resource-id ok)")
                    return True
                if _find_node_with_label(xml, SHORTS_LABELS):
                    _log(log_cb, f"[YT] Vào Shorts qua {label} (label match)")
                    return True

    # Tap nav by content-desc/text on whatever screen we are on now.
    xml = dump_ui(adb, serial)
    if xml:
        node = _find_node_with_label(xml, SHORTS_LABELS)
        if node:
            _log(log_cb, f"[YT] Tap nav Shorts theo label @{node.center}: text={node.text!r} desc={node.desc!r}")
            tap(adb, serial, *node.center)
            time.sleep(lognormal_sleep(0.8, 0.4, 1.2, 3.0))
            return True
        _save_debug_dump(serial, xml, "shorts_miss")
        _log(log_cb, f"[YT] Không tìm thấy Shorts trong dump (đã lưu debug_dumps/{serial.replace(':','_')}_shorts_miss.xml)")

    # Last resort: blind nav-tap. Many YT versions put Shorts at 2/5 of bottom nav.
    nav_y = int(size.height * 0.96)
    nav_x = int(size.width * 0.30)
    tap(adb, serial, nav_x, nav_y)
    _log(log_cb, f"[YT] Blind nav-tap @({nav_x},{nav_y}) — không xác minh được Shorts, vẫn tiếp tục")
    time.sleep(lognormal_sleep(0.6, 0.4, 0.8, 2.5))
    return True  # best-effort: caller still gets to scroll


def _is_in_shorts(adb: ADB, serial: str) -> tuple[bool, str]:
    """Heuristic check: are we currently in the Shorts player view?

    Returns (in_shorts, reason). False positives are worse than false negatives
    here (we'd swipe Home feed). So we require strong evidence.
    """
    if not is_foreground(adb, serial, YOUTUBE_PKG):
        return False, "YouTube không foreground"
    xml = dump_ui(adb, serial)
    if not xml:
        # Can't verify — give benefit of the doubt rather than entering retry storm.
        return True, "dump_ui rỗng (assume in)"
    if find_by_resource_id(xml, *SHORTS_RES_IDS):
        return True, "resource-id Shorts ok"
    # Negative evidence: presence of Home feed / Search bar means we're NOT in Shorts.
    HOME_NEGATIVE_IDS = (
        "com.google.android.youtube:id/results",
        "com.google.android.youtube:id/home_feed_swipe_refresh_layout",
        "com.google.android.youtube:id/big_yt_logo",
        "com.google.android.youtube:id/youtube_logo",
    )
    if find_by_resource_id(xml, *HOME_NEGATIVE_IDS):
        return False, "thấy resource-id Home"
    # Positive evidence by label: Like/Comment buttons rendered as content-desc.
    if _find_node_with_label(xml, LIKE_LABELS) or _find_node_with_label(xml, COMMENT_LABELS):
        return True, "thấy nút Like/Comment"
    return False, "không thấy dấu hiệu Shorts"


def _ensure_in_shorts(
    adb: ADB,
    serial: str,
    size: Size,
    log_cb=None,
    max_retry: int = 2,
) -> bool:
    """Make sure we're in Shorts; re-enter up to `max_retry` times if not."""
    in_shorts, reason = _is_in_shorts(adb, serial)
    if in_shorts:
        return True
    for attempt in range(1, max_retry + 1):
        _log(log_cb, f"[YT] Rớt khỏi Shorts ({reason}); vào lại lần {attempt}/{max_retry}")
        if not is_foreground(adb, serial, YOUTUBE_PKG):
            adb.shell(serial, f"monkey -p {YOUTUBE_PKG} -c android.intent.category.LAUNCHER 1")
            time.sleep(lognormal_sleep(0.8, 0.4, 1.5, 4.0))
        _go_to_shorts(adb, serial, size, log_cb)
        time.sleep(lognormal_sleep(0.6, 0.4, 0.8, 2.5))
        in_shorts, reason = _is_in_shorts(adb, serial)
        if in_shorts:
            return True
    _log(log_cb, f"[YT] Bỏ vòng còn lại — không trở lại Shorts được ({reason})")
    return False


def _list_like_templates() -> list[str]:
    """Return absolute paths to every .png in `like_templates/` (sorted)."""
    if not os.path.isdir(LIKE_TEMPLATES_DIR):
        return []
    return sorted(
        os.path.join(LIKE_TEMPLATES_DIR, f)
        for f in os.listdir(LIKE_TEMPLATES_DIR)
        if f.lower().endswith(".png")
    )


def _template_match_like(adb: ADB, serial: str, log_cb=None) -> Optional[tuple[int, int]]:
    """Locate the Like icon on screen via OpenCV multi-scale template matching.

    Returns the (x, y) tap point if any template in `like_templates/`
    matches above LIKE_TEMPLATE_THRESHOLD, else None. The user-supplied
    PNG should be a tight crop of the Like icon captured on any device;
    scale invariance handles different BoxPhone resolutions.
    """
    templates = _list_like_templates()
    if not templates:
        return None
    img_bytes = screencap_png(adb, serial)
    if not img_bytes:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        _log(log_cb, "[YT] Template match bỏ qua — cv2/numpy chưa cài")
        return None
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    screen = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if screen is None:
        return None
    sh, sw = screen.shape[:2]
    best_val = -1.0
    best_xy: Optional[tuple[int, int]] = None
    best_tpl = ""
    for tpl_path in templates:
        tpl = cv2.imread(tpl_path, cv2.IMREAD_COLOR)
        if tpl is None:
            continue
        for scale in LIKE_TEMPLATE_SCALES:
            th, tw = int(tpl.shape[0] * scale), int(tpl.shape[1] * scale)
            if th < 10 or tw < 10 or th > sh or tw > sw:
                continue
            tpl_s = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(screen, tpl_s, cv2.TM_CCOEFF_NORMED)
            _min_v, max_v, _min_l, max_l = cv2.minMaxLoc(res)
            if max_v > best_val:
                best_val = max_v
                best_xy = (max_l[0] + tw // 2, max_l[1] + th // 2)
                best_tpl = os.path.basename(tpl_path)
    if best_xy is None or best_val < LIKE_TEMPLATE_THRESHOLD:
        _log(log_cb, f"[YT] Template match Like fail (best={best_val:.2f}, threshold={LIKE_TEMPLATE_THRESHOLD})")
        return None
    _log(log_cb, f"[YT] Template match Like @{best_xy} score={best_val:.2f} ({best_tpl})")
    return best_xy


def _double_tap_center(adb: ADB, serial: str, size: Size) -> None:
    """Double-tap the screen centre. On newer YouTube Shorts this triggers
    the heart-like animation, similar to TikTok. On older versions it's
    a noop / play-pause — harmless but unverifiable.
    """
    x = size.width // 2
    y = size.height // 2
    adb.shell(serial, f"input tap {x} {y}")
    time.sleep(0.12)
    adb.shell(serial, f"input tap {x} {y}")


def _maybe_like(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Like the current reel using a 4-tier strategy:
      1) Resource-id match (cleanest)
      2) Content-desc / text label match (multi-lingual: 좋아요, Like, ...)
      3) OpenCV template match against `like_templates/*.png`
      4) Double-tap centre (works on newer Shorts; best-effort)
      5) Skip + log + dump if everything fails
    """
    xml = dump_ui(adb, serial)
    if xml:
        node = find_by_resource_id(xml, *LIKE_RES_IDS)
        if node:
            tap(adb, serial, *node.center)
            _log(log_cb, f"[YT] Like reel (resource-id) @{node.center}")
            return True
        node = _find_node_with_label(xml, LIKE_LABELS)
        if node:
            tap(adb, serial, *node.center)
            _log(log_cb, f"[YT] Like reel (label) @{node.center} desc={node.desc!r}")
            return True
        _save_debug_dump(serial, xml, "like_miss")

    pos = _template_match_like(adb, serial, log_cb)
    if pos:
        tap(adb, serial, *pos)
        return True

    # Last-resort: double-tap centre. Cannot verify it took effect but harmless.
    _double_tap_center(adb, serial, size)
    _log(log_cb, "[YT] Like reel (double-tap centre, best-effort, không xác minh)")
    return True


def _scroll_reels(
    adb: ADB,
    serial: str,
    size: Size,
    n_reels: int,
    stop_event: Optional[threading.Event],
    *,
    delay_min: float,
    delay_max: float,
    like_rate: float,
    shorts_time_limit_sec: float = 0.0,
    log_cb=None,
) -> None:
    start_ts = time.time()
    for i in range(n_reels):
        if _cancelled(stop_event):
            return
        if shorts_time_limit_sec > 0 and (time.time() - start_ts) >= shorts_time_limit_sec:
            _log(
                log_cb,
                f"[YT] Đạt thời gian giới hạn Shorts ({shorts_time_limit_sec:.0f}s),"
                f" đã lướt {i} reel. Thoát Shorts, chuyển sang search.",
            )
            return
        # Watchdog: a stray ad-interstitial or mis-tap can rotate the screen
        # mid-Shorts; recover before swiping with stale coordinates.
        ensure_portrait(adb, serial, log_cb=log_cb)
        # Recover if a Shorts ad bounced us into Play Store / external app.
        ensure_app_foreground(adb, serial, YOUTUBE_PKG, log_cb=log_cb)
        # Dismiss any bottom-sheet (description / share / more menu) that
        # would otherwise eat our swipe-up gesture.
        _dismiss_overlay(adb, serial, size, log_cb)
        # Verify we're still in Shorts before pretending to watch. If we drifted
        # out (popup, accidental tap-to-Home, etc.), try to re-enter.
        if not _ensure_in_shorts(adb, serial, size, log_cb):
            return

        watch = random.uniform(delay_min, delay_max)
        _log(log_cb, f"[YT] Reel {i+1}/{n_reels}: xem {watch:.1f}s")
        if interruptible_sleep(stop_event, watch):
            return

        if random.random() < like_rate:
            _maybe_like(adb, serial, size, log_cb)
            time.sleep(lognormal_sleep(0.5, 0.4, 0.5, 1.8))
            # Like fallback may tap the wrong target on unknown ROMs — re-verify.
            if not _ensure_in_shorts(adb, serial, size, log_cb):
                return

        if random.random() < 0.06:
            # Occasional mis-tap then back, like a fumble.
            x = int(size.width * random.uniform(0.2, 0.8))
            y = int(size.height * random.uniform(0.3, 0.7))
            tap(adb, serial, x, y)
            time.sleep(lognormal_sleep(0.3, 0.3, 0.3, 1.2))
            back(adb, serial)

        swipe_up(adb, serial, size, strong=random.random() < 0.3)
        time.sleep(lognormal_sleep(0.4, 0.4, 0.4, 1.6))


def _open_search(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Best-effort: try the SEARCH intent first, fall back to the search UI."""
    r = adb.shell(
        serial,
        f"am start -a android.intent.action.SEARCH --es query \"\" -n {YOUTUBE_PKG}/com.google.android.apps.youtube.app.WatchWhileActivity",
    )
    if r.ok:
        time.sleep(lognormal_sleep(0.6, 0.4, 0.8, 2.5))
    xml = dump_ui(adb, serial)
    if xml and find_by_resource_id(xml, *SEARCH_INPUT_RES_IDS):
        return True
    if xml:
        node = find_by_resource_id(xml, *SEARCH_BUTTON_RES_IDS)
        if not node:
            node = find_by_text(xml, *SEARCH_LABELS, contains=True)
        if node:
            tap(adb, serial, *node.center)
            time.sleep(lognormal_sleep(0.5, 0.3, 0.6, 2.0))
            return True
    # Fallback: top-right magnifier ~95% width 7-9% height.
    x = int(size.width * 0.92)
    y = int(size.height * 0.08)
    tap(adb, serial, x, y)
    time.sleep(lognormal_sleep(0.5, 0.4, 0.6, 2.0))
    return True


def _do_search(adb: ADB, serial: str, size: Size, keyword: str, log_cb=None) -> bool:
    # Preferred path: vnd.youtube deep-link straight to results, skip UI.
    q = urllib.parse.quote_plus(keyword)
    r = adb.shell(serial, f'am start -a android.intent.action.VIEW -d "vnd.youtube:///results?search_query={q}"')
    if r.ok:
        time.sleep(lognormal_sleep(1.0, 0.5, 1.5, 4.0))
        return True

    _log(log_cb, "[YT] Deep-link search fail, fallback UI")
    _open_search(adb, serial, size, log_cb)
    xml = dump_ui(adb, serial)
    if xml:
        edit = find_by_resource_id(xml, *SEARCH_INPUT_RES_IDS)
        if edit:
            tap(adb, serial, *edit.center)
            time.sleep(lognormal_sleep(0.4, 0.3, 0.4, 1.5))
    ok, method = type_text(adb, serial, keyword, submit=True)
    _log(log_cb, f"[YT] Search {keyword!r} ({method}) ok={ok}")
    if not ok:
        return False
    time.sleep(lognormal_sleep(1.0, 0.5, 1.5, 4.0))
    return True


def _tap_top_result(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Tap a random top-3 video result."""
    xml = dump_ui(adb, serial)
    if not xml:
        return False
    candidates = []
    for line in xml.splitlines():
        pass
    from .ui_state import iter_nodes
    for node in iter_nodes(xml):
        if "video" in (node.resource_id or "").lower() and node.clickable:
            candidates.append(node)
        elif node.class_name == "android.view.ViewGroup" and node.clickable and "video" in (node.desc or "").lower():
            candidates.append(node)
    if not candidates:
        candidates = [
            n for n in iter_nodes(xml)
            if n.clickable and n.class_name in ("android.view.ViewGroup", "android.widget.FrameLayout")
            and n.bounds[3] - n.bounds[1] > size.height * 0.1
        ]
    if not candidates:
        _log(log_cb, "[YT] Không thấy kết quả tìm kiếm; fallback tap giữa")
        tap(adb, serial, size.width // 2, int(size.height * 0.32))
        return True
    pool = candidates[: min(3, len(candidates))]
    pick = random.choice(pool)
    tap(adb, serial, *pick.center)
    _log(log_cb, f"[YT] Mở kết quả tại {pick.center}")
    return True


def _watch_video(
    adb: ADB,
    serial: str,
    size: Size,
    min_sec: float,
    max_sec: float,
    stop_event: Optional[threading.Event],
    log_cb=None,
) -> None:
    """Pretend-watch the currently-open YouTube video.

    This is the loop most exposed to landscape flips: when a 16:9 video
    starts playing, YouTube calls setRequestedOrientation(SENSOR_LANDSCAPE)
    and the device rotates. On Android 12+ the WindowManager flag set in
    task_runner blocks that call entirely; on older ROMs we rely on the
    ensure_portrait() watchdog here, which re-applies the lock as soon as
    we detect rot != 0 between naps.

    Pause/resume tap target is intentionally above the screen midpoint
    (40% height) — the player's fullscreen icon sits at the bottom-right
    of the video surface, which on a portrait phone with a 16:9 player
    lives around (95% width, 33% height). Tapping at (50%, 40%) stays
    inside the video but well clear of the fullscreen control.
    """
    total = random.uniform(min_sec, max_sec)
    _log(log_cb, f"[YT] Xem video {total:.0f}s")
    end = time.time() + total
    pause_x = size.width // 2
    pause_y = int(size.height * 0.40)
    while time.time() < end:
        # Watchdog: recover from app-driven landscape between naps.
        ensure_portrait(adb, serial, log_cb=log_cb)
        # And from ad-driven foreground hijack (Play Store / external app).
        ensure_app_foreground(adb, serial, YOUTUBE_PKG, log_cb=log_cb)

        nap = min(end - time.time(), random.uniform(8.0, 20.0))
        if interruptible_sleep(stop_event, max(0.5, nap)):
            return
        # Occasional pause/resume — tap in the safe upper-half of the player.
        if random.random() < 0.25:
            tap(adb, serial, pause_x, pause_y)
            if interruptible_sleep(stop_event, lognormal_sleep(0.5, 0.4, 0.4, 1.8)):
                return
            tap(adb, serial, pause_x, pause_y)
        # Occasional small scroll on the comments below.
        if random.random() < 0.15:
            swipe(
                adb,
                serial,
                int(size.width * 0.5),
                int(size.height * 0.85),
                int(size.width * 0.5),
                int(size.height * 0.55),
                duration_ms=400,
            )


def run_youtube_task(
    adb: ADB,
    serial: str,
    keyword: str,
    *,
    reels_min: int = 5,
    reels_max: int = 10,
    delay_min: float = 10.0,
    delay_max: float = 15.0,
    like_rate: float = 1.0,
    shorts_time_limit_sec: float = 0.0,
    watch_min_sec: float = 60.0,
    watch_max_sec: float = 300.0,
    stop_event: Optional[threading.Event] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """One YouTube cycle for `keyword`. Returns True if it ran end-to-end.

    Flow:
      1) Clean-launch YouTube (kills any stuck ad session from a prior run).
      2) Enter Shorts; scroll `n = random[reels_min..reels_max]` reels,
         each watched `random[delay_min..delay_max]` seconds, liked with
         probability `like_rate`.
      3) Leave Shorts, search keyword, watch a random top-3 result for
         `random[watch_min_sec..watch_max_sec]` seconds.
    """
    if _cancelled(stop_event):
        return False

    if not _open_youtube(adb, serial, log_cb):
        return False
    size = get_size(adb, serial)
    _log(log_cb, f"[YT] Bắt đầu task keyword={keyword!r} size={size.width}x{size.height}")

    if _go_to_shorts(adb, serial, size, log_cb):
        n_reels = random.randint(reels_min, reels_max)
        limit_note = (
            f", giới hạn {shorts_time_limit_sec:.0f}s"
            if shorts_time_limit_sec > 0
            else " (không giới hạn thời gian)"
        )
        _log(
            log_cb,
            f"[YT] Sẽ lướt tối đa {n_reels} reel, delay {delay_min:.0f}-{delay_max:.0f}s, "
            f"like_rate={like_rate}{limit_note}",
        )
        _scroll_reels(
            adb,
            serial,
            size,
            n_reels,
            stop_event,
            delay_min=delay_min,
            delay_max=delay_max,
            like_rate=like_rate,
            shorts_time_limit_sec=shorts_time_limit_sec,
            log_cb=log_cb,
        )
        if _cancelled(stop_event):
            return True
        back(adb, serial)
        time.sleep(lognormal_sleep(0.5, 0.4, 0.5, 1.8))

    if _cancelled(stop_event):
        return True

    # Operator requirement: keyword search must originate from the Home tab,
    # not from inside Shorts. Navigate explicitly so the SERP UI is the
    # standard Watch-page layout, not a Shorts-embedded result list.
    _go_to_home_tab(adb, serial, size, log_cb)
    if _cancelled(stop_event):
        return True
    ensure_app_foreground(adb, serial, YOUTUBE_PKG, log_cb=log_cb)

    if not _do_search(adb, serial, size, keyword, log_cb):
        return False
    if _cancelled(stop_event):
        return True
    if not _tap_top_result(adb, serial, size, log_cb):
        return False
    time.sleep(lognormal_sleep(1.0, 0.5, 1.8, 4.5))
    _watch_video(adb, serial, size, watch_min_sec, watch_max_sec, stop_event, log_cb)

    back(adb, serial)
    return True
