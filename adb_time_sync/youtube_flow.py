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
# Covers TWO distinct overlays seen in the wild:
#   A. Description bottom-sheet (title = "설명" / "Description" + body text).
#   B. "More options" bottom-sheet (Shorts ⋮ menu): a list of menu items
#      including 설명 / 재생목록에 저장 / 자막 / 화질 / 관심 없음 / 채널 추천 안함 / 신고 / 의견 보내기.
# Detecting ANY of these labels in the dump is a strong positive signal —
# none of them ever appear in the regular Shorts player surface.
YT_OVERLAY_TITLES = (
    # Description
    "설명", "Description", "Mô tả", "说明", "説明",
    # Share
    "공유", "Share", "Chia sẻ", "分享", "共有",
    # More-options menu items (Korean target ROM)
    "재생목록에 저장",     # Save to playlist
    "관심 없음",          # Not interested  (DANGEROUS if tapped — demotes channel)
    "채널 추천 안함",      # Don't recommend channel
    "신고",               # Report
    "의견 보내기",         # Send feedback
    # English equivalents for non-Korean ROMs
    "Save to playlist",
    "Not interested",
    "Don't recommend channel",
    "Report",
    "Send feedback",
    # Vietnamese equivalents
    "Lưu vào danh sách phát",
    "Không quan tâm",
    "Không đề xuất kênh này",
    "Báo cáo",
    "Gửi phản hồi",
)
HOME_TAB_RES_IDS = (
    "com.google.android.youtube:id/home_feed_swipe_refresh_layout",
    "com.google.android.youtube:id/big_yt_logo",
    "com.google.android.youtube:id/youtube_logo",
)
SERP_RESULTS_RES_IDS = (
    "com.google.android.youtube:id/results",
    "com.google.android.youtube:id/search_results",
)
PLAYER_RES_IDS = (
    "com.google.android.youtube:id/player_view",
    "com.google.android.youtube:id/watch_player",
    "com.google.android.youtube:id/player_overlay",
    "com.google.android.youtube:id/floaty_bar",
    "com.google.android.youtube:id/player_fragment_container",
)
# Same ad copy as Chrome SERP — same Google ad inventory shows on YT.
YT_AD_LABELS = (
    "광고", "스폰서",
    "sponsored", "ad ·", "ads ·", "promoted",
    "quảng cáo", "được tài trợ",
    "广告", "赞助商",
    "広告", "スポンサー",
)
# "Skip Ad" button copy on YouTube ads. Layout seen on Korean BoxPhone:
# a small rounded chip sitting on the right edge of the player at ~50%
# height showing "건너뛰기" (Skip) once the ad's skippable window opens.
SKIP_AD_LABELS = (
    "건너뛰기", "광고 건너뛰기",
    "Skip", "Skip ad", "Skip ads", "Skip Ad",
    "Bỏ qua", "Bỏ qua quảng cáo",
    "Saltar", "Saltar anuncio",
    "Ignorer", "Ignorer l'annonce",
    "跳过", "跳过广告",
    "スキップ", "広告をスキップ",
    "Überspringen",
)
SKIP_AD_RES_IDS = (
    "com.google.android.youtube:id/skip_ad_button",
    "com.google.android.youtube:id/skip_ad_button_text",
    "com.google.android.youtube:id/ad_skip_button",
    "com.google.android.youtube:id/skip_button",
)
# Close-X icons on app-install cards layered under Shorts ads.
AD_CARD_CLOSE_RES_IDS = (
    "com.google.android.youtube:id/close_button",
    "com.google.android.youtube:id/dismiss_button",
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
        # so seeing one is strong evidence an overlay is up. Use contains=True
        # to tolerate whitespace / mixed-case / partial matches in the menu.
        for title in YT_OVERLAY_TITLES:
            if find_by_text(xml, title, contains=True):
                has_overlay = True
                break
    if not has_overlay:
        return False

    _log(log_cb, "[YT] Phát hiện overlay (mô tả/share/menu) — đóng bằng back")
    back(adb, serial)
    time.sleep(lognormal_sleep(0.4, 0.3, 0.4, 1.2))
    return True


def _skip_ad_if_present(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Tap "건너뛰기" / "Skip Ad" / equivalent if YouTube is showing a
    skippable ad on top of a Shorts reel.

    Detection priority:
      1. Skip-ad button by resource-id (cleanest, but obfuscated on some YT builds).
      2. Skip-ad button by content-desc/text label (multi-locale).
      3. App-install card close-X by resource-id (for non-skippable ads
         that overlay the bottom half of Shorts with an install panel).

    Returns True if we tapped something. Caller can re-check Shorts presence.
    """
    xml = dump_ui(adb, serial)
    if not xml:
        return False

    # 1) Resource-id Skip button
    node = find_by_resource_id(xml, *SKIP_AD_RES_IDS)
    if node and node.clickable:
        tap(adb, serial, *node.center)
        _log(log_cb, f"[YT] Tap Skip Ad (res-id) @{node.center}")
        time.sleep(lognormal_sleep(0.5, 0.3, 0.5, 1.5))
        return True

    # 2) Label match — Skip button uses the visible string, not res-id, on
    #    many YT versions. Require clickable to avoid tapping a static label.
    node = _find_node_with_label(xml, SKIP_AD_LABELS)
    if node and node.clickable:
        tap(adb, serial, *node.center)
        _log(log_cb, f"[YT] Tap Skip Ad (label) @{node.center}: text={node.text!r} desc={node.desc!r}")
        time.sleep(lognormal_sleep(0.5, 0.3, 0.5, 1.5))
        return True

    # 3) Non-skippable app-install card → close X (frees the lower half of
    #    Shorts so swipe-up reaches the player area).
    if any(lbl in (xml or "") for lbl in YT_AD_LABELS):
        close = find_by_resource_id(xml, *AD_CARD_CLOSE_RES_IDS)
        if close and close.clickable:
            tap(adb, serial, *close.center)
            _log(log_cb, f"[YT] Đóng app-install card (res-id close) @{close.center}")
            time.sleep(lognormal_sleep(0.5, 0.3, 0.5, 1.5))
            return True

    return False


def _go_to_home_tab(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Navigate to YouTube's Home feed. CRUCIAL post-condition: we must be
    NEITHER in Shorts NOR in Watch view when this returns — the upcoming
    search must fire from a Home-feed context so the SERP renders as the
    standard Watch layout, not embedded inside the Shorts player.

    Operator report: even with the old version, search sometimes still
    landed in Shorts. Root cause was that the Shorts player overlay can
    stay visible on top while HOME_TAB_RES_IDS exist in the dump
    underneath, so the verify-by-resource-id passed but we were still
    in Shorts. Strengthened strategy:

      0) Aggressive back-out: press BACK up to 5 times until _is_in_shorts
         reports False AND we're not in Watch view (no PLAYER_RES_IDS).
      1) Deep-link feed/home (2 URL variants).
      2) Tap leftmost bottom-nav slot.
      3) Monkey LAUNCHER (last resort).

      After each attempt, verify Home AND not-in-Shorts before accepting.
    """

    def _at_home(xml_str: str) -> bool:
        """Strict Home check: Home res-id present, NO player res-id,
        and _is_in_shorts says no."""
        if not xml_str:
            return False
        if not find_by_resource_id(xml_str, *HOME_TAB_RES_IDS):
            return False
        if find_by_resource_id(xml_str, *PLAYER_RES_IDS):
            return False  # video player still up
        in_shorts, _reason = _is_in_shorts(adb, serial)
        return not in_shorts

    # Step 0 — aggressive back-out so subsequent nav isn't fighting Shorts.
    for i in range(5):
        in_shorts, _ = _is_in_shorts(adb, serial)
        if not in_shorts:
            break
        back(adb, serial)
        time.sleep(lognormal_sleep(0.3, 0.2, 0.3, 1.0))
    else:
        _log(log_cb, "[YT] Back 5 lần vẫn còn Shorts — sẽ ép qua deep-link")

    # Step 1 — deep-link.
    deep_links = (
        "vnd.youtube://feed/home",
        "vnd.youtube://www.youtube.com/feed/home",
    )
    for url in deep_links:
        r = adb.shell(serial, f'am start -a android.intent.action.VIEW -d "{url}"')
        if r.ok:
            time.sleep(lognormal_sleep(0.6, 0.4, 0.9, 2.5))
            xml = dump_ui(adb, serial)
            if _at_home(xml or ""):
                _log(log_cb, f"[YT] Về Home tab qua {url} (verified)")
                return True

    # Step 2 — bottom-nav home tap (leftmost slot ~10% × 96%).
    nav_x = int(size.width * 0.10)
    nav_y = int(size.height * 0.96)
    tap(adb, serial, nav_x, nav_y)
    time.sleep(lognormal_sleep(0.5, 0.4, 0.7, 2.0))
    xml = dump_ui(adb, serial)
    if _at_home(xml or ""):
        _log(log_cb, f"[YT] Về Home tab qua bottom-nav @({nav_x},{nav_y}) (verified)")
        return True

    # Step 3 — monkey LAUNCHER. This rebinds the activity stack to Home.
    adb.shell(serial, f"monkey -p {YOUTUBE_PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 3.0))
    xml = dump_ui(adb, serial)
    if _at_home(xml or ""):
        _log(log_cb, "[YT] Về Home tab qua monkey LAUNCHER (verified)")
        return True

    # Could not verify. Accept anyway — search will retry from wherever we are.
    _log(log_cb, "[YT] Không verify được Home tab; vẫn tiếp tục (search có thể chệch)")
    return False


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
    # Infinite mode: shorts_time_limit_sec == 0 means "scroll Reels forever
    # until the operator hits Cancel" — both the time cap AND the reel-count
    # cap are disabled. (Previously 0 meant "no time limit but still bounded
    # by reels_min/max", which confused operators who expected 0 = ∞.)
    infinite_mode = (shorts_time_limit_sec <= 0)
    if infinite_mode:
        _log(log_cb, "[YT] Reel mode = VÔ HẠN (giới hạn=0) — lướt tới khi Cancel")

    i = 0
    while True:
        if _cancelled(stop_event):
            return
        # Bounded mode: stop when either the reel count OR the time cap hits.
        if not infinite_mode:
            if i >= n_reels:
                return
            if (time.time() - start_ts) >= shorts_time_limit_sec:
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
        # Skip pre-roll / mid-roll ads ("건너뛰기" / "Skip") so swipe-up
        # doesn't waste the reel slot watching a sponsor video.
        _skip_ad_if_present(adb, serial, size, log_cb)
        # Dismiss any bottom-sheet (description / share / more menu) that
        # would otherwise eat our swipe-up gesture.
        _dismiss_overlay(adb, serial, size, log_cb)
        # Verify we're still in Shorts before pretending to watch. If we drifted
        # out (popup, accidental tap-to-Home, etc.), try to re-enter.
        if not _ensure_in_shorts(adb, serial, size, log_cb):
            return

        watch = random.uniform(delay_min, delay_max)
        label = f"{i+1}/∞" if infinite_mode else f"{i+1}/{n_reels}"
        _log(log_cb, f"[YT] Reel {label}: xem {watch:.1f}s")
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
        i += 1


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


# Server error / retry overlay markers (multi-locale). Substring match on
# the raw XML dump is cheap and good enough — these strings don't appear
# in normal Watch / SERP / Home dumps.
YT_ERROR_MARKERS = (
    "[400]", "[500]", "[503]",
    "서버에 문제",        # KR: server problem
    "Server error",
    "Something went wrong",
    "Có lỗi máy chủ",
)
# Retry button labels (multi-locale).
YT_RETRY_LABELS = (
    "다시 시도", "재시도",    # KR
    "Try again", "Retry",
    "Thử lại",
    "再試行",                # JP
    "重试",                  # CN
)


def _has_yt_error_screen(xml: str) -> bool:
    if not xml:
        return False
    low = xml.lower()
    return any(m.lower() in low for m in YT_ERROR_MARKERS)


def _tap_yt_retry(adb: ADB, serial: str, log_cb=None) -> bool:
    """If the YT error screen is up, tap its '다시 시도' / 'Try again' button.

    Returns True if we tapped a Retry control. The UI's retry chip is a
    clickable TextView whose visible text is one of YT_RETRY_LABELS — no
    stable resource-id, so we match by label.
    """
    xml = dump_ui(adb, serial)
    if not _has_yt_error_screen(xml or ""):
        return False
    _log(log_cb, "[YT] Phát hiện màn hình lỗi YT (400/server) — tap Retry")
    for label in YT_RETRY_LABELS:
        node = find_by_text(xml, label, contains=True)
        if node and node.clickable:
            tap(adb, serial, *node.center)
            time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 2.5))
            return True
    # Fallback: label exists but the clickable parent isn't matched —
    # tap the label centre anyway, Android will route to the parent.
    for label in YT_RETRY_LABELS:
        node = find_by_text(xml, label, contains=True)
        if node:
            tap(adb, serial, *node.center)
            _log(log_cb, f"[YT] Tap Retry (label-only) {label!r} @{node.center}")
            time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 2.5))
            return True
    return False


