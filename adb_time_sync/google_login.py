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
        mapping = {"power": 26, "enter": 66, "home": 3, "back": 4}
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
    def __init__(self, serial: str, config: Dict, logger, stop_event=None):
        self.serial = serial
        self.config = config
        self.logger = logger
        self.device = None
        self.screenshot_dir = Path(config.get("screenshot_dir", "./screenshots"))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._adb_path = config.get("adb_path", "adb")
        self._use_fallback = False
        self._stop_event = stop_event

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
            "login_entry_texts",
            "add_account_texts",
            "post_login_action_texts",
            "next_button_texts",
            "final_button_texts",
            "login_button_texts",
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

    def _click_configured_texts(self, login_config: Dict, key: str, fallback: Optional[list] = None, timeout: int = 2) -> bool:
        candidates = self._collect_texts(login_config, key, fallback)
        if not candidates:
            return False
        self.logger.info(f"Trying configured text candidates for {key}: {candidates}")
        return self.click_first_text(candidates, timeout=timeout)

    def _prioritize_text_candidates(self, texts) -> list:
        korean = []
        latin = []
        for text in texts or []:
            if not text:
                continue
            if any("\uac00" <= ch <= "\ud7a3" for ch in str(text)):
                korean.append(text)
            else:
                latin.append(text)
        return korean + latin

    def _reenter_login_flow(self, login_config: Dict, reason: str = "") -> None:
        self.logger.info(f"Re-entering Google login flow{f': {reason}' if reason else ''}")
        try:
            self.device.press("back")
            time.sleep(0.5)
            self.device.press("back")
            time.sleep(0.5)
        except Exception as e:
            self.logger.debug(f"Back press during re-enter failed: {e}")

        try:
            self.device.press("home")
            time.sleep(0.5)
        except Exception as e:
            self.logger.debug(f"Home press during re-enter failed: {e}")

        self._clean_launch_chplay()
        time.sleep(2)
        self.open_login_entry(login_config)

    def _force_portrait(self) -> None:
        """Force device to portrait mode using all available methods."""
        try:
            # Disable auto-rotation
            run_command([self._adb_path, "-s", self.serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"], timeout=5)
            # Set user rotation to 0 (portrait)
            run_command([self._adb_path, "-s", self.serial, "shell", "settings", "put", "system", "user_rotation", "0"], timeout=5)
            # Window manager lock
            run_command([self._adb_path, "-s", self.serial, "shell", "wm", "set-user-rotation", "lock", "0"], timeout=5)
            run_command([self._adb_path, "-s", self.serial, "shell", "wm", "set-fix-to-user-rotation", "enabled"], timeout=5)
            # uiautomator2 method if available
            if self.device and not self._use_fallback:
                try:
                    self.device.set_orientation("natural")
                    self.device.freeze_rotation()
                except Exception:
                    pass
        except Exception:
            pass

    def _clean_launch_chplay(self) -> None:
        """Clean-launch Play Store / Google auth flow like the YouTube flow."""
        self.logger.info("[PLAY] Clean launch — home → force-stop Play Store/GMS → relaunch")
        try:
            run_command([self._adb_path, "-s", self.serial, "shell", "input", "keyevent", "3"], timeout=10)
            time.sleep(0.3)
        except Exception:
            pass

        for pkg in ("com.android.vending", "com.google.android.gms"):
            try:
                run_command([self._adb_path, "-s", self.serial, "shell", "am", "force-stop", pkg], timeout=10)
            except Exception:
                pass
        try:
            run_command([self._adb_path, "-s", self.serial, "shell", "am", "kill-all"], timeout=10)
        except Exception:
            pass
        time.sleep(1)

        # Step 1: Launch Play Store and wait for it to fully load
        try:
            self.device.app_start("com.android.vending")
            self.logger.info("[PLAY] Play Store launched, waiting for load...")
            time.sleep(5)
        except Exception as e:
            self.logger.debug(f"Play Store launch failed: {e}")

        # Step 2: Try to launch GMS auth (may help trigger login screen)
        try:
            run_command(
                [self._adb_path, "-s", self.serial, "shell", "am", "start-activity",
                 "-n", "com.google.android.gms/.auth.DefaultAuthDelegateService"],
                timeout=15,
            )
            self.logger.info("[PLAY] GMS auth service launched")
            time.sleep(3)
        except Exception as e:
            self.logger.debug(f"GMS auth launch failed: {e}")

        # Step 3: Fallback — if Play Store didn't load, try ADB intent
        try:
            check = run_command(
                [self._adb_path, "-s", self.serial, "shell", "dumpsys", "activity", "activities"],
                timeout=10,
            )
            stdout = check.stdout or ""
            if "com.android.vending" not in stdout:
                self.logger.info("[PLAY] Play Store not in foreground, retrying via ADB intent")
                run_command(
                    [self._adb_path, "-s", self.serial, "shell", "am", "start",
                     "-a", "android.intent.action.VIEW",
                     "-d", "market://details?id=com.android.vending"],
                    timeout=15,
                )
                time.sleep(4)
        except Exception:
            pass

        # Step 4: Re-apply portrait lock (GMS may override rotation)
        self._force_portrait()
        time.sleep(1)

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

        # Locale change may trigger rotation — re-lock portrait
        try:
            run_command([self._adb_path, "-s", self.serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"], timeout=5)
            run_command([self._adb_path, "-s", self.serial, "shell", "settings", "put", "system", "user_rotation", "0"], timeout=5)
            run_command([self._adb_path, "-s", self.serial, "shell", "wm", "set-user-rotation", "lock", "0"], timeout=5)
        except Exception:
            pass

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
        dump_path = None
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
        finally:
            if dump_path:
                try:
                    import os
                    os.unlink(dump_path)
                except Exception:
                    pass

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
                try:
                    import os
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return None
            pull = run_command([self._adb_path, "-s", self.serial, "pull", "/sdcard/window_dump.xml", tmp_path], timeout=20)
            if pull.returncode != 0:
                try:
                    import os
                    os.unlink(tmp_path)
                except Exception:
                    pass
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
        for text in self._prioritize_text_candidates(texts):
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
        # Extended list: Korean + English variants for Play Store login button
        login_entry_texts = self._collect_texts(
            login_config, "login_entry_texts",
            [
                "Sign in", "Log in", "LOGIN", "Login", "SIGN IN",
                "로그인", "Google에 로그인", "계정 추가", "추가",
                "Sign in to Google", "Add account",
            ],
        )
        self.logger.info(f"Looking for login entry button: {', '.join(login_entry_texts)}")

        for attempt in range(6):
            # Dump UI to debug what's on screen
            if self._use_fallback:
                dump_path = self._dump_ui_xml()
                if dump_path:
                    try:
                        import xml.etree.ElementTree as ET
                        root = ET.parse(dump_path).getroot()
                        all_texts = [n.attrib.get("text", "") for n in root.iter() if n.attrib.get("text")]
                        self.logger.info(f"[UI DEBUG] Visible texts: {all_texts[:15]}")
                    except Exception:
                        pass
                    finally:
                        try:
                            import os
                            os.unlink(dump_path)
                        except Exception:
                            pass

            if self.click_first_text(login_entry_texts, timeout=3):
                time.sleep(2)
                return

            if self._click_configured_texts(login_config, "sign_in_texts", sign_in_texts + ["로그인", "Login", "Sign in"], timeout=3):
                time.sleep(2)
                return

            # Try swiping to reveal login button
            if attempt >= 2:
                self.logger.info(f"Trying swipe to find login button on attempt {attempt + 1}")
                self._swipe_up()
                time.sleep(1)

            if attempt < 5:
                self.logger.info(f"Login entry not found on attempt {attempt + 1}; retrying after a short pause")
                time.sleep(2)

        self.logger.warning("Login entry not found after retries; will re-enter the flow on the next pass")

    def enter_email_if_needed(self, email: str, login_config: Dict, timeout: int) -> bool:
        username_field = login_config.get("username_field", {})
        password_field = login_config.get("password_field", {})
        retry_limit = max(2, int(login_config.get("retry_count", 3)))

        if self.device(text=email).exists and self.wait_for_element(password_field, 2):
            self.logger.info("Password screen already open, skipping email step")
            return True

        for attempt in range(retry_limit):
            if self.wait_for_element(username_field, 3):
                break

            self.logger.info(f"Username field not visible on attempt {attempt + 1}/{retry_limit}; trying to re-open login")
            self.open_login_entry(login_config)
            if attempt < retry_limit - 1:
                time.sleep(1.5)

        if not self.wait_for_element(username_field, timeout):
            add_account_texts = login_config.get("add_account_texts", ["Add account", "계정 추가", "추가"])
            self.logger.info(f"Looking for add account button: {', '.join(add_account_texts)}")
            if self.click_first_text(add_account_texts, timeout=2):
                time.sleep(2)

        if not self.wait_for_element(username_field, timeout):
            fallback = self.find_element({"resourceIds": ["com.google.android.gms:id/email", "identifierId", "com.google.android.gms:id/account_name"]}, timeout=3)
            if fallback:
                self.logger.info("Used fallback email field selector")
            else:
                self._reenter_login_flow(login_config, "email field missing")
                return False

        self.take_screenshot(email, "before_email")
        if not self.input_text(username_field, email):
            self._reenter_login_flow(login_config, "email input failed")
            return False

        time.sleep(1)
        self.take_screenshot(email, "after_email")

        next_button = login_config.get("next_button", {})
        if next_button:
            self.click_element(next_button)
            time.sleep(2)

        self._click_configured_texts(login_config, "next_button_texts", ["NEXT", "Next", "다음", "다음 단계", "다음으로", "Continue", "계속"], timeout=1)
        self.click_next_if_present(login_config, timeout=1)
        return True

    def _get_screen_size(self) -> tuple[int, int]:
        """Get actual screen resolution via ADB / uiautomator2, adapting to current orientation."""
        # Get physical size first
        w, h = 1080, 1920
        try:
            result = run_command([self._adb_path, "-s", self.serial, "shell", "wm", "size"], timeout=10)
            for line in (result.stdout or "").splitlines():
                if "Physical size" in line or "Override size" in line:
                    parts = line.split(":")[-1].strip().split("x")
                    if len(parts) == 2:
                        w, h = int(parts[0]), int(parts[1])
        except Exception:
            pass

        # Check if rotated via SurfaceOrientation
        is_landscape = False
        try:
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
        except Exception:
            pass

        # If landscape, swap w and h for correct swipe coordinates
        if is_landscape and h > w:
            w, h = h, w

        return w, h

    def _swipe_adb(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 200):
        """Swipe using absolute pixel coordinates via ADB."""
        self._adb_swipe(x1, y1, x2, y2, duration_ms)

    def _swipe_right(self):
        """Swipe left→right to reveal elements on the right."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.05), int(h * 0.5), int(w * 0.95), int(h * 0.5), 180)
            self.logger.info(f"Swiped right on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe right failed: {e}")

    def _swipe_left(self):
        """Swipe right→left to reveal elements on the left."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.95), int(h * 0.5), int(w * 0.05), int(h * 0.5), 180)
            self.logger.info(f"Swiped left on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe left failed: {e}")

    def _swipe_up(self):
        """Swipe bottom→top to scroll down (reveal content below)."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.5), int(h * 0.85), int(w * 0.5), int(h * 0.15), 180)
            self.logger.info(f"Swiped up on {w}x{h}")
        except Exception as e:
            self.logger.debug(f"Swipe up failed: {e}")

    def _swipe_down(self):
        """Swipe top→bottom to scroll up (reveal content above)."""
        w, h = self._get_screen_size()
        try:
            self._swipe_adb(int(w * 0.5), int(h * 0.15), int(w * 0.5), int(h * 0.85), 180)
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
                time.sleep(0.3)
                if find_fn and find_fn():
                    return True
        return False

    def enter_password(self, email: str, password: str, login_config: Dict, timeout: int) -> bool:
        password_field = login_config.get("password_field", {})
        retry_limit = max(2, int(login_config.get("retry_count", 3)))
        login_button_texts = self._collect_texts(
            login_config,
            "login_button_texts",
            ["NEXT", "Next", "다음", "확인", "로그인", "로그인하기", "다음으로"],
        )

        for attempt in range(retry_limit):
            if self.wait_for_element(password_field, 3):
                break

            self.logger.info(f"Password field not visible on attempt {attempt + 1}/{retry_limit}; trying to re-open login")
            self._reenter_login_flow(login_config, "password field missing")
            time.sleep(2)

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
                self._reenter_login_flow(login_config, "password field missing after fallback")
                return False

        self.take_screenshot(email, "before_password")
        if not self.input_text(password_field, password):
            self._reenter_login_flow(login_config, "password input failed")
            return False

        time.sleep(1)
        self.take_screenshot(email, "after_password")

        login_button = login_config.get("login_button", {})
        clicked = False

        # Try clicking login button directly — no swipe needed after password
        if login_button and self.click_element(login_button):
            clicked = True
        elif self.click_first_text(login_button_texts, timeout=2):
            clicked = True
        else:
            # Retry once after short pause
            time.sleep(1)
            if login_button and self.click_element(login_button):
                clicked = True
            elif self.click_first_text(login_button_texts, timeout=2):
                clicked = True

        if clicked:
            time.sleep(3)
        else:
            self.logger.error("Could not find or click login button")
            self._reenter_login_flow(login_config, "login button missing")
            return False

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

            # No swipe needed — just click next/confirm buttons
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

        login_config = self._get_login_config()
        if login_config.get("lock_portrait", True):
            self._lock_portrait_orientation()
            # Force portrait via ADB as extra guarantee
            try:
                run_command([self._adb_path, "-s", self.serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"], timeout=5)
                run_command([self._adb_path, "-s", self.serial, "shell", "settings", "put", "system", "user_rotation", "0"], timeout=5)
                run_command([self._adb_path, "-s", self.serial, "shell", "wm", "set-user-rotation", "lock", "0"], timeout=5)
                time.sleep(1)
            except Exception:
                pass

        retry_limit = max(2, int(login_config.get("retry_count", 3)))

        try:
            for attempt in range(1, retry_limit + 1):
                if self._stop_event and self._stop_event.is_set():
                    self.logger.info("Login cancelled by user")
                    return {"success": False, "message": "Cancelled by user", "screenshot": ""}

                self.logger.info(f"Google login attempt {attempt}/{retry_limit}")

                # Force portrait every attempt in case app override it
                self._force_portrait()
                time.sleep(0.5)

                self._clean_launch_chplay()

                timeout = login_config.get("wait_timeout", 30)
                blocking_texts = self._collect_texts(
                    login_config,
                    "blocking_texts",
                    ["CAPTCHA", "used to distinguish humans from robots", "Listen and type"],
                )
                blocking = self.first_visible_text(blocking_texts)
                if blocking:
                    self.logger.info(f"Detected blocking screen: {blocking}; trying to scroll past it...")
                    # Don't raise — try scrolling to dismiss or find other buttons
                    self._swipe_all_directions(find_fn=lambda: not self.first_visible_text(blocking_texts))
                    # Check again after scrolling
                    blocking = self.first_visible_text(blocking_texts)
                    if blocking:
                        self.logger.warning(f"CAPTCHA still present after scrolling: {blocking}")
                        # Continue anyway — enter_email_if_needed will try to find elements

                if not self.enter_email_if_needed(email, login_config, timeout):
                    if attempt < retry_limit:
                        self.logger.info("Email step failed; retrying login flow")
                        self._reenter_login_flow(login_config, "email step failed")
                        continue
                    raise Exception("Username field not found")

                if not self.enter_password(email, password, login_config, timeout):
                    if attempt < retry_limit:
                        self.logger.info("Password step failed; retrying login flow")
                        self._reenter_login_flow(login_config, "password step failed")
                        continue
                    raise Exception("Password field not found or password input failed")

                if not self._click_post_password_agree_button(email, login_config):
                    if attempt < retry_limit:
                        self.logger.info("Consent click failed; retrying login flow")
                        self._reenter_login_flow(login_config, "consent click failed")
                        continue
                    return {"success": False, "message": "Consent button not found after password", "screenshot": self.take_screenshot(email, "no_consent")}

                screenshot = self.take_screenshot(email, "login_success")
                return {"success": True, "message": "Login successful", "screenshot": screenshot}

            return {"success": False, "message": "Login retries exhausted", "screenshot": ""}
        except Exception as e:
            screenshot = self.take_screenshot(email, "error")
            self.logger.error(f"Google login FAILED for {email}: {e}")
            return {"success": False, "message": str(e), "screenshot": screenshot}

    def _click_post_password_agree_button(self, email: str, login_config: Dict) -> bool:
        action_texts = self._collect_texts(
            login_config,
            "post_login_action_texts",
            ["I agree", "Agree", "Accept", "동의", "확인", "동의합니다", "수락", "Next", "다음"],
        )

        if self.click_first_text(action_texts, timeout=3):
            self.logger.info("Clicked post-password consent button")
            time.sleep(2)
            self.click_next_if_present(login_config, timeout=1)
            return True

        self.logger.warning("Could not click post-password consent button")
        return False

    def verify_account_exists(self, email: str) -> bool:
        return self.account_exists_on_device(email)

    def get_all_google_accounts(self) -> list:
        """Get all Google account emails on the device."""
        adb_path = self.config.get("adb_path", "adb")
        accounts = []
        try:
            result = run_command([adb_path, "-s", self.serial, "shell", "dumpsys", "account"], timeout=10)
            stdout = getattr(result, "stdout", "") or ""
            # Parse AccountManager output to find Google accounts
            # Look for lines containing @gmail.com or @google.com
            for line in stdout.splitlines():
                line = line.strip()
                if "@gmail.com" in line.lower() or "@google.com" in line.lower():
                    # Extract email from the line
                    for word in line.split():
                        if "@" in word and ("gmail.com" in word.lower() or "google.com" in word.lower()):
                            email = word.strip().strip('"').strip("'")
                            if email not in accounts:
                                accounts.append(email)
        except Exception as e:
            self.logger.debug(f"Failed to get Google accounts: {e}")
        return accounts

    def logout_all_google_accounts(self) -> dict:
        """Remove ALL Google accounts from Samsung device (Korean language).

        Flow:
        1. Get list of all Google accounts
        2. For each account:
           a. Open Settings (설정)
           b. Tap Accounts and backup (계정 및 백업)
           c. Tap Accounts (계정)
           d. Find and tap the Google account
           e. Tap Remove account (계정 삭제)
           f. Confirm removal
        3. Return summary
        """
        self.logger.info(f"[LOGOUT] Starting ALL Google account removal on {self.serial}")

        if not self.connect():
            return {"success": False, "message": "Failed to connect device", "removed": [], "failed": []}

        # Get all Google accounts
        accounts = self.get_all_google_accounts()
        self.logger.info(f"[LOGOUT] Found {len(accounts)} Google accounts: {accounts}")

        if not accounts:
            self.logger.info("[LOGOUT] No Google accounts found on device")
            return {"success": True, "message": "No Google accounts found", "removed": [], "failed": []}

        removed = []
        failed = []

        for email in accounts:
            if self._stop_event and self._stop_event.is_set():
                self.logger.info("[LOGOUT] Cancelled by user")
                break

            self.logger.info(f"[LOGOUT] Removing account: {email}")
            result = self._remove_single_account(email)
            if result.get("success"):
                removed.append(email)
                self.logger.info(f"[LOGOUT] Successfully removed: {email}")
            else:
                failed.append({"email": email, "error": result.get("message", "Unknown error")})
                self.logger.warning(f"[LOGOUT] Failed to remove: {email} - {result.get('message')}")

            # Wait between accounts
            time.sleep(1)

        # Go home to clean up
        try:
            self.device.press("home")
        except Exception:
            pass

        total = len(accounts)
        success_count = len(removed)
        self.logger.info(f"[LOGOUT] Summary: {success_count}/{total} accounts removed")

        return {
            "success": success_count > 0,
            "message": f"Removed {success_count}/{total} accounts",
            "removed": removed,
            "failed": failed,
        }

    def _remove_single_account(self, email: str) -> dict:
        """Remove a single Google account from device."""
        try:
            # Step 1: Open Settings
            self.logger.info(f"[LOGOUT] Opening Settings for {email}")
            run_command([self._adb_path, "-s", self.serial, "shell", "am", "start", "-a", "android.settings.SETTINGS"], timeout=10)
            time.sleep(2)

            # Step 2: Find and tap "계정 및 백업" (Accounts and backup) or "계정" (Accounts)
            self.logger.info("[LOGOUT] Looking for Accounts menu")
            account_menu_texts = ["계정 및 백업", "계정", "Accounts and backup", "Accounts"]
            if not self._find_and_tap_text(account_menu_texts, timeout=5, scroll=True):
                # Fallback: try direct intent
                self.logger.info("[LOGOUT] Trying direct intent to Accounts settings")
                run_command([self._adb_path, "-s", self.serial, "shell", "am", "start", "-a", "android.settings.SYNC_SETTINGS"], timeout=10)
                time.sleep(2)

            # Step 3: Tap "계정" (Accounts) submenu if present
            self.logger.info("[LOGOUT] Looking for Accounts submenu")
            submenu_texts = ["계정", "Accounts"]
            self._find_and_tap_text(submenu_texts, timeout=3)
            time.sleep(1)

            # Step 4: Find and tap the Google account by email
            self.logger.info(f"[LOGOUT] Looking for account {email}")
            if not self._find_and_tap_text([email], timeout=5, scroll=True):
                # Try finding "Google" entry first, then the email
                self.logger.info("[LOGOUT] Trying to find Google provider first")
                if self._find_and_tap_text(["Google", "구글"], timeout=3):
                    time.sleep(1)
                    if not self._find_and_tap_text([email], timeout=5, scroll=True):
                        return {"success": False, "message": f"Could not find account {email} in Settings"}
                else:
                    return {"success": False, "message": f"Could not find account {email} in Settings"}

            time.sleep(1)

            # Step 5: Tap "계정 삭제" (Remove account)
            self.logger.info("[LOGOUT] Tapping Remove account")
            remove_texts = ["계정 삭제", "삭제", "Remove account", "Remove"]
            if not self._find_and_tap_text(remove_texts, timeout=5):
                return {"success": False, "message": "Could not find Remove account button"}

            time.sleep(1)

            # Step 6: Confirm removal
            self.logger.info("[LOGOUT] Confirming removal")
            confirm_texts = ["계정 삭제", "삭제", "확인", "Remove", "OK"]
            self._find_and_tap_text(confirm_texts, timeout=3)
            time.sleep(2)

            # Verify account is removed
            if not self.account_exists_on_device(email):
                return {"success": True, "message": f"Account {email} removed"}
            else:
                return {"success": False, "message": f"Account {email} still present after removal"}

        except Exception as e:
            self.logger.error(f"[LOGOUT] Failed to remove {email}: {e}")
            return {"success": False, "message": str(e)}

    def _find_and_tap_text(self, texts: list, timeout: int = 5, scroll: bool = False) -> bool:
        """Find text on screen and tap it. Optionally scroll to find it."""
        deadline = time.time() + timeout
        scroll_count = 0
        max_scrolls = 5

        while time.time() < deadline:
            # Try to find and tap any of the texts
            for text in texts:
                if self.click_first_text([text], timeout=0):
                    self.logger.info(f"[LOGOUT] Tapped: {text}")
                    time.sleep(1)
                    return True

            # Try finding by textContains
            for text in texts:
                element = self.find_element({"textContainsList": [text]}, timeout=0)
                if element and element.exists:
                    element.click()
                    self.logger.info(f"[LOGOUT] Tapped element containing: {text}")
                    time.sleep(1)
                    return True

            if not scroll or scroll_count >= max_scrolls:
                break

            # Scroll down to find more items
            self.logger.info(f"[LOGOUT] Scrolling to find text... (attempt {scroll_count + 1})")
            try:
                size = self._get_screen_size()
                if size:
                    w, h = size
                    self._adb_swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), 300)
                else:
                    self.device.swipe(540, 1500, 540, 600, 0.5)
            except Exception:
                try:
                    self.device.swipe(540, 1500, 540, 600, 0.5)
                except Exception:
                    pass
            time.sleep(1)
            scroll_count += 1

        return False

    def _get_screen_size(self):
        """Get device screen size."""
        try:
            result = run_command([self._adb_path, "-s", self.serial, "shell", "wm", "size"], timeout=5)
            # Output like: "Physical size: 1080x2400"
            for line in (result.stdout or "").splitlines():
                if "size" in line.lower():
                    size_str = line.split(":")[-1].strip()
                    if "x" in size_str:
                        w, h = size_str.split("x")
                        return int(w), int(h)
        except Exception:
            pass
        return None
