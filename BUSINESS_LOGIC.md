# BoxPhone Auto — Business Logic & Quy trình triển khai

Tài liệu này mô tả toàn bộ **logic nghiệp vụ**, **luồng triển khai**, và **cách gọi hàm** của dự án `AUTO PHONE ADB` (đóng gói thành `BoxPhone-Auto`). Mục tiêu của ứng dụng: **giả lập người dùng thật** thao tác YouTube / Chrome trên nhiều BoxPhone (điện thoại Android cloud) song song qua ADB, phục vụ tăng tương tác / xem báo / lướt Shorts.

---

## 1. Tổng quan kiến trúc

```
┌──────────────────────────────────────────────────────────────┐
│  GUI (PySide6)              gui_app.py                        │
│  ───────────                                                  │
│  - Tab Run    : chọn thiết bị + form YouTube/Chrome + Start   │
│  - Tab Devices: alias CRUD + import từ ADB                    │
│  - Tab History: 200 dòng task_runs gần nhất                   │
└──────────────────────────┬───────────────────────────────────┘
                           │ build Task[] → core.run_tasks()
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Orchestrator             main.py                             │
│  ────────────                                                 │
│  - run_tasks(): ThreadPoolExecutor song song theo device      │
│  - _runner_for_device(): chạy 1 device, log có prefix [DEV X] │
│  - Persist kết quả vào SQLite (db.log_task_run)               │
└──────────────────────────┬───────────────────────────────────┘
                           │ for each serial:
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Per-device runner        adb_time_sync/task_runner.py        │
│  ───────────────────                                          │
│  - _preflight()       : kiểm trạng thái ADB + cài app + wake  │
│  - apply_screen_config: khoá portrait                         │
│  - vòng for Task ⇒ _run_one_task()                            │
└────────────┬─────────────────────────────┬───────────────────┘
             │ app=youtube                 │ app=chrome
             ▼                             ▼
┌────────────────────────┐    ┌──────────────────────────────┐
│ youtube_flow.py        │    │ chrome_flow.py               │
│ - _open_youtube        │    │ - _open_search_url           │
│ - _go_to_shorts        │    │ - _scroll_serp               │
│ - _scroll_reels        │    │ - _pick_serp_result          │
│ - _maybe_like          │    │ - _browse_landing            │
│ - _post_comment        │    │                              │
│ - _do_search           │    │                              │
│ - _watch_video         │    │                              │
└────────────────────────┘    └──────────────────────────────┘
             │                             │
             └──────────────┬──────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  Hạ tầng ADB / UI         adb_time_sync/*.py                  │
│  ────────────────                                             │
│  - adb.py        : wrapper subprocess `adb` (UTF-8, timeout)  │
│  - human.py      : tap/swipe + log-normal sleep + jitter      │
│  - ui_state.py   : uiautomator dump + parse XML → UiNode      │
│  - text_input.py : gõ Unicode qua clipboard / ADBKeyboard     │
│  - wake.py       : đánh thức máy nếu mWakefulness=asleep      │
│  - screen.py     : khoá hướng portrait, screen timeout        │
│  - language.py   : đổi locale máy (không bắt buộc)            │
│  - time_sync.py  : đồng bộ giờ (không bắt buộc)               │
│  - reset_device.py: factory reset (không bắt buộc)            │
└──────────────────────────────────────────────────────────────┘
```

**Điểm khác biệt**: dự án **KHÔNG** dùng `uiautomator2` — mọi tương tác đều qua `adb shell input ...` + `uiautomator dump` cho mục đích đơn giản hóa môi trường (không cần cài server Python trên thiết bị, đỡ xung đột với BoxPhone đã root sẵn).

---

## 2. Mô hình dữ liệu (SQLite — `app.db`)

File: [db.py](db.py)

| Bảng | Cột | Ý nghĩa |
|------|-----|---------|
| `devices` | `ip PK, name, width, height, last_seen` | Alias thiết bị + kích thước màn hình cuối đo được |
| `task_runs` | `id, ts, serial, app, keyword, requested_loops, done_loops, status, note` | Lịch sử mỗi task chạy xong (status: `ok` / `partial` / `fail`) |
| `coords_cache` | `width, height, role, x, y` (PK = 3 đầu) | Cache toạ độ UI theo độ phân giải (chuẩn bị cho mở rộng) |

Các hàm chính:
- `init_db()` — tạo schema lúc khởi động
- `list_devices()` / `upsert_device(ip, name)` / `update_device_name(ip, name)` / `touch_device(ip, w, h)`
- `log_task_run(serial, app, keyword, requested, done, status, note='')`
- `recent_runs(limit=100)` — đổ vào tab History

