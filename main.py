"""CLI entry for BoxPhone YouTube/Chrome automation.

Usage:
    python main.py                          # interactive menu
    python main.py run                      # run tasks.txt on all online devices
    python main.py run --tasks my.txt --workers 5 --devices 192.168.5.11,192.168.5.12

The GUI in gui_app.py is the recommended entry — this CLI is here for
headless / scripted use only.
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from adb_time_sync.adb import ADB
from adb_time_sync.human import home, wait_for_foreground
from adb_time_sync.task_runner import (
    Task,
    load_tasks,
    run_tasks_on_device,
)
import db


def _device_alias(serial: str, aliases: dict[str, str]) -> str:
    return aliases.get(serial) or serial


def _online_devices(adb: ADB) -> list[str]:
    return [serial for serial, state in adb.devices_all() if state == "device"]


def _alias_map() -> dict[str, str]:
    return {d["ip"]: d.get("name") or "" for d in db.list_devices() if d.get("name")}


def _runner_for_device(
    adb: ADB,
    serial: str,
    tasks: list[Task],
    stop_event: threading.Event,
    log_cb: Optional[Callable[[str], None]],
    label: str,
    watch_seconds: Optional[float],
) -> dict:
    def child_log(msg: str) -> None:
        if log_cb:
            log_cb(f"[DEV {label}] {msg}")

    db.touch_device(serial)
    return run_tasks_on_device(
        adb,
        serial,
        tasks,
        stop_event=stop_event,
        log_cb=child_log,
        watch_seconds=watch_seconds,
    )


def run_tasks(
    adb: ADB,
    tasks: list[Task],
    serials: list[str],
    workers: int,
    *,
    stop_event: Optional[threading.Event] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    watch_seconds: Optional[float] = None,
) -> dict[str, dict[str, int]]:
    """Run `tasks` on each serial in parallel; return {serial: {task_str: done}}."""
    stop_event = stop_event or threading.Event()
    aliases = _alias_map()
    workers = max(1, min(workers, len(serials) or 1))

    if log_cb:
        log_cb(
            f"[RUN] devices={len(serials)} workers={workers} tasks={len(tasks)}"
        )

    results: dict[str, dict[str, int]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                _runner_for_device,
                adb,
                serial,
                tasks,
                stop_event,
                log_cb,
                _device_alias(serial, aliases),
                watch_seconds,
            ): serial
            for serial in serials
        }
        for fut in as_completed(futs):
            serial = futs[fut]
            try:
                results[serial] = fut.result()
            except Exception as e:
                results[serial] = {"_error": 0}
                if log_cb:
                    log_cb(f"[DEV {_device_alias(serial, aliases)}] [ERROR] {e}")

    # Persist results.
    for serial, per_task in results.items():
        for task_str, done in per_task.items():
            if task_str.startswith("_"):
                db.log_task_run(serial, "system", task_str, 0, 0, "error")
                continue
            try:
                app, keyword, loops_str = task_str.split("|", 2)
                requested = int(loops_str)
            except ValueError:
                app, keyword, requested = "?", task_str, 0
            status = "ok" if done == requested else ("partial" if done > 0 else "fail")
            db.log_task_run(serial, app, keyword, requested, done, status)

    return results


def _print_summary(results: dict[str, dict[str, int]]) -> None:
    print("\n=== KẾT QUẢ ===")
    grand_total = 0
    for serial, per_task in results.items():
        print(f"\n{serial}:")
        for task_str, done in per_task.items():
            print(f"  {task_str:50} done={done}")
            if not task_str.startswith("_"):
                grand_total += done
    print(f"\nTỔNG loop hoàn thành: {grand_total}")


def cmd_run(args: argparse.Namespace) -> int:
    db.init_db()
    adb = ADB(adb_path="adb", timeout_sec=20, verbose=False)

    tasks = load_tasks(args.tasks)
    if not tasks:
        print(f"Không có task nào trong {args.tasks}")
        return 2

    if args.devices:
        serials = [s.strip() for s in args.devices.split(",") if s.strip()]
    else:
        serials = _online_devices(adb)
    if not serials:
        print("Không có thiết bị online.")
        return 3

    print(f"Sẽ chạy {len(tasks)} task trên {len(serials)} thiết bị, workers={args.workers}")
    for t in tasks:
        print(f"  - {t}")

    results = run_tasks(
        adb,
        tasks,
        serials,
        workers=args.workers,
        log_cb=lambda m: print(m, flush=True),
        watch_seconds=args.watch if args.watch > 0 else None,
    )
    _print_summary(results)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    adb = ADB()
    devices = adb.devices_all()
    aliases = _alias_map()
    if not devices:
        print("Không có thiết bị ADB.")
        return 0
    for serial, state in devices:
        name = aliases.get(serial, "")
        print(f"  {serial:30} {state}  {name}")
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    db.init_db()
    db.update_device_name(args.ip, args.name)
    print(f"Đã đặt tên {args.ip} = {args.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="auto-phone", description="BoxPhone YouTube/Chrome automation")
    sub = p.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Chạy tasks.txt")
    p_run.add_argument("--tasks", default="tasks.txt")
    p_run.add_argument("--workers", type=int, default=4)
    p_run.add_argument("--devices", default="", help="Danh sách IP cách nhau bằng dấu phẩy (bỏ trống = tất cả online)")
    p_run.add_argument("--watch", type=float, default=0,
                       help="Giây xem chung cho mỗi reel + mỗi video. 0 = giữ mặc định/opts.")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="Liệt kê thiết bị ADB")
    p_list.set_defaults(func=cmd_list)

    p_ren = sub.add_parser("rename", help="Đặt alias cho thiết bị")
    p_ren.add_argument("ip")
    p_ren.add_argument("name")
    p_ren.set_defaults(func=cmd_rename)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