def _do_search(adb: ADB, serial: str, size: Size, keyword: str, log_cb=None) -> bool:
    """Search for `keyword` on YouTube; verify the SERP actually rendered.

    Why this is non-trivial: `am start ... -d <uri>` returns exit 0 as soon
    as the intent is dispatched, EVEN IF YouTube then renders a [400]
    server-error page. Operator screenshot showed exactly that — the
    triple-slash form `vnd.youtube:///results?search_query=...` (empty
    host, "/results" as path) is rejected with 400 on some Korean YT
    builds. We now:

      1) Try deep-link URIs in order of preference (correct form first,
         legacy form last).
      2) After each, give YT a beat then check for the [400] / 서버에 문제
         error overlay. If present, tap Retry once; if still bad, move on.
      3) Verify SERP via _wait_serp_ready (4s). Only return True when we
         actually see the result list — never trust the am-start exit code.
      4) On total deep-link failure, fall back to the UI search flow
         (open search box → type keyword → submit).
    """
    q = urllib.parse.quote_plus(keyword)
    deep_links = (
        # Canonical YouTube deep-link form per official docs.
        f"vnd.youtube://results?search_query={q}",
        # https:// handoff — Android resolves to YouTube via intent filter.
        f"https://www.youtube.com/results?search_query={q}",
        # Legacy triple-slash form — kept for older YT builds that need it.
        f"vnd.youtube:///results?search_query={q}",
    )

    for url in deep_links:
        r = adb.shell(serial, f'am start -a android.intent.action.VIEW -d "{url}"')
        if not r.ok:
            continue
        time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 2.5))

        # If YT slapped us with the 400/retry screen, tap Retry once.
        if _tap_yt_retry(adb, serial, log_cb):
            time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 2.5))

        # Verify SERP rendered. If not, try the next URL variant.
        if _wait_serp_ready(adb, serial, timeout=4.0, log_cb=log_cb):
            _log(log_cb, f"[YT] Search {keyword!r} ok qua {url}")
            return True
        _log(log_cb, f"[YT] {url} không cho SERP, thử URL kế")

    # Fallback: UI flow.
    _log(log_cb, "[YT] Mọi deep-link search fail — fallback UI search")
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
    # Last error check after UI submit.
    if _tap_yt_retry(adb, serial, log_cb):
        time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 2.5))
    return _wait_serp_ready(adb, serial, timeout=5.0, log_cb=log_cb)