---

## 3. Định dạng Task

File: [adb_time_sync/task_runner.py](adb_time_sync/task_runner.py)

```
app|keyword|loops[|key=value,key=value,...]
```

- `app` ∈ `{youtube, chrome}`
- `keyword`: từ khoá tìm kiếm (Unicode OK)
- `loops`: số lần lặp task trên 1 device (≥ 1)
- `opts` (tuỳ chọn, phân tách bởi `,`):
  - **YouTube**: `reels_min`, `reels_max`, `delay_min`, `delay_max` (giây/reel), `like_rate ∈ [0,1]`, `comment_rate ∈ [0,1]`, `watch_min`, `watch_max` (giây xem video sau search), `shorts_time_limit` (giây max ở Shorts)
  - **Chrome**: `watch_min`, `watch_max` (giây lướt trang đích)

Có 2 nguồn task:
1. **GUI** (`gui_app.py`): build `list[Task]` từ form rồi đẩy thẳng vào `core.run_tasks()`.
2. **CLI** (`main.py run --tasks tasks.txt`): đọc [tasks.txt](tasks.txt), parse bằng `parse_task_line()`.

Pool comment: [comments.txt](comments.txt) — 1 câu/dòng, dùng cho `_post_comment()` của YouTube.

---

## 4. Luồng triển khai chi tiết

### 4.1. Khởi động & chọn thiết bị (GUI)

1. `MainWindow.__init__()` → `db.init_db()` → build 3 tab.
2. `_refresh_devices()` gọi `ADB.devices_all()` → liệt kê `serial state` từ lệnh `adb devices`.
3. Tab Run: người dùng tick các thiết bị, đặt số workers (số device chạy song song), điền keyword + tham số.
4. Bấm **▶ Start YouTube / Start Chrome / Start All** → `_build_*_tasks()` validate → `_launch(tasks, label)`.

### 4.2. Orchestration song song

File: [main.py:run_tasks](main.py)

```
run_tasks(adb, tasks, serials, workers, stop_event, log_cb, watch_seconds)
 └── ThreadPoolExecutor(max_workers=workers)
      └── for each serial: _runner_for_device(...)
           ├── db.touch_device(serial)
           └── run_tasks_on_device(...)  ← per-device pipeline
```

Sau khi tất cả future hoàn thành: ghi `db.log_task_run()` cho từng `(serial, task)`.

### 4.3. Per-device pipeline

File: [adb_time_sync/task_runner.py:run_tasks_on_device](adb_time_sync/task_runner.py)

```
run_tasks_on_device(adb, serial, tasks, ...)
 ├── _preflight()                 ← state == "device" + pm list packages
 │    ├── pkg required: com.google.android.youtube / com.android.chrome
 │    └── wake_device() (best-effort)
 ├── apply_screen_config(lock_portrait=True)   ← chống xoay ngang giữa chừng
 └── for task in tasks:
      ├── home(adb, serial)       ← về home để bắt đầu sạch
      └── _run_one_task(task)
           └── run_youtube_task(...) | run_chrome_task(...)
```

Nếu preflight fail (ADB offline / chưa cài app) → trả `{"_skipped": 0}` và log `[PREFLIGHT] SKIP`.

### 4.4. YouTube flow

File: [adb_time_sync/youtube_flow.py:run_youtube_task](adb_time_sync/youtube_flow.py)

Mỗi loop:
1. `_open_youtube()` — **Clean launch**: `home` → `am force-stop com.google.android.youtube` → `am kill-all` → `monkey -p ... LAUNCHER` → verify foreground. Bước này bắt buộc vì BoxPhone hay khởi động vào session Shorts đang dính quảng cáo cũ; nếu không force-stop thì swipe-up sẽ tap vào nút "Visit" của ad thay vì cuộn sang reel kế tiếp.
2. `_go_to_shorts()` — deep-link `vnd.youtube://shorts` → fallback bottom-nav theo content-desc (multi-locale: `Shorts`, `쇼츠`, `ショート`, `短视频`) → fallback blind tap ~30%×96% màn hình.
3. `_scroll_reels(n_reels)` — vòng `n = random[reels_min..reels_max]`:
   - `ensure_portrait()` đầu mỗi reel (watchdog rotation).
   - `_ensure_in_shorts()` mỗi reel (resource-id `reel_player_page_container` hoặc nút Like/Comment) — nếu rớt khỏi Shorts thì vào lại tối đa 2 lần.
   - Sleep `random[delay_min..delay_max]` (xem reel).
   - Với xác suất `like_rate`: `_maybe_like()` — 4 tầng fallback:
     1. resource-id `reel_like_button` / `like_button`
     2. content-desc multi-locale (`좋아요`, `Like`, `Thích`...)
     3. OpenCV template match với mọi PNG trong `like_templates/` (đa scale)
     4. Double-tap giữa màn hình
   - `swipe_up()` sang reel kế tiếp.
   - Nếu `shorts_time_limit_sec > 0` mà vượt → break sớm.
