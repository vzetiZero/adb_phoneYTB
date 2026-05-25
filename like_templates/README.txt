like_templates/ — đặt ảnh icon Like ở đây để code dò bằng OpenCV.

Cách chụp:
  1. Mở YouTube Shorts trên 1 thiết bị.
  2. Chụp màn hình (Power + Volume Down).
  3. Pull về máy: adb -s <serial> pull /sdcard/Pictures/Screenshots/<filename>.png .
  4. Mở ảnh, crop sát vào icon trái tim (Like). Lưu PNG vào folder này.

Lưu ý:
  - Có thể đặt nhiều file (như_state.png, like_unselected.png) — code thử tất cả.
  - Multi-scale matching tự xử lý DPI khác nhau, không cần resize.
  - Threshold mặc định 0.75; nếu false-positive nhiều, sửa LIKE_TEMPLATE_THRESHOLD
    trong adb_time_sync/youtube_flow.py.
  - Nếu folder rỗng, code sẽ skip bước này và rơi xuống double-tap centre.