def _wait_serp_ready(
    adb: ADB,
    serial: str,
    timeout: float = 6.0,
    log_cb=None,
) -> bool:
    """Poll dump_ui until SERP indicators are present.

    Deep-link search returns immediately but YT needs ~1-3s to render the
    result list. Before that point `_tap_top_result` was tapping into empty
    space or onto the loading spinner. Waiting on `SERP_RESULTS_RES_IDS`
    or a video-like resource id makes the next tap deterministic.
    """
    end = time.time() + timeout
    while time.time() < end:
        xml = dump_ui(adb, serial)
        if xml:
            if find_by_resource_id(xml, *SERP_RESULTS_RES_IDS):
                return True
            # Some YT versions don't expose the parent res-id; accept if we
            # can see at least one node whose res-id contains "video".
            from .ui_state import iter_nodes
            for n in iter_nodes(xml):
                if "video" in (n.resource_id or "").lower() and n.clickable:
                    return True
        time.sleep(0.4)
    _log(log_cb, f"[YT] SERP chưa render sau {timeout:.1f}s, vẫn tiếp tục")
    return False


def _bounds_contains(outer, inner) -> bool:
    """True iff `inner` rectangle lies entirely within `outer` rectangle."""
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    return ix1 >= ox1 and iy1 >= oy1 and ix2 <= ox2 and iy2 <= oy2