4. `back()` thoát Shorts.

> **Comment đã bị gỡ bỏ** theo yêu cầu vận hành: tỉ lệ false-positive cao (gõ nhầm ô tìm kiếm) và rủi ro spam-ban YouTube. File `comments.txt` còn lưu nhưng không được nạp.
5. `_do_search(keyword)`:
   - Ưu tiên deep-link `vnd.youtube:///results?search_query=...`.
   - Fallback: `_open_search()` (intent SEARCH / tap nút search / tap góc trên-phải) → `type_text(keyword, submit=True)`.
6. `_tap_top_result()` — quét XML tìm node có `resource-id` chứa `"video"` hoặc node `ViewGroup` clickable đủ to → chọn ngẫu nhiên top-3.
7. `_watch_video(min, max)` — xem `random[watch_min..watch_max]` giây với pause/resume ngẫu nhiên (xác suất 25% tap giữa, 15% cuộn comment).
8. `back()` về SERP.

### 4.5. Chrome flow

File: [adb_time_sync/chrome_flow.py:run_chrome_task](adb_time_sync/chrome_flow.py)

Mỗi loop:
1. `_open_search_url(keyword)` — bắn intent `VIEW` tới `https://www.google.com/search?q=<encoded>` với `--activity-clear-task` để tránh kế thừa state tab cũ. Verify Chrome foreground.
2. `_scroll_serp()` — `swipe_up` ngẫu nhiên 1–3 lần + thỉnh thoảng `swipe_down` (xác suất 35%) để giả người đang đọc lướt.
3. `_pick_serp_result()` — chiến thuật:
   - Dump UI, tìm node clickable kiểu `TextView` / `View` / `LinkTextView` có text ≥ 15 ký tự, **không** chứa nhãn quảng cáo (`AD_LABELS` multi-locale: `광고`, `Sponsored`, `Quảng cáo`, `广告`, `広告`, `Werbung`, `Annonce`, `Anuncio`, `Iklan`, `โฆษณา`...).
   - Loại node nằm trong vùng omnibox (`y < 12% height`).
   - Sort theo `y1` → tap **kết quả #1 không phải quảng cáo**.
   - Fallback (khi WebView canvas-render, không lộ text): tap toạ độ `(random 10–35% × width, 30% × height)`.
4. `_browse_landing(seconds)` — lướt trang đích `random[watch_min..watch_max]` giây:
   - Vòng lặp: nghỉ đọc `random 5–18s` → 75% `swipe_up` / 25% `swipe_down` → 10% tap giữa rồi `back` (giả vô tình bấm link).
5. `back()` về SERP.

### 4.6. Hạ tầng nhân tính hoá (chống detect bot)

- `lognormal_sleep(mu, sigma, lo, hi)` — phân phối log-normal cho mọi delay (median ~2s, long tail).
- `tap()` thêm jitter `±8px`; `swipe()` jitter `±25px` toạ độ + duration ngẫu nhiên.
- Mọi sleep dài đều dùng `interruptible_sleep(stop_event, ...)` để bấm **⏹ Cancel** dừng được trong vài giây.
- `apply_screen_config(lock_portrait=True)` → khoá portrait tránh BoxPhone tự xoay khi YouTube fullscreen.
- `wake_device()` nếu `dumpsys power` báo `wakefulness=asleep`.

### 4.7. Chống xoay ngang — phòng thủ 3 lớp

YouTube và Chrome khi gặp video 16:9 sẽ chủ động gọi `setRequestedOrientation(LANDSCAPE)` ở Activity level, **ghi đè** mọi setting hệ thống. Để chống, pipeline dùng 3 lớp phòng thủ:

**Lớp 1 — Lock hệ thống** (mỗi device, gọi 1 lần sau preflight):
- `apply_screen_config(lock_portrait=True)` trong [adb_time_sync/screen.py](adb_time_sync/screen.py):
  - `settings put system accelerometer_rotation 0` (tắt auto-rotate cảm biến)
  - `settings put system user_rotation 0` (đặt rotation hiện tại = portrait)
  - `wm set-user-rotation lock 0` + `wm set-fix-to-user-rotation enabled`

**Lớp 2 — Ignore app orientation requests** (Android 12+, gọi 1 lần sau lớp 1):
- `try_disable_orientation_requests(adb, serial)` → `cmd window set-ignore-orientation-request true`.
- Đây là **fix sạch nhất**: WindowManager bỏ qua mọi `setRequestedOrientation()` từ app. YouTube/Chrome có gọi cũng không xoay được.
- Android < 12 (BoxPhone Hàn cũ Android 9-11): command return non-zero, log `"ROM không hỗ trợ ignore-orientation-request"`. Rơi sang lớp 3.

**Lớp 3 — Watchdog `ensure_portrait()`** (chạy trong vòng lặp, tự hồi phục):
- File: `screen.ensure_portrait(adb, serial, log_cb)` đọc rotation hiện tại bằng `get_current_rotation()` — thử 3 cách theo thứ tự:
  1. `dumpsys input` → `SurfaceOrientation: N` (cross Android 8-14)
  2. `settings get system user_rotation` (fallback)
  3. `dumpsys window displays` → `mCurRotation=ROTATION_N` (fallback 2)
- Nếu rotation ≠ 0 → re-apply `apply_screen_config(lock_portrait=True)` + `try_disable_orientation_requests` + log `[ORI] Phát hiện rotation=N, ép lại portrait`.
- Gọi định kỳ trong các loop dài:
  - [youtube_flow._watch_video](adb_time_sync/youtube_flow.py) — đầu mỗi nap (8-20s)
  - [youtube_flow._scroll_reels](adb_time_sync/youtube_flow.py) — đầu mỗi reel
  - [chrome_flow._browse_landing](adb_time_sync/chrome_flow.py) — đầu mỗi nap (5-18s)
- Cost: ~100ms `dumpsys input` mỗi lần check, không đáng kể so với watch time 60-300s.

**Điều chỉnh bổ trợ**:
- `_watch_video` đổi vùng tap pause/resume từ `(width/2, height/2)` sang `(width/2, 0.40*height)` — tránh trúng nút fullscreen ở góc dưới phải video player (player chiếm ~33% chiều cao trên cùng khi portrait).
- `_browse_landing` đổi vùng tap mid-page từ `(0.5, 0.5)` sang `(0.5, 0.35)` — tránh trúng poster video ở giữa bài báo.

---

## 5. Vòng đời 1 phiên chạy (sequence)

```
User bấm Start All
  │
  ▼
gui_app._launch(tasks, "YT+Chrome")
  │ spawn Thread runner
  ▼
core.run_tasks(adb, tasks, serials, workers=4)
  │ ThreadPoolExecutor x 4
  ├── Thread-1 → device 192.168.5.11
  ├── Thread-2 → device 192.168.5.12
  ├── Thread-3 → device 192.168.5.13
  └── Thread-4 → device 192.168.5.14
        │
        ▼
   _preflight() OK → apply_screen_config(portrait)
        │
        ├── Task youtube|baomoi|5
        │    └── 5 × run_youtube_task() [shorts→search→watch]
        │
        └── Task chrome|tin nóng hôm nay|3
             └── 3 × run_chrome_task()  [google→SERP→tap→lướt]
        │
        ▼
   results = {"youtube|baomoi|5": 5, "chrome|tin nóng hôm nay|3": 3}
        │
        ▼
db.log_task_run(...)  ← persist
  │
  ▼
GUI._finish_ui() → reload History tab
```

---

## 6. Bổ sung từ khoá mới — ví dụ `naver news` → `https://news.naver.com/`

> **Yêu cầu**: thêm 1 keyword "naver news" vào danh mục Google search; ứng dụng sẽ tìm trên Google, click vào kết quả bài báo trỏ tới `https://news.naver.com/` để mở báo và giả lập người xem trang.

### 6.1. Cách dùng pipeline hiện có (KHÔNG cần sửa code)

Vì SERP của Google khi search **"naver news"** trả về `news.naver.com` ở vị trí top-1 (organic, không phải ad), nên flow Chrome hiện tại **đã đủ** xử lý:

**Cách 1 — GUI (Tab Run → Chrome):**
- Mục **Từ khoá Google**, nhập:
  ```
  naver news
  ```
