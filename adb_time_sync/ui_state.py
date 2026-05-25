"""Read UI state from a device without uiautomator2.

Uses `uiautomator dump` + simple XML parsing.
Falls back to template matching for visual elements
that aren't exposed as accessibility nodes.
"""
from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from .adb import ADB


@dataclass(frozen=True)
class UiNode:
    resource_id: str
    class_name: str
    text: str
    desc: str
    bounds: tuple[int, int, int, int]  # x1, y1, x2, y2
    clickable: bool
    package: str

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2


_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _parse_bounds(raw: str) -> Optional[tuple[int, int, int, int]]:
    m = _BOUNDS_RE.search(raw or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def dump_ui(adb: ADB, serial: str) -> Optional[str]:
    """Dump the current UI hierarchy as XML, or None on failure."""
    r = adb.shell(serial, "uiautomator dump /sdcard/ui.xml")
    if not r.ok:
        return None
    if "ERROR" in (r.out or "").upper() and "could not get idle" in (r.out or "").lower():
        adb.shell(serial, "uiautomator dump --compressed /sdcard/ui.xml")
    r2 = adb.shell(serial, "cat /sdcard/ui.xml")
    if not r2.ok or not r2.out:
        return None
    out = r2.out
    idx = out.find("<?xml")
    if idx > 0:
        out = out[idx:]
    return out


def iter_nodes(xml_text: str):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return
    for node in root.iter("node"):
        bounds = _parse_bounds(node.attrib.get("bounds", ""))
        if not bounds:
            continue
        yield UiNode(
            resource_id=node.attrib.get("resource-id", ""),
            class_name=node.attrib.get("class", ""),
            text=node.attrib.get("text", ""),
            desc=node.attrib.get("content-desc", ""),
            bounds=bounds,
            clickable=node.attrib.get("clickable", "false") == "true",
            package=node.attrib.get("package", ""),
        )


def find_by_resource_id(xml_text: str, *resource_ids: str) -> Optional[UiNode]:
    targets = set(resource_ids)
    for node in iter_nodes(xml_text):
        if node.resource_id in targets:
            return node
    return None


def find_by_text(xml_text: str, *texts: str, contains: bool = False) -> Optional[UiNode]:
    targets = [t.lower() for t in texts]
    for node in iter_nodes(xml_text):
        t = (node.text or "").lower()
        d = (node.desc or "").lower()
        if contains:
            if any(needle in t or needle in d for needle in targets):
                return node
        else:
            if t in targets or d in targets:
                return node
    return None


def find_all_by_class(xml_text: str, class_name: str, *, clickable_only: bool = True) -> list[UiNode]:
    res: list[UiNode] = []
    for node in iter_nodes(xml_text):
        if node.class_name != class_name:
            continue
        if clickable_only and not node.clickable:
            continue
        res.append(node)
    return res


def random_top_n(nodes: list[UiNode], n: int = 5) -> Optional[UiNode]:
    if not nodes:
        return None
    pool = nodes[: min(n, len(nodes))]
    return random.choice(pool)
