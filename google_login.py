import json
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from adb_time_sync.adb import ADB

try:
    import uiautomator2 as u2  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    u2 = None


def run_command(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


class ADBHelper:
    def __init__(self, adb_path: str = "adb", logger=None):
        self.adb_path = adb_path
        self.logger = logger
        self.adb = ADB(adb_path=adb_path, timeout_sec=20, verbose=False)

    def _run(self, serial: str, *args: str):
        return self.adb.shell(serial, " ".join(args))

    def lock_portrait(self, serial: str) -> None:
        self._run(serial, "settings put system accelerometer_rotation 0")
        self._run(serial, "settings put system user_rotation 0")
        self._run(serial, "wm set-user-rotation lock 0")
        self._run(serial, "wm set-fix-to-user-rotation enabled")

    def set_device_locale(self, serial: str, locale: str) -> None:
        self._run(serial, f"settings put system system_locales {locale}")

    def clear_device_screenshots(self, serial: str) -> None:
        self._run(serial, "rm -rf /sdcard/screenshots")


class _FallbackElement:
    def __init__(self, automation, node, selector):
        self.automation = automation
        self.node = node
        self.selector = selector
        self.exists = node is not None
        self.info = {"className": node.get("class", "") if node else ""}

    def wait(self, timeout: int = 0) -> bool:
        return self.exists

    def click(self) -> bool:
        if not self.node:
            return False
        return self.automation._tap_node(self.node)

    def clear_text(self) -> bool:
        return True

    def set_text(self, text: str) -> bool:
        return self.automation._set_text(text)


class _FallbackUIDevice:
    def __init__(self, automation):
        self.automation = automation
        self.info = {"screenOn": True}

    def __call__(self, **kwargs):
        selector = {}
        for key, value in kwargs.items():
            selector[key] = value
        return _FallbackElement(self.automation, self.automation._find_node(selector), selector)

    def press(self, key):
        if isinstance(key, int):
            self.automation._adb_keyevent(key)
            return True
        mapping = {"power": 26, "enter": 66, "home": 3}
        return self.automation._adb_keyevent(mapping.get(key, 66))

    def swipe(self, x1, y1, x2, y2, duration=0.5):
        self.automation._adb_swipe(x1, y1, x2, y2, duration)

    def app_start(self, package, activity=None):
        self.automation._adb_start_app(package, activity)

    def set_orientation(self, orientation):
        return True

    def freeze_rotation(self):
        return True


class GoogleLoginAutomation:
    def __init__(self, serial: str, config: Dict, logger):
        self.serial = serial
        self.config = config
        self.logger = logger
        self.device = None
        self.screenshot_dir = Path(config.get("screenshot_dir", "./screenshots"))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._adb_path = config.get("adb_path", "adb")
        self._use_fallback = False

    def _collect_texts(self, login_config: Dict, key: str, fallback: Optional[list] = None) -> list:
        """Collect localized button/text candidates from config, including Korean variants."""
        texts = []
        localized = login_config.get("localized_texts", {}) or {}
        if isinstance(localized, dict):
            localized_values = localized.get(key, {})
            if isinstance(localized_values, dict):
                for lang_texts in localized_values.values():
                    if isinstance(lang_texts, list):
                        texts.extend(lang_texts)
                    elif isinstance(lang_texts, str):
                        texts.append(lang_texts)

        values = login_config.get(key, fallback or [])
        if isinstance(values, list):
            texts.extend(values)
        elif isinstance(values, str):
            texts.append(values)

        if fallback and not values:
            texts.extend(fallback)

        seen = set()
        ordered = []
        for text in texts:
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered

    def _get_login_config(self) -> Dict:
        """Return the base login config merged with the selected device profile."""
        login_config = dict(self.config.get("google_login", {}))
        profile_name = self.config.get("device_profile", "generic")
        profiles = login_config.get("profiles", {}) or {}
        profile_config = profiles.get(profile_name, {}) or {}

        for key in [
            "wait_timeout",
            "cleanup_screenshots_on_success",
            "sign_in_texts",
            "blocking_texts",
            "failure_texts",
            "username_field",
            "password_field",
            "next_button",
            "login_button",
            "accept_button",
            "final_button",
            "lock_portrait",
            "uiautomator2_reconnect",
        ]:
            if key not in profile_config:
                continue

            base_value = login_config.get(key)
            override_value = profile_config.get(key)
            if isinstance(base_value, dict) and isinstance(override_value, dict):
                login_config[key] = {**base_value, **override_value}
            else:
                login_config[key] = override_value

        return login_config

    def connect(self, login_config: Optional[Dict] = None) -> bool:
        """Connect to device and prepare it for Google login automation."""
        last_exc = None
        config = login_config or self._get_login_config()
        enable_reconnect = config.get("uiautomator2_reconnect", True)

        self._clear_device_screenshots()
        if config.get("lock_portrait", True):
            try:
                helper = ADBHelper(adb_path=self._adb_path, logger=self.logger)
                helper.lock_portrait(self.serial)
            except Exception as e:
                self.logger.debug(f"ADBHelper pre-connect portrait lock failed: {e}")
        self._set_korean_locale()

        self.logger.info("Waiting for system to stabilize after locale change...")
        time.sleep(3)

        if u2 is not None:
            for attempt in range(1, 3):
                try:
                    self.device = u2.connect(self.serial)
                    self.logger.info(f"Connected to device {self.serial}")
                    time.sleep(1)
                    self._ensure_screen_awake()
                    if config.get("lock_portrait", True):
                        self._lock_portrait_orientation()
                    return True
                except Exception as e:
                    last_exc = e
                    self.logger.warning(f"Failed to connect to {self.serial} on attempt {attempt}: {e}")
                    if not enable_reconnect:
                        break
                    try:
                        self.device = u2.connect(f"adb://{self.serial}")
                        self.logger.info(f"Connected to device {self.serial} via adb://")
                        time.sleep(1)
                        self._ensure_screen_awake()
                        if config.get("lock_portrait", True):
                            self._lock_portrait_orientation()
                        return True
                    except Exception as e2:
                        last_exc = e2
                        self.logger.warning(f"Retry via adb:// failed for {self.serial}: {e2}")
                    time.sleep(2)
        else:
            self._use_fallback = True
            self.device = _FallbackUIDevice(self)
            self.logger.info(f"uiautomator2 not available; using ADB fallback flow for {self.serial}")
            time.sleep(1)
            self._ensure_screen_awake()
            if config.get("lock_portrait", True):
                self._lock_portrait_orientation()
            return True

        self.logger.error(f"Failed to connect to {self.serial}: {last_exc}")
        return False

    def _ensure_screen_awake(self) -> None:
        try:
            screen_on = self.device.info.get("screenOn", True) if self.device else True
            if not screen_on:
                self.logger.info("Screen is off or black; waking device")
                self.device.press("power")
                time.sleep(1)
        except Exception as e:
            self.logger.debug(f"Could not verify or wake screen: {e}")

    def _set_korean_locale(self) -> None:
        try:
            helper = ADBHelper(adb_path=self._adb_path, logger=self.logger)
            helper.set_device_locale(self.serial, "ko-KR")
        except Exception as e:
            self.logger.warning(f"Could not change locale to Korean on {self.serial}: {e}")

    def _clear_device_screenshots(self) -> None:
        try:
            helper = ADBHelper(adb_path=self._adb_path, logger=self.logger)
            helper.clear_device_screenshots(self.serial)
        except Exception as e:
            self.logger.warning(f"Could not clear device screenshots on {self.serial}: {e}")

    def _lock_portrait_orientation(self) -> None:
        """Lock orientation to natural portrait using ADB and UiAutomator2 API."""
        try:
            helper = ADBHelper(adb_path=self._adb_path, logger=self.logger)
            helper.lock_portrait(self.serial)
        except Exception as e:
            self.logger.debug(f"ADBHelper portrait lock failed: {e}")

        try:
            if self.device:
                self.device.set_orientation("natural")
                self.device.freeze_rotation()
                self.logger.info(f"Locked orientation to natural portrait on {self.serial}")
        except Exception as e:
            self.logger.debug(f"Orientation lock failed: {e}")

    def take_screenshot(self, account_email: str, step: str) -> str:
        """No-op. Screen capturing is disabled to prevent latency and file writes."""
        return ""

    def cleanup_screenshots(self, account_email: str) -> int:
        """No-op. Screenshots are disabled."""
        return 0

    def finalize_screenshots(self, account_email: str, login_config: Dict) -> str:
        """No-op. Screenshots are disabled."""
        return ""

    def wait_for_element(self, selector: Dict, timeout: int = 30) -> bool:
        return self.find_element(selector, timeout=timeout) is not None

    def _find_node(self, selector: Dict):
        if not self._use_fallback or not self.device:
            return None
        try:
            dump_path = self._dump_ui_xml()
            if not dump_path:
                return None
            root = ET.parse(dump_path).getroot()
            nodes = [root] + list(root.iter())
            for node in nodes:
                if not hasattr(node, "attrib"):
                    continue
                attrs = node.attrib
                text = attrs.get("text", "") or ""
                resource_id = attrs.get("resource-id", "") or attrs.get("resourceId", "") or ""
                class_name = attrs.get("class", "") or ""
                if self._matches_selector(attrs, selector):
                    return node
            return None
        except Exception as e:
            self.logger.debug(f"Fallback UI dump failed: {e}")
            return None

    def _matches_selector(self, attrs: Dict, selector: Dict) -> bool:
        for strategy, value in self.iter_selector_values(selector):
            if strategy == "resourceId" and (attrs.get("resource-id") == value or attrs.get("resourceId") == value):
                return True
            if strategy == "className" and attrs.get("class") == value:
                return True
            if strategy == "text" and (attrs.get("text") == value or attrs.get("content-desc") == value):
                return True
            if strategy == "textContains" and (value.lower() in (attrs.get("text") or "").lower() or value.lower() in (attrs.get("content-desc") or "").lower()):
                return True
            if strategy == "description" and attrs.get("content-desc") == value:
                return True
            if strategy == "descriptionContains" and value.lower() in (attrs.get("content-desc") or "").lower():
                return True
        return False

    def _dump_ui_xml(self) -> Optional[str]:
        try:
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                tmp_path = tmp.name
            result = run_command([self._adb_path, "-s", self.serial, "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"], timeout=20)
            if result.returncode != 0:
                return None
            pull = run_command([self._adb_path, "-s", self.serial, "pull", "/sdcard/window_dump.xml", tmp_path], timeout=20)
            if pull.returncode != 0:
                return None
            return tmp_path
        except Exception:
            return None

    def _adb_press(self, key_code: int) -> bool:
        result = run_command([self._adb_path, "-s", self.serial, "shell", "input", "keyevent", str(key_code)], timeout=10)
        return result.returncode == 0

    def _adb_swipe(self, x1, y1, x2, y2, duration):
        result = run_command([self._adb_path, "-s", self.serial, "shell", "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2))], timeout=10)
        return result.returncode == 0

    def _adb_start_app(self, package, activity=None):
        if activity:
            result = run_command([self._adb_path, "-s", self.serial, "shell", "am", "start", "-n", f"{package}/{activity}"], timeout=15)
        else:
            result = run_command([self._adb_path, "-s", self.serial, "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=15)
        return result.returncode == 0

    def _tap_node(self, node) -> bool:
        if not node:
            return False
        bounds = node.attrib.get("bounds", "")
        try:
            left, top, right, bottom = [int(v) for v in bounds.replace("[", "").replace("]", "").split("[")[0].split(",")]
        except Exception:
            return False
        x = (left + right) // 2
        y = (top + bottom) // 2
        result = run_command([self._adb_path, "-s", self.serial, "shell", "input", "tap", str(x), str(y)], timeout=10)
        return result.returncode == 0

    def _set_text(self, text: str) -> bool:
        escaped = text.replace(" ", "%s")
        result = run_command([self._adb_path, "-s", self.serial, "shell", "input", "text", escaped], timeout=10)
        return result.returncode == 0

    def find_element(self, selector: Dict, timeout: int = 0):
        """Find an element using all configured selector fallbacks."""
        deadline = time.time() + timeout

        while True:
            for strategy, value in self.iter_selector_values(selector):
                try:
                    if self._use_fallback:
                        element = self.device(**{strategy: value})
                        if element.exists:
                            return element
                    else:
                        element = self.device(**{strategy: value})
                        if element.exists:
                            return element
                except Exception as e:
                    self.logger.debug(f"Selector {strategy}={value} failed: {e}")

            if timeout <= 0 or time.time() >= deadline:
                return None

            time.sleep(0.5)

    def iter_selector_values(self, selector: Dict):
        # className is tried before text/textContains so an input field is
        # matched by its widget type rather than accidentally matching a label
        # or error message (e.g. textContains "password" hitting the red
        # "Enter a password" TextView instead of the EditText).
        selector_map = [
            ("resourceId", "resourceId"),
            ("resourceIds", "resourceId"),
            ("className", "className"),
            ("classNames", "className"),
            ("text", "text"),
            ("texts", "text"),
            ("textContains", "textContains"),
            ("textContainsList", "textContains"),
            ("description", "description"),
            ("descriptions", "description"),
            ("descriptionContains", "descriptionContains"),
            ("descriptionContainsList", "descriptionContains"),
        ]

        for config_key, strategy in selector_map:
            values = selector.get(config_key)
            if not values:
                continue

            if not isinstance(values, list):
                values = [values]

            for value in values:
                if value:
                    yield strategy, value

    def click_element(self, selector: Dict) -> bool:
        try:
            element = self.find_element(selector, timeout=2)
            if not element:
                return False

            element.click()
            return True
        except Exception as e:
            self.logger.error(f"Click element error: {e}")
            return False

    def click_first_text(self, texts, timeout: int = 3) -> bool:
        for text in texts:
            try:
                element = self.device(text=text)
                if element.wait(timeout=timeout):
                    element.click()
                    self.logger.info(f"Clicked '{text}' button")
                    return True

                contains_element = self.device(textContains=text)
                if contains_element.wait(timeout=1):
                    contains_element.click()
                    self.logger.info(f"Clicked button containing '{text}'")
                    return True

                desc_element = self.device(description=text)
                if desc_element.wait(timeout=timeout):
                    desc_element.click()
                    self.logger.info(f"Clicked description '{text}' button")
                    return True

                desc_contains_element = self.device(descriptionContains=text)
                if desc_contains_element.wait(timeout=1):
                    desc_contains_element.click()
                    self.logger.info(f"Clicked description containing '{text}'")
                    return True
            except Exception as e:
                self.logger.debug(f"Button '{text}' not clickable: {e}")

        return False

    def click_next_if_present(self, login_config: Dict, timeout: int = 2) -> bool:
        """Try to advance through Google transitional screens by clicking common 'Next' buttons."""
        next_button = login_config.get("next_button", {})
        if next_button and self.click_element(next_button):
            time.sleep(1)
            return True

        next_candidates = self._collect_texts(
            login_config,
            "next_button_texts",
            ["NEXT", "Next", "다음", "다음 단계", "다음으로", "Continue", "계속"],
        )
        if not next_candidates:
            return False

        for _ in range(3):
            if self.click_first_text(next_candidates, timeout=timeout):
                time.sleep(1)
                return True
            time.sleep(0.5)
        return False

    def click_final_confirmation(self, login_config: Dict, timeout: int = 2) -> bool:
        final_button = login_config.get("final_button", {})
        if final_button and self.click_element(final_button):
            time.sleep(2)
            return True

        final_candidates = self._collect_texts(
            login_config,
            "final_button_texts",
            ["Finish", "Done", "Complete", "Confirm", "OK", "확인", "계속", "완료"],
        )
        if not final_candidates:
            return False

        for _ in range(3):
            if self.click_first_text(final_candidates, timeout=timeout):
                time.sleep(2)
                return True
            time.sleep(0.5)
        return False

    def ensure_editable(self, field):
        """Make sure we type into an EditText, not a matched label/error view."""
        try:
            class_name = field.info.get("className", "")
        except Exception:
            return field

        if "EditText" in class_name:
            return field

        edit = self.device(className="android.widget.EditText")
        if edit.exists:
            self.logger.debug(f"Selector matched non-editable '{class_name}'; using on-screen EditText")
            return edit
        return field

    def input_text(self, selector: Dict, text: str) -> bool:
        try:
            field = self.find_element(selector, timeout=3)
            if not field:
                return False

            field = self.ensure_editable(field)
            field.click()
            time.sleep(0.3)
            field.clear_text()
            if selector.get("input_method") == "fastinput":
                self.device.set_fastinput_ime(True)
                self.device.send_keys(text, clear=False)
                return True

            if selector.get("input_method") == "adb":
                return self.adb_input_text(text)

            try:
                field.set_text(text)
                return True
            except Exception as e:
                self.logger.debug(f"Field.set_text failed, falling back to fastinput/adb: {e}")

            try:
                self.device.set_fastinput_ime(True)
                self.device.send_keys(text, clear=False)
                return True
            except Exception as e:
                self.logger.debug(f"Fast input fallback failed: {e}")

            return self.adb_input_text(text)
        except Exception as e:
            self.logger.error(f"Input text error: {e}")
            return False

    def adb_input_text(self, text: str) -> bool:
        adb_path = self.config.get("adb_path", "adb")
        escaped_text = text.replace(" ", "%s")
        try:
            result = run_command([adb_path, "-s", self.serial, "shell", "input", "text", escaped_text], timeout=10)
            if result.returncode != 0:
                stderr = getattr(result, "stderr", "") or ""
                self.logger.debug(f"ADB input text failed: {stderr.strip()}")
                return False

            return True
        except Exception as e:
            self.logger.debug(f"ADB input text error: {e}")
            return False

    def press_submit_key(self) -> bool:
        try:
            self.device.press("enter")
            time.sleep(0.5)
            return True
        except Exception as e:
            self.logger.debug(f"Press enter failed: {e}")

        try:
            self.device.press(66)
            time.sleep(0.5)
            return True
        except Exception as e:
            self.logger.debug(f"Press keycode 66 failed: {e}")
            return False

    def account_exists_on_device(self, email: str) -> bool:
        adb_path = self.config.get("adb_path", "adb")
        try:
            result = run_command([adb_path, "-s", self.serial, "shell", "dumpsys", "account"], timeout=10)
            stdout = getattr(result, "stdout", "") or ""
            return email.lower() in stdout.lower()
        except Exception as e:
            self.logger.debug(f"AccountManager check failed: {e}")
            return False

    def has_text(self, text: str) -> bool:
        try:
            return self.device(text=text).exists or self.device(textContains=text).exists
        except Exception:
            return False

    def first_visible_text(self, texts) -> Optional[str]:
        for text in texts:
            if self.has_text(text):
                return text
        return None

    def open_login_entry(self, login_config: Dict):
        sign_in_texts = self._collect_texts(login_config, "sign_in_texts", ["Sign in", "Log in", "SIGN IN"])
        login_entry_texts = self._collect_texts(login_config, "login_entry_texts", ["Sign in", "Log in", "LOGIN", "로그인", "Login"])
        self.logger.info(f"Looking for login entry button: {', '.join(login_entry_texts)}")
        if self.click_first_text(login_entry_texts):
            time.sleep(2)
            return

        # Some Play Store screens use an alternate login button label.
        self.logger.info("Trying fallback Play Store login button")
        self.click_first_text(sign_in_texts + ["로그인", "Login"], timeout=2)
        time.sleep(2)

    def enter_email_if_needed(self, email: str, login_config: Dict, timeout: int) -> bool:
        username_field = login_config.get("username_field", {})
        password_field = login_config.get("password_field", {})

        if self.device(text=email).exists and self.wait_for_element(password_field, 2):
            self.logger.info("Password screen already open, skipping email step")
            return True

        if not self.wait_for_element(username_field, 3):
            self.open_login_entry(login_config)

        for _ in range(2):
            if self.wait_for_element(username_field, 3):
                break
            self.logger.debug("Username field not found yet; retrying once")
            time.sleep(1)

        if not self.wait_for_element(username_field, timeout):
            add_account_texts = login_config.get("add_account_texts", ["Add account", "계정 추가", "추가"])
            self.logger.info(f"Looking for add account button: {', '.join(add_account_texts)}")
            if self.click_first_text(add_account_texts):
                time.sleep(2)

        if not self.wait_for_element(username_field, timeout):
            fallback = self.find_element({"resourceIds": ["com.google.android.gms:id/email", "identifierId", "com.google.android.gms:id/account_name"]}, timeout=3)
            if fallback:
                self.logger.info("Used fallback email field selector")
            else:
                return False

        self.take_screenshot(email, "before_email")
        if not self.input_text(username_field, email):
            return False

        time.sleep(1)
        self.take_screenshot(email, "after_email")

        next_button = login_config.get("next_button", {})
        if next_button:
            self.click_element(next_button)
            time.sleep(2)

        self.click_next_if_present(login_config, timeout=1)
        return True

    def _get_screen_size(self) -> tuple[int, int]:
        """Get actual screen resolution via ADB / uiautomator2, adapting to current orientation."""
        if not self._use_fallback and self.device:
            try:
                info = self.device.info
                if info and "displayWidth" in info and "displayHeight" in info:
                    return int(info["displayWidth"]), int(info["displayHeight"])
            except Exception:
                pass
            try:
                w, h = self.device.window_size()
                if w and h:
                    return int(w), int(h)
            except Exception:
                pass

        try:
            w, h = 1080, 1920
            result = run_command([self._adb_path, "-s", self.serial, "shell", "wm", "size"], timeout=10)
            for line in (result.stdout or "").splitlines():
                if "Physical size" in line or "Override size" in line:
                    parts = line.split(":")[-1].strip().split("x")
                    if len(parts) == 2:
                        w, h = int(parts[0]), int(parts[1])
            
            # Check landscape via dumpsys window displays
            disp_result = run_command([self._adb_path, "-s", self.serial, "shell", "dumpsys", "window", "displays"], timeout=10)
            stdout = disp_result.stdout or ""
            is_landscape = False
            for line in stdout.splitlines():
                if "cur=" in line:
                    parts = line.split("cur=")
                    if len(parts) > 1:
                        cur_part = parts[1].split()[0]
                        cur_wh = cur_part.split("x")
                        if len(cur_wh) == 2:
                            cw, ch = int(cur_wh[0]), int(cur_wh[1])
                            if cw > ch:
                                is_landscape = True
                            return cw, ch

            # Fallback to SurfaceOrientation check
            rot_result = run_command([self._adb_path, "-s", self.serial, "shell", "dumpsys", "input"], timeout=10)
            for line in (rot_result.stdout or "").splitlines():
                if "SurfaceOrientation" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        try:
                            rot = int(parts[1].strip())
                            if rot in (1, 3):
                                is_landscape = True
                        except ValueError:
                            pass
            
            if is_landscape and w > h:
                pass
            elif is_landscape and h > w:
                w, h = h, w
            return w, h
        except Exception as e:
            self.logger.debug(f"Failed to get screen size: {e}")
        return 1080, 1920

    def _swipe_adb(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500):
        """Swipe using absolute pixel coordinates via ADB."""
        self._adb_swipe(x1, y1, x2, y2, duration_ms)

    def _swipe_right(self):
        """Swipe left→right to reveal elements on the right."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.1), int(h * 0.5), int(w * 0.9), int(h * 0.5))
            self.logger.info(f"Swiped right on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe right failed: {e}")

    def _swipe_left(self):
        """Swipe right→left to reveal elements on the left."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.9), int(h * 0.5), int(w * 0.1), int(h * 0.5))
            self.logger.info(f"Swiped left on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe left failed: {e}")

    def _swipe_up(self):
        """Swipe bottom→top to scroll down (reveal content below)."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.5), int(h * 0.8), int(w * 0.5), int(h * 0.2))
            self.logger.info(f"Swiped up on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe up failed: {e}")

    def _swipe_down(self):
        """Swipe top→bottom to scroll up (reveal content above)."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.5), int(h * 0.2), int(w * 0.5), int(h * 0.8))
            self.logger.info(f"Swiped down on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe down failed: {e}")

    def _swipe_all_directions(self, find_fn=None, times=2):
        """Try swiping in all 4 directions multiple times. If find_fn is provided, call it
        after each swipe to check if the target element appeared."""
        directions = [
            ("up", self._swipe_up),
            ("down", self._swipe_down),
            ("right", self._swipe_right),
            ("left", self._swipe_left),
        ]
        for name, swipe_fn in directions:
            for _ in range(times):
                swipe_fn()
                time.sleep(0.5)
                if find_fn and find_fn():
                    return True
        return False

    def enter_password(self, email: str, password: str, login_config: Dict, timeout: int) -> bool:
        password_field = login_config.get("password_field", {})
        if not self.wait_for_element(password_field, timeout):
            fallback_password = self.find_element(
                {
                    "resourceIds": [
                        "Passwd",
                        "com.google.android.gms:id/password",
                        "com.google.android.gms:id/password_text",
                    ],
                    "classNames": ["android.widget.EditText"],
                    "textContainsList": ["비밀번호", "비밀번호 입력", "password"],
                },
                timeout=5,
            )
            if fallback_password:
                self.logger.info("Used fallback password field selector")
                password_field = {
                    "classNames": ["android.widget.EditText"],
                    "textContainsList": ["비밀번호", "비밀번호 입력", "password"],
                }
            else:
                return False

        self.take_screenshot(email, "before_password")
        if not self.input_text(password_field, password):
            return False

        time.sleep(1)
        self.take_screenshot(email, "after_password")

        # --- Login Button Logic with Swipe Fallback ---
        login_button = login_config.get("login_button", {})
        clicked = False

        # 1. Try clicking the configured login button first
        if login_button and self.click_element(login_button):
            clicked = True
        else:
            # 2. Try clicking by text fallback
            login_button_texts = self._collect_texts(
                login_config,
                "login_button_texts",
                ["NEXT", "Next", "다음", "확인", "로그인", "로그인하기", "다음으로"],
            )
            if self.click_first_text(login_button_texts, timeout=2):
                clicked = True

        # 3. Button might be off-screen — try swiping in all directions
        if not clicked:
            self.logger.info("Login button not found/clickable. Trying multi-direction swipe...")
            def try_click_login():
                if login_button and self.click_element(login_button):
                    return True
                return self.click_first_text(login_button_texts, timeout=2)
            self._swipe_all_directions(find_fn=try_click_login)
            clicked = try_click_login()

        if clicked:
            time.sleep(3)
        else:
            self.logger.error("Could not find or click login button even after swiping")

        self.click_next_if_present(login_config, timeout=1)

        if self.wait_for_element(password_field, 2):
            self.logger.info("Password screen still present after button click, trying Enter fallback")
            self.press_submit_key()

        return True

    def finish_and_verify(self, email: str, login_config: Dict) -> Dict:
        action_texts = self._collect_texts(
            login_config,
            "post_login_action_texts",
            [
                "I agree", "Agree", "ACCEPT", "Accept", "동의",
                "I UNDERSTAND", "I understand", "Understand",
                "MORE", "More", "NEXT", "Next", "Not now", "Skip",
            ],
        )
        failure_texts = self._collect_texts(
            login_config,
            "failure_texts",
            [
                "Enter a password",
                "Wrong password",
                "Couldn't sign you in",
                "Couldn't find your Google Account",
                "This browser or app may not be secure",
                "Verify it's you",
                "Verify your identity",
                "CAPTCHA",
                "used to distinguish humans from robots",
                "Listen and type",
            ],
        )

        last_failure = None
        for _ in range(45):
            failure = self.first_visible_text(failure_texts)
            if failure:
                last_failure = failure
                break

            # Advance through any consent screen first (e.g. "I agree", then a
            # following "Accept"), so the account finishes setting up.
            clicked = self.click_first_text(action_texts, timeout=1)

            if self.account_exists_on_device(email):
                # The account is registered, but a second consent button
                # ("Accept" after "I agree") can still be on screen - clear it.
                for _ in range(4):
                    if not self.click_first_text(action_texts, timeout=1):
                        break
                    time.sleep(1.5)

                accept_button = login_config.get("accept_button", {})
                if accept_button and self.click_element(accept_button):
                    self.logger.info("Clicked final accept button")
                    time.sleep(2)
                    self.click_final_confirmation(login_config, timeout=2)

                self.logger.info(f"Google login SUCCESS for {email} on {self.serial}")
                screenshot = self.finalize_screenshots(email, login_config)
                return {"success": True, "message": "Login successful", "screenshot": screenshot}

            if clicked:
                time.sleep(2)
                continue

            # Consent buttons might be off-screen — try swiping to find them
            def try_click_action():
                return self.click_first_text(action_texts, timeout=1)
            if not clicked:
                self._swipe_all_directions(find_fn=try_click_action)

            self.click_next_if_present(login_config, timeout=1)

            time.sleep(1)

        screenshot = self.take_screenshot(email, "login_incomplete")
        if last_failure:
            return {"success": False, "message": f"Login stopped on Google message: {last_failure}", "screenshot": screenshot}

        return {"success": False, "message": "Login not confirmed by Android AccountManager", "screenshot": screenshot}

    def _reset_device(self) -> None:
        """Force-stop apps and return to Home screen for a clean start."""
        self.logger.info("[RESET] Resetting device to clean state before login")
        try:
            self.device.press("home")
            time.sleep(0.5)
        except Exception:
            pass
        for pkg in ("com.android.vending", "com.google.android.gms", "com.android.chrome"):
            try:
                run_command([self._adb_path, "-s", self.serial, "shell", "am", "force-stop", pkg], timeout=10)
            except Exception:
                pass
        try:
            run_command([self._adb_path, "-s", self.serial, "shell", "am", "kill-all"], timeout=10)
        except Exception:
            pass
        time.sleep(1)

    def login_google_account(self, email: str, password: str) -> Dict:
        if not self.connect():
            return {"success": False, "message": "Failed to connect device", "screenshot": ""}

        self.logger.info(f"Starting Google login for {email} on {self.serial}")

        # Reset device to clean state before starting
        self._reset_device()

        # Re-enforce portrait orientation lock after connection has settled
        login_config = self._get_login_config()
        if login_config.get("lock_portrait", True):
            self._lock_portrait_orientation()

        try:
            self.device.app_start("com.android.vending")
            time.sleep(3)
            self.device.app_start("com.google.android.gms", ".auth.DefaultAuthDelegateService")
            time.sleep(2)

            login_config = self._get_login_config()
            timeout = login_config.get("wait_timeout", 30)
            blocking_texts = self._collect_texts(
                login_config,
                "blocking_texts",
                ["CAPTCHA", "used to distinguish humans from robots", "Listen and type"],
            )
            blocking = self.first_visible_text(blocking_texts)
            if blocking:
                self.logger.info(f"Detected blocking screen: {blocking}; trying to scroll past it...")
                self._swipe_all_directions(find_fn=lambda: not self.first_visible_text(blocking_texts))
                blocking = self.first_visible_text(blocking_texts)
                if blocking:
                    self.logger.warning(f"CAPTCHA still present after scrolling: {blocking}")

            if not self.enter_email_if_needed(email, login_config, timeout):
                raise Exception("Username field not found")

            if not self.enter_password(email, password, login_config, timeout):
                raise Exception("Password field not found or password input failed")

            if not self._click_post_password_agree_button(email, login_config):
                screenshot = self.take_screenshot(email, "no_consent")
                return {"success": False, "message": "Consent button not found after password", "screenshot": screenshot}

            screenshot = self.take_screenshot(email, "login_success")
            return {"success": True, "message": "Login successful", "screenshot": screenshot}
        except Exception as e:
            screenshot = self.take_screenshot(email, "error")
            self.logger.error(f"Google login FAILED for {email}: {e}")
            return {"success": False, "message": str(e), "screenshot": screenshot}

    def _click_post_password_agree_button(self, email: str, login_config: Dict) -> bool:
        action_texts = ["동의"]

        if self.click_first_text(action_texts, timeout=3):
            self.logger.info("Clicked post-password consent button 동의")
            time.sleep(2)
            self.click_next_if_present(login_config, timeout=1)
            return True

        self.logger.warning("Could not click post-password consent button 동의")
        return False

    def verify_account_exists(self, email: str) -> bool:
        return self.account_exists_on_device(email)