def _subtree_has_ad_marker(target, all_nodes, labels) -> bool:
    """True iff any node geometrically inside `target.bounds` carries an
    ad text/desc label.

    Why: on YT SERP the outermost clickable ViewGroup of a sponsored
    result has empty text/desc — the "광고" / "Sponsored" badge lives in
    a CHILD TextView a few levels deeper. Checking only the clickable
    node misses every ad. We don't have parent/child links from the
    uiautomator dump, so we approximate the subtree with "any node whose
    bounds are fully inside the clickable node's bounds".
    """
    for n in all_nodes:
        if n is target:
            continue
        if not _bounds_contains(target.bounds, n.bounds):
            continue
        text = ((n.text or "") + " " + (n.desc or "")).lower()
        if not text.strip():
            continue
        if any(lbl in text for lbl in labels):
            return True
    return False


# Resource-id substrings that signal an ad slot directly (skip the whole node).
AD_RES_ID_FRAGMENTS = (
    "promoted",
    "sponsored",
    "ad_badge",
    "ad_text",
    "ad_overlay",
    "ad_container",
    "endcap_layout",
)


def _scroll_serp_past_ads(adb: ADB, serial: str, size: Size, log_cb=None) -> None:
    """Small swipe-up to move SERP past the top ad block.

    Operator-reported pattern: when search succeeds, the first 1-2 cards
    are usually sponsored. The subtree-based ad filter in _tap_top_result
    catches most, but adversarial layouts can still slip through. A pair
    of short swipes shifts the viewport so the candidate list is sourced
    from the organic results lower on the page — the ad block scrolls
    off the top and is no longer a viable tap target either way.

    Swipe distance ≈ 25% of screen height per stroke, 1-2 strokes
    randomized to mimic human scanning. Far less than swipe_up() (50%)
    which would skip a row of organic results.
    """
    n = random.randint(1, 2)
    for _ in range(n):
        swipe(
            adb,
            serial,
            int(size.width * 0.5),
            int(size.height * 0.65),
            int(size.width * 0.5),
            int(size.height * 0.40),
            duration_ms=400,
        )
        time.sleep(lognormal_sleep(0.4, 0.3, 0.4, 1.0))
    _log(log_cb, f"[YT] Cuộn SERP {n} lượt nhẹ để bỏ qua block quảng cáo đầu")