- **Số lần lặp mỗi từ khoá**: ví dụ `3`
- **Lướt trang đích**: `min = 90s`, `max = 240s` (cho thời gian xem báo đủ dài như người thật)
- Chọn các thiết bị → bấm **▶ Start Chrome**.

**Cách 2 — CLI (`tasks.txt`):**
```
# Mở Google → search "naver news" → click news.naver.com → đọc 90–240s
chrome|naver news|3|watch_min=90,watch_max=240
```
Rồi chạy:
```
python main.py run --tasks tasks.txt --workers 4
```

**Khi đó pipeline sẽ:**
1. `_open_search_url("naver news")` → mở `https://www.google.com/search?q=naver+news` trong Chrome.
2. `_scroll_serp()` cuộn 1–3 lần (giả đọc lướt).
3. `_pick_serp_result()` quét node clickable, **bỏ qua quảng cáo** (đã có `광고` trong `AD_LABELS` cho thị trường Hàn Quốc), chọn kết quả organic top → tap → mở `https://news.naver.com/`.
4. `_browse_landing(90..240s)` cuộn lên/xuống xen kẽ với pause đọc 5–18s, thỉnh thoảng tap link rồi `back`.
5. `back()` quay về SERP, lặp lại `loops` lần (mỗi lần là 1 session độc lập do `--activity-clear-task` trên intent).

### 6.2. Whitelist domain `prefer_domain` (ĐÃ TRIỂN KHAI)

Pipeline Chrome hiện đã hỗ trợ tham số `prefer_domain` để **ép click đúng** domain mong muốn. Nếu không tìm thấy domain trên SERP (Google không xếp hạng nó, hoặc bị ad chen trước), pipeline tự động **mở thẳng URL** `https://<domain>/` thay vì click bừa.

**Vị trí code:**
- [adb_time_sync/chrome_flow.py](adb_time_sync/chrome_flow.py): `_pick_serp_result(prefer_domain=...)`, `_open_chrome_url`, `_normalize_domain`, nhánh fallback trong `run_chrome_task`.
- [adb_time_sync/task_runner.py](adb_time_sync/task_runner.py): truyền `task.opts["prefer_domain"]` vào `run_chrome_task`.
- [gui_app.py](gui_app.py): tab Chrome chấp nhận cú pháp `keyword | domain` cho mỗi dòng từ khoá.

**Luồng thực thi khi `prefer_domain` được set:**

```
_open_search_url(keyword)               → mở Google search "naver news"
_scroll_serp()                          → cuộn SERP 1–3 lần
_pick_serp_result(prefer_domain=...)    → quét node, chỉ tap nếu text chứa domain
   │
   ├── thấy → tap node đó              ✅ click qua Google (organic referer)
   │
   └── không thấy
        ↓
   _scroll_serp() lần 2 + retry _pick_serp_result()
        ↓
   vẫn không thấy → _open_chrome_url("https://news.naver.com/")  ⚠ fallback direct
        ↓
_browse_landing(watch_min..watch_max)   → đọc báo, cuộn lên/xuống xen kẽ
back()                                  → về SERP (hoặc Home)
```

**Cách sử dụng — GUI:**
- Tab Chrome → ô **Từ khoá Google** gõ:
  ```
  naver news | news.naver.com
  tin nóng hôm nay
  giá vàng | giavang.net
  ```
