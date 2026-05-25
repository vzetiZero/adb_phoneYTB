"""PySide6 GUI to manage devices and run YouTube / Chrome workflows.

Top tabs:
  - Run      : pick devices, choose workflow (YouTube / Chrome / Advanced), Start
  - Devices  : alias CRUD + import from ADB
  - History  : last 200 task_runs from SQLite

Run tab is split:
  Left  = device selection + workers + Cancel
  Right = workflow sub-tabs (form-driven) + shared log panel
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import List

# When packaged with PyInstaller (--onefile), the exe extracts to a temp dir
# and CWD may not match where the user double-clicked from. Anchor relative
# files (tasks.txt, comments.txt, app.db, debug_dumps/) to the .exe folder.
if getattr(sys, "frozen", False):
    try:
        os.chdir(os.path.dirname(sys.executable))
    except Exception:
        pass

from PySide6 import QtCore, QtGui, QtWidgets

import db
import main as core
from adb_time_sync.adb import ADB
from adb_time_sync.task_runner import Task


class LogSignal(QtCore.QObject):
    message = QtCore.Signal(str)
    finished = QtCore.Signal()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        db.init_db()
        self.setWindowTitle("BoxPhone Automation — YouTube / Chrome")
        self.resize(1280, 820)

        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._signal = LogSignal()
        self._signal.message.connect(self._append_log)
        self._signal.finished.connect(self._finish_ui)

        self._build_ui()
        self._apply_style()
        self._refresh_devices()
        self._refresh_history()

    # ====================================================================
    # UI construction
    # ====================================================================
    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        v = QtWidgets.QVBoxLayout(root)
        self.tabs = QtWidgets.QTabWidget()
        v.addWidget(self.tabs)

        self._build_run_tab()
        self._build_devices_tab()
        self._build_history_tab()

    def _build_run_tab(self) -> None:
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "Run")
        h = QtWidgets.QHBoxLayout(tab)
        left = QtWidgets.QVBoxLayout()
        right = QtWidgets.QVBoxLayout()
        h.addLayout(left, 1)
        h.addLayout(right, 2)

        # ---- Left column : Devices + Workers + Cancel ----
        self.device_list = QtWidgets.QListWidget()
        self.device_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        left.addWidget(self._labeled("Thiết bị (chọn nhiều)", self.device_list))

        row = QtWidgets.QHBoxLayout()
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_select_all = QtWidgets.QPushButton("Select All")
        self.btn_clear_sel = QtWidgets.QPushButton("Clear")
        self.btn_refresh.clicked.connect(self._refresh_devices)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_clear_sel.clicked.connect(self._clear_sel)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_select_all)
        row.addWidget(self.btn_clear_sel)
        left.addLayout(row)

        self.workers_spin = QtWidgets.QSpinBox()
        self.workers_spin.setRange(1, 50)
        self.workers_spin.setValue(4)
        left.addWidget(self._labeled("Số thiết bị chạy song song", self.workers_spin))

        # Combined runner — uses configs from BOTH sub-tabs.
        self.btn_start_all = QtWidgets.QPushButton("▶ Start All (YouTube → Chrome)")
        self.btn_start_all.setToolTip(
            "Chạy nối tiếp trên mỗi thiết bị: hết task YouTube → chạy task Chrome.\n"
            "Dùng config từ cả 2 tab. Bỏ trống từ khoá tab nào thì skip tab đó."
        )
        self.btn_start_all.clicked.connect(self._start_all)
        left.addWidget(self.btn_start_all)

        self.btn_cancel = QtWidgets.QPushButton("⏹ Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        left.addWidget(self.btn_cancel)

        # Home — manually return every selected device to Android home screen
        # (force-stop YouTube + Chrome, then KEYCODE_HOME). Useful when an ad
        # gets stuck or the operator wants to reset the floor without waiting
        # for a workflow to finish.
        self.btn_home = QtWidgets.QPushButton("🏠 Home (về màn hình chính)")
        self.btn_home.setToolTip(
            "Force-stop YouTube + Chrome trên các thiết bị đã chọn, rồi bấm Home.\n"
            "Dùng để reset nhanh khi máy bị kẹt quảng cáo hoặc cần đưa về trạng thái sạch."
        )
        self.btn_home.clicked.connect(self._go_home_devices)
        left.addWidget(self.btn_home)
        left.addStretch(1)

        # ---- Right column : workflow tabs + log ----
        self.flow_tabs = QtWidgets.QTabWidget()
        self.flow_tabs.addTab(self._build_youtube_form(), "YouTube")
        self.flow_tabs.addTab(self._build_chrome_form(), "Chrome (Google)")
        right.addWidget(self.flow_tabs, 1)

        log_box = QtWidgets.QGroupBox("Log")
        log_lay = QtWidgets.QVBoxLayout(log_box)
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        log_lay.addWidget(self.log_view)
        right.addWidget(log_box, 1)

    # --------------------------- YouTube form ---------------------------
    def _build_youtube_form(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)

        hint = QtWidgets.QLabel(
            "<b>Workflow:</b> về Home → force-stop YouTube → mở YouTube sạch → "
            "lướt Shorts (N reel × Y giây/reel, like theo tỉ lệ) → "
            "search từ khoá → xem video ngẫu nhiên (W giây). Comment đã bị tắt."
        )
        hint.setWordWrap(True)
        v.addWidget(hint)

        form = QtWidgets.QFormLayout()
        v.addLayout(form)

        self.yt_keywords = QtWidgets.QPlainTextEdit()
        self.yt_keywords.setPlaceholderText("Mỗi dòng 1 từ khoá. Ví dụ:\nbaomoi\nnhạc trẻ 2026\nreview điện thoại")
        self.yt_keywords.setFixedHeight(80)
        form.addRow("Từ khoá tìm kiếm", self.yt_keywords)

        self.yt_loops = QtWidgets.QSpinBox()
        self.yt_loops.setRange(1, 1000)
        self.yt_loops.setValue(3)
        form.addRow("Số lần lặp mỗi từ khoá", self.yt_loops)

        # Reels min/max
        reels_w = QtWidgets.QWidget()
        reels_h = QtWidgets.QHBoxLayout(reels_w)
        reels_h.setContentsMargins(0, 0, 0, 0)
        self.yt_reels_min = QtWidgets.QSpinBox(); self.yt_reels_min.setRange(0, 200); self.yt_reels_min.setValue(5)
        self.yt_reels_max = QtWidgets.QSpinBox(); self.yt_reels_max.setRange(0, 200); self.yt_reels_max.setValue(10)
        reels_h.addWidget(QtWidgets.QLabel("min"));  reels_h.addWidget(self.yt_reels_min)
        reels_h.addWidget(QtWidgets.QLabel("max"));  reels_h.addWidget(self.yt_reels_max)
        reels_h.addStretch(1)
        form.addRow("Số reel lướt", reels_w)

        self.yt_reel_watch = QtWidgets.QSpinBox()
        self.yt_reel_watch.setRange(1, 600); self.yt_reel_watch.setValue(15); self.yt_reel_watch.setSuffix(" s")
        form.addRow("Xem mỗi reel", self.yt_reel_watch)

        self.yt_shorts_max_min = QtWidgets.QSpinBox()
        self.yt_shorts_max_min.setRange(0, 720)
        self.yt_shorts_max_min.setValue(10)
        self.yt_shorts_max_min.setSuffix(" phút  (0 = không giới hạn, theo reel min/max)")
        form.addRow("Thời gian tối đa lướt Reel", self.yt_shorts_max_min)

        self.yt_like_rate = QtWidgets.QDoubleSpinBox()
        self.yt_like_rate.setRange(0.0, 1.0); self.yt_like_rate.setSingleStep(0.1); self.yt_like_rate.setValue(1.0)
        form.addRow("Tỉ lệ like (0-1)", self.yt_like_rate)

        # Video watch min/max
        watch_w = QtWidgets.QWidget()
        watch_h = QtWidgets.QHBoxLayout(watch_w)
        watch_h.setContentsMargins(0, 0, 0, 0)
        self.yt_watch_min = QtWidgets.QSpinBox(); self.yt_watch_min.setRange(1, 7200); self.yt_watch_min.setValue(60); self.yt_watch_min.setSuffix(" s")
        self.yt_watch_max = QtWidgets.QSpinBox(); self.yt_watch_max.setRange(1, 7200); self.yt_watch_max.setValue(300); self.yt_watch_max.setSuffix(" s")
        watch_h.addWidget(QtWidgets.QLabel("min")); watch_h.addWidget(self.yt_watch_min)
        watch_h.addWidget(QtWidgets.QLabel("max")); watch_h.addWidget(self.yt_watch_max)
        watch_h.addStretch(1)
        form.addRow("Xem video sau search", watch_w)

        self.btn_start_yt = QtWidgets.QPushButton("▶ Start YouTube")
        self.btn_start_yt.clicked.connect(self._start_youtube)
        v.addWidget(self.btn_start_yt)
        v.addStretch(1)
        return w

    # --------------------------- Chrome form ---------------------------
    def _build_chrome_form(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)

        hint = QtWidgets.QLabel(
            "<b>Workflow:</b> mở Chrome → Google search từ khoá → cuộn SERP → "
            "tap kết quả top-5 (bỏ ads) → lướt trang đích (W giây ngẫu nhiên)."
        )
        hint.setWordWrap(True)
        v.addWidget(hint)

        form = QtWidgets.QFormLayout()
        v.addLayout(form)

        self.cr_keywords = QtWidgets.QPlainTextEdit()
        self.cr_keywords.setPlaceholderText(
            "Mỗi dòng 1 từ khoá. Có thể chỉ định domain ưu tiên bằng dấu '|'.\n"
            "Ví dụ:\n"
            "tin nóng hôm nay\n"
            "giá vàng\n"
            "naver news | news.naver.com\n"
            "  (Sẽ search Google rồi tap đúng kết quả news.naver.com; \n"
            "   nếu không có trên SERP sẽ mở thẳng https://news.naver.com/)"
        )
        self.cr_keywords.setFixedHeight(110)
        form.addRow("Từ khoá Google", self.cr_keywords)

        self.cr_loops = QtWidgets.QSpinBox()
        self.cr_loops.setRange(1, 1000); self.cr_loops.setValue(3)
        form.addRow("Số lần lặp mỗi từ khoá", self.cr_loops)

        # Browse min/max
        b_w = QtWidgets.QWidget()
        b_h = QtWidgets.QHBoxLayout(b_w)
        b_h.setContentsMargins(0, 0, 0, 0)
        self.cr_watch_min = QtWidgets.QSpinBox(); self.cr_watch_min.setRange(1, 7200); self.cr_watch_min.setValue(60); self.cr_watch_min.setSuffix(" s")
        self.cr_watch_max = QtWidgets.QSpinBox(); self.cr_watch_max.setRange(1, 7200); self.cr_watch_max.setValue(300); self.cr_watch_max.setSuffix(" s")
        b_h.addWidget(QtWidgets.QLabel("min")); b_h.addWidget(self.cr_watch_min)
        b_h.addWidget(QtWidgets.QLabel("max")); b_h.addWidget(self.cr_watch_max)
        b_h.addStretch(1)
        form.addRow("Lướt trang đích", b_w)

        self.btn_start_cr = QtWidgets.QPushButton("▶ Start Chrome")
        self.btn_start_cr.clicked.connect(self._start_chrome)
        v.addWidget(self.btn_start_cr)
        v.addStretch(1)
        return w

    # --------------------------- Devices tab ---------------------------
    def _build_devices_tab(self) -> None:
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "Devices")
        v = QtWidgets.QVBoxLayout(tab)

        self.dev_table = QtWidgets.QTableWidget(0, 4)
        self.dev_table.setHorizontalHeaderLabels(["IP / Serial", "Name", "Size", "Last seen"])
        self.dev_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.dev_table)

        row = QtWidgets.QHBoxLayout()
        b_import = QtWidgets.QPushButton("Import from ADB"); b_import.clicked.connect(self._dev_import)
        b_ren = QtWidgets.QPushButton("Rename"); b_ren.clicked.connect(self._dev_rename)
        b_del = QtWidgets.QPushButton("Delete"); b_del.clicked.connect(self._dev_delete)
        b_auto = QtWidgets.QPushButton("Auto Number"); b_auto.clicked.connect(self._dev_auto_number)
        for b in (b_import, b_ren, b_del, b_auto):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

    # --------------------------- History tab ---------------------------
    def _build_history_tab(self) -> None:
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "History")
        v = QtWidgets.QVBoxLayout(tab)

        self.hist_table = QtWidgets.QTableWidget(0, 7)
        self.hist_table.setHorizontalHeaderLabels(
            ["Time", "Serial", "App", "Keyword", "Requested", "Done", "Status"]
        )
        self.hist_table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.hist_table)

        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("Reload"); b.clicked.connect(self._refresh_history)
        row.addWidget(b); row.addStretch(1)
        v.addLayout(row)

    # ====================================================================
    # Helpers
    # ====================================================================
    def _labeled(self, text: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox(text)
        lay = QtWidgets.QVBoxLayout(box)
        lay.addWidget(widget)
        return box

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #ffffff; color: #0f172a; font-size: 14px; }
            QTabWidget::pane { border: 1px solid #cbd5e1; }
            QTabBar::tab { padding: 8px 16px; font-weight: 700; }
            QGroupBox { border: 1px solid #cbd5e1; border-radius: 6px; margin-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; font-weight: 700; color: #1f2937; }
            QPushButton { background: #ffffff; border: 2px solid #1f2937; padding: 8px 12px; border-radius: 6px; font-weight: 700; }
            QPushButton:hover { background: #f1f5f9; }
            QPushButton:disabled { border-color: #94a3b8; color: #94a3b8; }
            QPushButton#start { background: #16a34a; color: #ffffff; border-color: #15803d; }
            QPushButton#start:hover { background: #15803d; }
            QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTextEdit, QPlainTextEdit, QTableWidget {
                background: #ffffff; border: 2px solid #1f2937; border-radius: 6px;
            }
            QTableWidget { gridline-color: #cbd5e1; }
            """
        )
        # mark all start buttons green
        for btn in (
            getattr(self, "btn_start_yt", None),
            getattr(self, "btn_start_cr", None),
            getattr(self, "btn_start_all", None),
        ):
            if btn is not None:
                btn.setObjectName("start")
                btn.style().unpolish(btn); btn.style().polish(btn)

    # ====================================================================
    # Devices selection
    # ====================================================================
    def _refresh_devices(self) -> None:
        adb = ADB()
        try:
            devices = adb.devices_all()
        except Exception as e:
            self._log(f"[LỖI] adb devices: {e}")
            devices = []
        aliases = {d["ip"]: (d.get("name") or "") for d in db.list_devices()}

        self.device_list.clear()
        for serial, state in sorted(devices):
            name = aliases.get(serial, "")
            label = f"{serial}  [{state}]" + (f"  ({name})" if name else "")
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, serial)
            item.setCheckState(QtCore.Qt.Unchecked)
            if state != "device":
                item.setForeground(QtGui.QColor("#94a3b8"))
            self.device_list.addItem(item)

        # Devices tab table
        if hasattr(self, "dev_table"):
            self.dev_table.setRowCount(0)
            for d in db.list_devices():
                row = self.dev_table.rowCount()
                self.dev_table.insertRow(row)
                self.dev_table.setItem(row, 0, QtWidgets.QTableWidgetItem(d.get("ip", "")))
                self.dev_table.setItem(row, 1, QtWidgets.QTableWidgetItem(d.get("name") or ""))
                wh = ""
                if d.get("width") and d.get("height"):
                    wh = f"{d['width']}x{d['height']}"
                self.dev_table.setItem(row, 2, QtWidgets.QTableWidgetItem(wh))
                self.dev_table.setItem(row, 3, QtWidgets.QTableWidgetItem(d.get("last_seen") or ""))

    def _selected_devices(self) -> List[str]:
        res = []
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item.checkState() == QtCore.Qt.Checked:
                res.append(item.data(QtCore.Qt.UserRole))
        return res

    def _select_all(self) -> None:
        for i in range(self.device_list.count()):
            self.device_list.item(i).setCheckState(QtCore.Qt.Checked)

    def _clear_sel(self) -> None:
        for i in range(self.device_list.count()):
            self.device_list.item(i).setCheckState(QtCore.Qt.Unchecked)

    def _dev_import(self) -> None:
        adb = ADB()
        try:
            for serial, _state in adb.devices_all():
                db.upsert_device(serial)
        except Exception as e:
            self._log(f"[LỖI] import: {e}")
        self._refresh_devices()

    def _dev_rename(self) -> None:
        row = self.dev_table.currentRow()
        if row < 0:
            self._log("[LỖI] Chọn 1 dòng để rename")
            return
        ip = self.dev_table.item(row, 0).text().strip()
        cur = self.dev_table.item(row, 1).text() if self.dev_table.item(row, 1) else ""
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename", f"Name cho {ip}:", text=cur)
        if not ok:
            return
        db.update_device_name(ip, name.strip() or None)
        self._refresh_devices()

    def _dev_delete(self) -> None:
        row = self.dev_table.currentRow()
        if row < 0:
            return
        ip = self.dev_table.item(row, 0).text().strip()
        db.delete_device(ip)
        self._refresh_devices()

    def _dev_auto_number(self) -> None:
        devices = sorted(db.list_devices(), key=lambda d: str(d.get("ip") or ""))
        for i, d in enumerate(devices, start=1):
            ip = d.get("ip")
            if ip:
                db.update_device_name(ip, f"{i:02d}")
        self._refresh_devices()

    # ====================================================================
    # History
    # ====================================================================
    def _refresh_history(self) -> None:
        rows = db.recent_runs(200)
        self.hist_table.setRowCount(0)
        for r in rows:
            row = self.hist_table.rowCount()
            self.hist_table.insertRow(row)
            self.hist_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(r.get("ts", ""))))
            self.hist_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(r.get("serial", ""))))
            self.hist_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(r.get("app", ""))))
            self.hist_table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(r.get("keyword", ""))))
            self.hist_table.setItem(row, 4, QtWidgets.QTableWidgetItem(str(r.get("requested_loops", ""))))
            self.hist_table.setItem(row, 5, QtWidgets.QTableWidgetItem(str(r.get("done_loops", ""))))
            self.hist_table.setItem(row, 6, QtWidgets.QTableWidgetItem(str(r.get("status", ""))))

    # ====================================================================
    # Log
    # ====================================================================
    def _log(self, msg: str) -> None:
        self._signal.message.emit(msg)

    def _append_log(self, msg: str) -> None:
        color = "#0f172a"
        if "[LỖI]" in msg or "[ERROR]" in msg or "fail" in msg.lower():
            color = "#dc2626"
        elif "[OK" in msg or "ok=True" in msg or "[XONG]" in msg:
            color = "#059669"
        elif "[HỦY]" in msg or "[CANCEL]" in msg:
            color = "#d97706"
        elif msg.startswith("[DEV "):
            label = msg.split("]", 1)[0].replace("[DEV ", "").strip()
            palette = ["#0891b2", "#7c3aed", "#db2777", "#16a34a", "#f59e0b", "#2563eb", "#dc2626"]
            color = palette[abs(hash(label)) % len(palette)]
        self.log_view.append(f"<span style='color:{color}'>{msg}</span>")

    # ====================================================================
    # Start handlers
    # ====================================================================
    def _read_keywords(self, edit: QtWidgets.QPlainTextEdit) -> list[str]:
        return [
            k.strip() for k in edit.toPlainText().splitlines()
            if k.strip() and not k.strip().startswith("#")
        ]

    def _build_youtube_tasks(self) -> list[Task] | None:
        """Returns: list[Task] | [] (no keywords) | None (validation error).

        Semantics (revised): a single Task carries ALL keywords. The runner
        executes ONE cycle (Shorts + per-keyword search/watch) per loop
        iteration, so loops=3 with 4 keywords = 3 Shorts sessions + 12
        watches (not 12 Shorts sessions like before).
        """
        kws = self._read_keywords(self.yt_keywords)
        if not kws:
            return []
        if self.yt_reels_min.value() > self.yt_reels_max.value():
            self._log("[LỖI] YouTube: reels_min > reels_max"); return None
        if self.yt_watch_min.value() > self.yt_watch_max.value():
            self._log("[LỖI] YouTube: watch_min > watch_max"); return None

        reel_s = float(self.yt_reel_watch.value())
        # add ±15% jitter around the single "Xem mỗi reel" knob to look natural
        d_min = max(1.0, reel_s * 0.85)
        d_max = reel_s * 1.15
        loops = int(self.yt_loops.value())
        shorts_limit_sec = int(self.yt_shorts_max_min.value()) * 60
        opts = {
            "all_keywords": list(kws),
            "reels_min": int(self.yt_reels_min.value()),
            "reels_max": int(self.yt_reels_max.value()),
            "delay_min": d_min,
            "delay_max": d_max,
            "like_rate": float(self.yt_like_rate.value()),
            "shorts_time_limit": float(shorts_limit_sec),
            "watch_min": float(self.yt_watch_min.value()),
            "watch_max": float(self.yt_watch_max.value()),
        }
        # Single task; task.keyword is the primary (first) for stats display.
        return [Task(app="youtube", keyword=kws[0], loops=loops, opts=opts)]

    def _build_chrome_tasks(self) -> list[Task] | None:
        lines = self._read_keywords(self.cr_keywords)
        if not lines:
            return []
        if self.cr_watch_min.value() > self.cr_watch_max.value():
            self._log("[LỖI] Chrome: watch_min > watch_max"); return None
        loops = int(self.cr_loops.value())
        base_opts = {
            "watch_min": float(self.cr_watch_min.value()),
            "watch_max": float(self.cr_watch_max.value()),
        }
        tasks: list[Task] = []
        for line in lines:
            # Allow per-line override: "keyword | preferred.domain"
            if "|" in line:
                kw, domain = line.split("|", 1)
                kw = kw.strip()
                domain = domain.strip()
            else:
                kw, domain = line, ""
            if not kw:
                continue
            opts = dict(base_opts)
            if domain:
                opts["prefer_domain"] = domain
            tasks.append(Task(app="chrome", keyword=kw, loops=loops, opts=opts))
        return tasks

    def _start_youtube(self) -> None:
        tasks = self._build_youtube_tasks()
        if tasks is None:
            return
        if not tasks:
            self._log("[LỖI] YouTube: chưa nhập từ khoá")
            return
        self._launch(tasks, "YouTube")

    def _start_chrome(self) -> None:
        tasks = self._build_chrome_tasks()
        if tasks is None:
            return
        if not tasks:
            self._log("[LỖI] Chrome: chưa nhập từ khoá")
            return
        self._launch(tasks, "Chrome")

    def _start_all(self) -> None:
        yt = self._build_youtube_tasks()
        cr = self._build_chrome_tasks()
        if yt is None or cr is None:
            return  # validation error already logged
        combined = (yt or []) + (cr or [])
        if not combined:
            self._log("[LỖI] Cả YouTube và Chrome đều chưa có từ khoá")
            return
        self._log(f"[ALL] YouTube={len(yt or [])} tasks, Chrome={len(cr or [])} tasks")
        self._launch(combined, "YT+Chrome")

    def _launch(self, tasks: list[Task], label: str) -> None:
        if self._worker and self._worker.is_alive():
            self._log("[LỖI] Một workflow đang chạy. Cancel trước nhé.")
            return
        selected = self._selected_devices()
        if not selected:
            self._log("[LỖI] Chưa chọn thiết bị")
            return

        workers = int(self.workers_spin.value())
        self._stop_event.clear()
        for btn in (self.btn_start_yt, self.btn_start_cr, self.btn_start_all):
            btn.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self._log(f"[RUN/{label}] devices={len(selected)} workers={workers} tasks={len(tasks)}")
        for t in tasks:
            self._log(f"  - {t}  opts={t.opts}")

        def runner() -> None:
            adb = ADB()
            results = core.run_tasks(
                adb,
                tasks,
                selected,
                workers=workers,
                stop_event=self._stop_event,
                log_cb=self._log,
            )
            total = sum(v for r in results.values() for k, v in r.items() if not k.startswith("_"))
            self._log(f"[XONG/{label}] Tổng loop hoàn thành: {total}")
            self._signal.finished.emit()

        self._worker = threading.Thread(target=runner, daemon=True)
        self._worker.start()

    @QtCore.Slot()
    def _finish_ui(self) -> None:
        for btn in (self.btn_start_yt, self.btn_start_cr, self.btn_start_all):
            btn.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._refresh_history()

    def _cancel(self) -> None:
        self._stop_event.set()
        self.btn_cancel.setEnabled(False)
        self._log("[HỦY] Yêu cầu dừng — đợi loop hiện tại kết thúc...")

    def _go_home_devices(self) -> None:
        """Force-stop YT + Chrome then KEYCODE_HOME on every selected device.

        Runs in a background thread so the GUI stays responsive — clearing
        20 devices serially over ADB can take a few seconds. Allowed while
        a workflow is running (the workflow's worker thread will resume on
        whatever screen the device is on; this just gives a clean reset).
        """
        selected = self._selected_devices()
        if not selected:
            self._log("[HOME] Chưa chọn thiết bị")
            return

        def runner() -> None:
            adb = ADB()
            for serial in selected:
                try:
                    adb.shell(serial, "am force-stop com.google.android.youtube")
                    adb.shell(serial, "am force-stop com.android.chrome")
                    adb.shell(serial, "am kill-all")
                    adb.shell(serial, "input keyevent KEYCODE_HOME")
                    self._log(f"[HOME] {serial} → đã về Home")
                except Exception as e:
                    self._log(f"[HOME] {serial} LỖI: {e}")
            self._log(f"[HOME] Xong, {len(selected)} thiết bị")

        threading.Thread(target=runner, daemon=True).start()


def main() -> int:
    app = QtWidgets.QApplication([])
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