def _tap_top_result(adb: ADB, serial: str, size: Size, log_cb=None) -> bool:
    """Tap a random top-3 ORGANIC video result on the SERP.

    Filter chain (each independently sufficient to skip a node):
      1) Wait up to 6s for SERP render.
      1.5) Scroll past the top ad block (1-2 short swipes).
      2) Ad-marker text in own OR descendant nodes (subtree bounds check).
         Closes the "outer clickable is empty, '광고' badge is in a child
         TextView" gap that let sponsored results through before.
      3) Resource-id substring match against AD_RES_ID_FRAGMENTS.
      4) Skip Shorts-shelf and reel-related nodes.
      5) Skip nodes above 10% height (filter chip strip).
      6) Require text/desc ≥ 5 chars for video-RID nodes, ≥ 20 for ViewGroup.
      7) Sort by (priority, y), pick random of top-3.
      8) Fallback: tap (50%, 45%) — deeper than before, lands below most
         un-detected ad blocks.
    """
    _wait_serp_ready(adb, serial, log_cb=log_cb)
    _scroll_serp_past_ads(adb, serial, size, log_cb)

    xml = dump_ui(adb, serial)
    if not xml:
        _log(log_cb, "[YT] SERP dump rỗng, fallback tap giữa")
        tap(adb, serial, size.width // 2, int(size.height * 0.45))
        time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 3.0))
        return True

    from .ui_state import iter_nodes
    all_nodes = list(iter_nodes(xml))
    candidates = []
    skipped_ads = 0
    for n in all_nodes:
        if not n.clickable:
            continue
        rid = (n.resource_id or "").lower()
        text = (n.text or "") + " " + (n.desc or "")
        low = text.lower()

        # 2a) Ad label in own text/desc.
        if any(bad in low for bad in YT_AD_LABELS):
            skipped_ads += 1
            continue
        # 2b) Ad label in any descendant inside this node's bounds.
        if _subtree_has_ad_marker(n, all_nodes, YT_AD_LABELS):
            skipped_ads += 1
            continue
        # 3) Known ad-slot res-id fragments.
        if any(frag in rid for frag in AD_RES_ID_FRAGMENTS):
            skipped_ads += 1
            continue
        # 4) Shorts shelf carousel and reel-related entries.
        if "shorts" in rid or "reel" in rid:
            continue

        x1, y1, x2, y2 = n.bounds
        h = y2 - y1
        # 5) Above the SERP body (search bar / filter chips area).
        if y1 < size.height * 0.10:
            continue
        # 6a) Strong: explicit video resource-id with title text.
        if "video" in rid and len(text.strip()) >= 5:
            candidates.append((n, 0))
            continue
        # 6b) Weak: big clickable block with a meaningful title.
        if n.class_name in ("android.view.ViewGroup", "android.widget.FrameLayout"):
            if h > size.height * 0.12 and len(text.strip()) >= 20:
                candidates.append((n, 1))

    if skipped_ads:
        _log(log_cb, f"[YT] Lọc bỏ {skipped_ads} kết quả quảng cáo trên SERP")

    if not candidates:
        # Fallback tap deeper than the typical sponsored-result block
        # (sponsored cards end around 35-40% height on YT mobile SERP).
        y = int(size.height * 0.45)
        x = size.width // 2
        _log(log_cb, f"[YT] Không tìm thấy video organic, fallback tap @({x},{y})")
        tap(adb, serial, x, y)
        time.sleep(lognormal_sleep(0.8, 0.4, 1.0, 3.0))
        return True

    # Sort by (priority asc, y asc) — strongest first, then top-down.
    candidates.sort(key=lambda t: (t[1], t[0].bounds[1]))
    pool = candidates[: min(3, len(candidates))]
    pick, prio = random.choice(pool)
    _log(
        log_cb,
        f"[YT] Mở video top-{pool.index((pick, prio))+1}/{len(pool)} "
        f"(prio={prio}): {pick.text[:60]!r} @{pick.center}",
    )
    tap(adb, serial, *pick.center)
    time.sleep(lognormal_sleep(0.9, 0.4, 1.5, 4.0))
    return True


