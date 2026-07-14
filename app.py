"""BoxPhone Automation — Desktop App via FastAPI + PyWebview.

Single entry point: starts FastAPI backend + opens native desktop window.
No browser needed.
"""

import os
import sys
import threading
import time

# Python 3.14 has circular import issues with asyncio/typing that break uvicorn.
# Require Python 3.9 - 3.13 for stable operation.
if sys.version_info >= (3, 14):
    print(f"[ERROR] Python {sys.version_info.major}.{sys.version_info.minor} is not supported.")
    print("[ERROR] Please use Python 3.12 or 3.13 instead.")
    print("[ERROR] Download: https://www.python.org/downloads/")
    input("Press Enter to exit...")
    sys.exit(1)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Initialize custom ADB server port from config.json before any other imports
try:
    import json
    from pathlib import Path

    config_path = Path(PROJECT_ROOT) / "config.json"
    if config_path.exists():
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        custom_port = config_data.get("adb_server_port")
        if custom_port:
            os.environ["ANDROID_ADB_SERVER_PORT"] = str(custom_port)
except Exception:
    pass


def start_api_server():
    """Start FastAPI server in background thread."""
    import uvicorn
    from api.main import app

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


def wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Block until the API server is ready or timeout."""
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    print("[INFO] Starting BoxPhone Automation...")

    # 1. Start API server in background
    server_thread = threading.Thread(target=start_api_server, daemon=True)
    server_thread.start()

    # 2. Wait for server to be ready
    api_url = "http://127.0.0.1:8765/api/status"
    print("[INFO] Waiting for API server...")
    if not wait_for_server(api_url):
        print("[ERROR] API server failed to start within timeout")
        return 1

    print("[OK] API server ready on http://127.0.0.1:8765")

    # 3. Open native desktop window — NO browser
    try:
        import webview

        window = webview.create_window(
            "BoxPhone Automation",
            "http://127.0.0.1:8765",
            width=1280,
            height=820,
            min_size=(960, 600),
            background_color="#f8fafc",
            text_select=True,
            easy_drag=True,
            frameless=False,
        )
        webview.start(debug=False)
    except ImportError:
        print("[WARNING] pywebview not installed. Opening in browser instead.")
        import webbrowser
        webbrowser.open("http://127.0.0.1:8765")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