- Dòng có dấu `|` → keyword bên trái, domain ưu tiên bên phải.
- Dòng không có `|` → giữ behavior cũ (tap kết quả #1 non-ad).

**Cách sử dụng — `tasks.txt` / CLI:**
```
chrome|naver news|3|prefer_domain=news.naver.com,watch_min=90,watch_max=240
```

**Chuẩn hoá domain (`_normalize_domain`):**
- Tự bỏ `https://`, `http://`, dấu `/` trailing, prefix `www.`.
- Nhập `"www.news.naver.com/"` cũng tương đương `"news.naver.com"`.
- So sánh `substring` lower-case trên cả `text` + `content-desc` của node SERP — bắt được cả khi Google hiển thị URL gọn (`news.naver.com › ...`) hay tiêu đề kèm hostname.

**Trade-off đã chọn:**
- Ưu tiên giữ "referer = Google" (`prefer_domain` match) → tốt cho analytics Naver / GSC.
- Chỉ khi cuộn 2 lần SERP vẫn không thấy mới direct-open → vẫn giả lập được "user xem trang" đầy đủ thời lượng.
- Không tap kết quả #1 random khi `prefer_domain` set — tránh click nhầm trang khác.

### 6.3. Lưu ý vận hành cho thị trường Hàn

- Bàn phím / IME mặc định trên BoxPhone Hàn không phải Latin → keyword tiếng Hàn nên gõ qua **clipboard** (`type_text()` đã ưu tiên `cmd clipboard set-text` từ Android 13+).
- Một số bài Naver có popup tuổi 19+ / yêu cầu login → `_browse_landing` sẽ cuộn trên popup, không gây lỗi nhưng không xem được nội dung. Nếu cần né, có thể thêm template match cho nút "닫기" (Đóng) — chưa có trong codebase hiện tại.
- Naver tracking dùng **referer** + **time-on-page** + **scroll depth** — pipeline hiện tại đáp ứng cả 3 (referer = Google search, time = `watch_min..watch_max`, scroll = vòng `_browse_landing`).

---

## 7. Các điểm mở rộng dễ thấy

| Mở rộng | Hàm cần sửa | Mức độ |
|---------|-------------|--------|
| Thêm `prefer_domain` lọc SERP | `chrome_flow._pick_serp_result` + `task_runner._parse_opts` | Nhỏ |
| Mở thẳng URL không qua Google | `chrome_flow.run_chrome_task` thêm nhánh `direct_url` | Nhỏ |
| Lên lịch chạy định kỳ | thêm scheduler quanh `core.run_tasks` (cron / `schedule`) | Vừa |
| Lưu screenshot mỗi loop | `screencap_png` đã có ở `human.py`, chỉ cần gọi cuối `_run_one_task` | Nhỏ |
| Báo cáo CSV theo ngày | query `task_runs` group by date trong tab History | Nhỏ |
| Đo `width/height` ngay sau preflight và `db.touch_device(w, h)` | `task_runner._preflight` đã có `get_size`, chỉ cần truyền vào `touch_device` | Nhỏ |

---

## 8. Tham chiếu nhanh các file

| File | Vai trò |
|------|---------|
| [main.py](main.py) | CLI entry + orchestrator `run_tasks` (song song theo device) |
| [gui_app.py](gui_app.py) | PySide6 GUI — 3 tab Run / Devices / History |
| [db.py](db.py) | SQLite layer (devices, task_runs, coords_cache) |
| [tasks.txt](tasks.txt) | Cấu hình task cho CLI |
| [comments.txt](comments.txt) | Pool comment ngẫu nhiên cho YouTube |
| [adb_time_sync/task_runner.py](adb_time_sync/task_runner.py) | Parse task + pipeline per-device + preflight |
| [adb_time_sync/youtube_flow.py](adb_time_sync/youtube_flow.py) | Shorts + Search + Watch + Like + Comment |
| [adb_time_sync/chrome_flow.py](adb_time_sync/chrome_flow.py) | Google SERP + tap result + lướt landing |
| [adb_time_sync/adb.py](adb_time_sync/adb.py) | Wrapper `subprocess` cho lệnh `adb` (UTF-8, timeout, no console flash) |
| [adb_time_sync/human.py](adb_time_sync/human.py) | tap / swipe / sleep nhân tính hoá, get_size, screencap |
| [adb_time_sync/ui_state.py](adb_time_sync/ui_state.py) | `uiautomator dump` → parse XML → `UiNode` + tìm theo resource-id / text |
| [adb_time_sync/text_input.py](adb_time_sync/text_input.py) | Gõ Unicode (clipboard → ADBKeyboard → input text) |
| [adb_time_sync/wake.py](adb_time_sync/wake.py) | Đọc `dumpsys power` + đánh thức |
| [adb_time_sync/screen.py](adb_time_sync/screen.py) | Khoá portrait + screen timeout |
| [adb_time_sync/language.py](adb_time_sync/language.py) | Đổi locale máy (không gọi trong pipeline mặc định) |
| [adb_time_sync/time_sync.py](adb_time_sync/time_sync.py) | Đồng bộ giờ thiết bị (không gọi trong pipeline mặc định) |
| [adb_time_sync/reset_device.py](adb_time_sync/reset_device.py) | Factory reset (không gọi trong pipeline mặc định) |
| [like_templates/](like_templates/) | PNG mẫu icon Like (OpenCV template match) |
| [Install.bat](Install.bat) / [Start.bat](Start.bat) / [build.bat](build.bat) | Script Windows: cài deps / chạy / đóng gói PyInstaller |
| [BoxPhone-Auto.spec](BoxPhone-Auto.spec) | PyInstaller spec |