def _verify_in_watch(adb: ADB, serial: str, timeout: float = 5.0, log_cb=None) -> bool:
    """After tapping a SERP result, confirm we entered the video Watch view.

    If False, the caller should `back()` to leave the SERP cleanly and skip
    the watch step (so we don't burn 60-300s of sleep on a non-playing page).
    """
    end = time.time() + timeout
    while time.time() < end:
        xml = dump_ui(adb, serial)
        if xml and find_by_resource_id(xml, *PLAYER_RES_IDS):
            return True
        time.sleep(0.5)
    return False


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
        # Close description / share / more-options bottom sheet if a stray
        # tap opened one (operator screenshot: 설명 panel covering bottom
        # half while a pre-roll ad plays at top).
        _dismiss_overlay(adb, serial, size, log_cb)
        # Skip pre-roll / mid-roll YouTube ads when the Skip chip appears.
        _skip_ad_if_present(adb, serial, size, log_cb)

        # Inner ad-watcher: instead of sleeping 8-20s straight (which can
        # miss a 5-second skip window if it opens mid-nap), break the nap
        # into 3-second chunks and re-check for the Skip chip between them.
        # Human-action cadence (pause / scroll comments) is still per
        # outer iteration so behaviour stays naturalistic.
        nap_target = random.uniform(8.0, 20.0)
        nap_end = min(end, time.time() + nap_target)
        while time.time() < nap_end:
            chunk = min(3.0, nap_end - time.time())
            if interruptible_sleep(stop_event, max(0.5, chunk)):
                return
            # Cheap fast-path: only dump UI + tap when the player view
            # likely has an ad showing. _skip_ad_if_present is a no-op when
            # no Skip element exists, so calling it every 3s is safe.
            _skip_ad_if_present(adb, serial, size, log_cb)

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
    do_shorts: bool = True,
    do_search: bool = True,
    extra_keywords: Optional[list[str]] = None,
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
    """One full YouTube CYCLE. Returns True if it ran end-to-end.

    Operator semantics (revised): a single "cycle" now means
      1) Clean-launch YouTube.
      2) Shorts session: scroll N reels with likes.
      3) For EACH keyword in [keyword, *extra_keywords]:
           - Back to Home tab.
           - Search that keyword.
           - Random click a top-3 organic video.
           - Watch it for watch_min..watch_max seconds.
      4) Return; the caller (`_run_one_task`) loops this cycle `loops`
         times, each iteration restarting with a clean-launch.

    Previously each keyword × loops was its own task, meaning loops=3
    with 3 keywords produced 9 Shorts sessions; now it produces 3
    Shorts sessions and 9 watches, which is what the operator wanted.
    """
    if _cancelled(stop_event):
        return False

    keywords = [keyword] + [k for k in (extra_keywords or []) if k]
    if not keywords:
        _log(log_cb, "[YT] Không có từ khoá nào, bỏ qua task")
        return False

    if not _open_youtube(adb, serial, log_cb):
        return False
    size = get_size(adb, serial)
    _log(
        log_cb,
        f"[YT] Bắt đầu cycle | size={size.width}x{size.height} "
        f"| keywords({len(keywords)})={keywords!r}",
    )

    # ----- Phase 1: Shorts -----
    if do_shorts:
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
    else:
        _log(log_cb, "[YT] Bỏ qua Shorts (do_shorts=False)")

    if not do_search:
        _log(log_cb, "[YT] Bỏ qua Search (do_search=False), kết thúc cycle")
        return True

    # ----- Phase 2: search + watch for each keyword -----
    for idx, kw in enumerate(keywords, start=1):
        if _cancelled(stop_event):
            return True
        _log(log_cb, f"[YT] Từ khoá {idx}/{len(keywords)}: {kw!r}")

        _go_to_home_tab(adb, serial, size, log_cb)
        if _cancelled(stop_event):
            return True
        ensure_app_foreground(adb, serial, YOUTUBE_PKG, log_cb=log_cb)

        if not _do_search(adb, serial, size, kw, log_cb):
            _log(log_cb, f"[YT] Search {kw!r} fail — skip sang từ khoá kế")
            continue
        if _cancelled(stop_event):
            return True

        if not _tap_top_result(adb, serial, size, log_cb):
            _log(log_cb, f"[YT] Không tap được kết quả {kw!r} — back và skip")
            back(adb, serial)
            time.sleep(lognormal_sleep(0.4, 0.3, 0.4, 1.2))
            continue

        if not _verify_in_watch(adb, serial, log_cb=log_cb):
            _log(log_cb, f"[YT] Tap kết quả {kw!r} không vào Watch — back và skip")
            back(adb, serial)
            time.sleep(lognormal_sleep(0.4, 0.3, 0.4, 1.2))
            continue

        time.sleep(lognormal_sleep(0.8, 0.4, 1.2, 3.5))
        _watch_video(adb, serial, size, watch_min_sec, watch_max_sec, stop_event, log_cb)

        # Clean exit from Watch view so the next keyword starts from a
        # known place (Home tab nav handles it but a back here avoids
        # leaving the player floating in minimised mode).
        back(adb, serial)
        time.sleep(lognormal_sleep(0.5, 0.4, 0.5, 1.8))

    _log(log_cb, f"[YT] Cycle xong, đã quét {len(keywords)} từ khoá")
    return True
