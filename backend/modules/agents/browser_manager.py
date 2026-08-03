# Path: backend/modules/agents/browser_manager.py
# Use: Global Selenium Browser Manager — one Chrome instance, multiple isolated tabs.
# Each sub-agent/task gets its own tab via a unique tab_id.

import time
import asyncio
import logging
import threading
import platform as os_platform
from typing import Dict, Optional, Tuple

import pyperclip
# codex-changes detail: make browser automation an optional runtime capability instead of breaking orchestrator imports.
try:
    import undetected_chromedriver as uc
except ImportError:
    uc = None
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from modules.ai_orchestrator.platform_config import PLATFORMS, PlatformInfo

logger = logging.getLogger("MAX.BROWSER_MANAGER")


class BrowserManager:
    """
    Singleton-style manager for one undetected_chromedriver instance.
    Multiple tabs are tracked by string IDs (e.g. 'reasoning_<task_id>').
    All public methods are thread-safe via an RLock.
    """

    def __init__(self):
        self._driver: Optional[uc.Chrome] = None
        self._tabs: Dict[str, str] = {}     # tab_id -> window_handle
        self._lock = threading.RLock()

    # ──────────────────────────────────────────────
    # Browser lifecycle
    # ──────────────────────────────────────────────
    def _ensure_browser(self):
        """Start Chrome if not already running (called internally, lock held by caller)."""
        # codex-changes detail: raise a targeted error only when browser automation is actually used.
        if uc is None:
            raise RuntimeError("undetected-chromedriver is not installed; browser automation is unavailable.")
        if self._driver:
            try:
                # Quick health check — will throw if browser crashed
                _ = self._driver.window_handles
                return
            except Exception:
                logger.warning("Browser session lost. Restarting...")
                self._driver = None
                self._tabs.clear()

        logger.info("🌐 Launching undetected Chrome for Orchestrator...")
        options = uc.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--start-maximized")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.page_load_strategy = "eager"

        # Detect Chrome version upfront to avoid crash-and-retry
        v = None
        if os_platform.system() == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
                version, _ = winreg.QueryValueEx(key, "version")
                v = int(version.split('.')[0])
                logger.info(f"Detected Chrome version: {v}")
            except Exception:
                pass

        try:
            if v:
                self._driver = uc.Chrome(options=options, version_main=v)
            else:
                self._driver = uc.Chrome(options=options)
        except Exception as e:
            err_str = str(e)
            if "Current browser version is" in err_str:
                import re, os, shutil
                match = re.search(r"Current browser version is (\d+)", err_str)
                if match:
                    fallback_v = int(match.group(1))
                    logger.warning(f"ChromeDriver version mismatch. Forcing version_main={fallback_v}")

                    # Clean up dangling processes and locked files from the failed attempt
                    if os_platform.system() == "Windows":
                        os.system("taskkill /f /im chromedriver.exe >nul 2>&1")
                        os.system("taskkill /f /im undetected_chromedriver.exe >nul 2>&1")

                    uc_dir = os.path.expandvars(r"%APPDATA%\undetected_chromedriver")
                    if os.path.exists(uc_dir):
                        try:
                            shutil.rmtree(uc_dir, ignore_errors=True)
                        except Exception:
                            pass

                    time.sleep(1)  # Give OS time to release file locks

                    # IMPORTANT: Cannot reuse ChromeOptions object — recreate it
                    options = uc.ChromeOptions()
                    options.add_argument("--disable-blink-features=AutomationControlled")
                    options.add_argument("--start-maximized")
                    options.add_argument("--no-sandbox")
                    options.add_argument("--disable-dev-shm-usage")
                    options.page_load_strategy = "eager"

                    self._driver = uc.Chrome(options=options, version_main=fallback_v)
                else:
                    raise
            else:
                raise

        self._driver.set_page_load_timeout(30)
        self._driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })

        # The initial tab counts as a spare; we'll open specific tabs via get_tab().
        self._tabs["__initial__"] = self._driver.current_window_handle
        logger.info("✅ Browser ready.")

    # ──────────────────────────────────────────────
    # Tab management
    # ──────────────────────────────────────────────
    async def get_tab(self, tab_id: str, url: Optional[str] = None) -> str:
        return await asyncio.to_thread(self._get_tab_sync, tab_id, url)

    def _get_tab_sync(self, tab_id: str, url: Optional[str] = None) -> str:
        """
        Return a window handle for `tab_id`.
        - If a tab with that ID already exists, switch to it and return the handle.
        - Otherwise, open a NEW tab, navigate to `url` (if given), and register it.
        """
        with self._lock:
            self._ensure_browser()

            # Already exists?
            if tab_id in self._tabs:
                handle = self._tabs[tab_id]
                try:
                    self._driver.switch_to.window(handle)
                    return handle
                except Exception:
                    # Handle went stale (user closed tab, etc.)
                    logger.warning(f"Tab '{tab_id}' handle stale. Re-creating...")
                    self._tabs.pop(tab_id, None)

            # Open new tab
            self._driver.execute_script("window.open('about:blank', '_blank');")
            time.sleep(0.5)
            new_handle = [h for h in self._driver.window_handles if h not in self._tabs.values()][-1]
            self._driver.switch_to.window(new_handle)
            self._tabs[tab_id] = new_handle

            if url:
                self._driver.get(url)
                # Wait for the page to actually be interactive — not just a blind sleep
                self._wait_for_page_ready()

            logger.info(f"🆕 Tab '{tab_id}' opened. Total tabs: {len(self._tabs)}")
            return new_handle

    def _wait_for_page_ready(self, timeout: int = 20):
        """
        Wait until the page is actually interactive.
        Tries to find any textarea / contenteditable element.
        Falls back to a generous sleep if DOM detection fails.
        """
        generic_selectors = [
            "rich-textarea div[contenteditable='true']",
            "div[contenteditable='true']",
            "textarea",
            "#prompt-textarea",
        ]
        for sel in generic_selectors:
            try:
                WebDriverWait(self._driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                )
                logger.info(f"✅ Page ready (found: {sel})")
                time.sleep(1)  # Small extra buffer after element appears
                return
            except Exception:
                continue

        # Fallback: if no selector matched, wait generously
        logger.warning("Page readiness check: no textarea found. Waiting 10s as fallback.")
        time.sleep(10)

    async def close_tab(self, tab_id: str):
        await asyncio.to_thread(self._close_tab_sync, tab_id)

    def _close_tab_sync(self, tab_id: str):
        """Close a specific tab and remove it from tracking."""
        with self._lock:
            handle = self._tabs.pop(tab_id, None)
            if not handle or not self._driver:
                return

            try:
                self._driver.switch_to.window(handle)
                self._driver.close()
            except Exception as e:
                logger.warning(f"Could not close tab '{tab_id}': {e}")

            # Switch back to any remaining tab
            remaining = self._driver.window_handles
            if remaining:
                self._driver.switch_to.window(remaining[0])
            else:
                # All tabs closed → quit browser
                self._quit_sync()

            logger.info(f"🗑️ Tab '{tab_id}' closed. Remaining: {len(self._tabs)}")

    async def quit(self):
        await asyncio.to_thread(self._quit_sync)

    def _quit_sync(self):
        """Fully shut down the browser."""
        with self._lock:
            if self._driver:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None
                self._tabs.clear()
                logger.info("🛑 Browser shut down.")

    # ──────────────────────────────────────────────
    # Selenium actions (scoped to a specific tab)
    # ──────────────────────────────────────────────
    async def inject_and_submit(self, tab_id: str, prompt: str, platform_info: PlatformInfo) -> int:
        """
        Type a prompt and submit it. Returns the response element count
        BEFORE submission (so smart_harvester knows when a NEW response appears).
        """
        return await asyncio.to_thread(self._inject_and_submit_sync, tab_id, prompt, platform_info)

    def _inject_and_submit_sync(self, tab_id: str, prompt: str, platform_info: PlatformInfo) -> int:
        """
        Type a prompt into the AI platform's textarea and submit it.
        Returns the number of response elements BEFORE clicking send.

        Flow:
        1. Switch to tab
        2. Find textarea (WebDriverWait, clickable)
        3. Clear textarea
        4. Paste prompt via clipboard
        5. VERIFY paste worked (retry up to 3 times)
        6. Count existing response elements
        7. Click submit button (WebDriverWait, clickable)
        8. Wait until response count increases (AI started responding)
        9. Return pre-submit response count
        """
        with self._lock:
            self._switch_to(tab_id)
            driver = self._driver

            # ── Step 1: Find the textarea ──
            textarea = None
            for sel in platform_info.textarea_selectors:
                try:
                    textarea = WebDriverWait(driver, 15).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                    break
                except Exception:
                    continue

            if not textarea:
                raise RuntimeError(f"Could not find textarea in tab '{tab_id}'.")

            # ── Step 2: Clear the textarea ──
            textarea.click()
            time.sleep(0.5)
            ctrl = Keys.COMMAND if os_platform.system() == "Darwin" else Keys.CONTROL
            textarea.send_keys(ctrl, "a")
            textarea.send_keys(Keys.BACKSPACE)
            time.sleep(0.3)

            # ── Step 3: Paste prompt via clipboard with verification ──
            original_clip = ""
            try:
                original_clip = pyperclip.paste()
            except Exception:
                pass

            paste_verified = False
            for attempt in range(3):
                pyperclip.copy(prompt)
                textarea.click()
                time.sleep(0.3)
                textarea.send_keys(ctrl, "v")
                time.sleep(1.0)

                # Verify text actually appeared
                try:
                    # For contenteditable divs, check innerText
                    current_text = textarea.text or ""
                    if not current_text:
                        current_text = driver.execute_script(
                            "return arguments[0].innerText || arguments[0].value || '';", textarea
                        )
                    if len(current_text.strip()) > 10:
                        paste_verified = True
                        logger.info(f"✅ Paste verified on attempt {attempt + 1} ({len(current_text)} chars)")
                        break
                    else:
                        logger.warning(f"Paste attempt {attempt + 1} failed (got {len(current_text)} chars). Retrying...")
                        textarea.send_keys(ctrl, "a")
                        textarea.send_keys(Keys.BACKSPACE)
                        time.sleep(0.5)
                except Exception as ve:
                    logger.warning(f"Paste verification error attempt {attempt + 1}: {ve}")

            # Restore clipboard
            try:
                pyperclip.copy(original_clip)
            except Exception:
                pass

            if not paste_verified:
                logger.warning("Paste could not be verified after 3 attempts. Proceeding anyway.")

            # ── Step 4: Count existing responses BEFORE submit ──
            pre_submit_count = self._count_responses(platform_info)
            logger.info(f"📊 Pre-submit response count: {pre_submit_count}")

            # ── Step 5: Click submit button ──
            submitted = False
            for sel in platform_info.submit_selectors:
                try:
                    btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                    driver.execute_script("arguments[0].click();", btn)
                    submitted = True
                    logger.info(f"✅ Submit clicked via: {sel}")
                    break
                except Exception:
                    continue

            if not submitted:
                # Fallback: Enter key
                logger.warning("No submit button found. Using Enter key as fallback.")
                textarea.send_keys(Keys.ENTER)

            time.sleep(2)  # Brief pause to let the AI platform register the submission

        # ── Step 6: Wait for AI to START responding (outside lock) ──
        # Poll until response count increases — means the AI has begun its reply
        start = time.time()
        while time.time() - start < 30:
            current_count = self._count_responses_unlocked(platform_info, tab_id)
            if current_count > pre_submit_count:
                logger.info(f"✅ New response detected (count: {pre_submit_count} → {current_count})")
                break
            time.sleep(1)
        else:
            logger.warning("30s timeout waiting for new response element. Proceeding with harvest anyway.")

        return pre_submit_count

    async def smart_harvester(self, tab_id: str, platform_info: PlatformInfo,
                              pre_submit_count: int = 0, timeout: int = 120) -> str:
        """
        Wait for the AI platform response to stabilize, then return the text.
        `pre_submit_count` is the number of response elements BEFORE the prompt was sent.
        """
        return await asyncio.to_thread(
            self._smart_harvester_sync, tab_id, platform_info, pre_submit_count, timeout
        )

    def _smart_harvester_sync(self, tab_id: str, platform_info: PlatformInfo,
                               pre_submit_count: int = 0, timeout: int = 120) -> str:
        """
        Wait for the AI response to stabilize, then return the text.

        Flow:
        1. Wait until a NEW response element exists (count > pre_submit_count)
        2. Poll the LAST response element's text until it stabilizes (4s no change)
        3. Return that text
        """
        # Ensure we're on the right tab
        with self._lock:
            self._switch_to(tab_id)

        start = time.time()

        # ── Phase 1: Wait for new response to appear ──
        response_appeared = False
        while time.time() - start < 30:
            count = self._count_responses_unlocked(platform_info, tab_id)
            if count > pre_submit_count:
                response_appeared = True
                break
            time.sleep(1)

        if not response_appeared:
            logger.warning(f"Tab '{tab_id}': 30s and no new response element. Trying refresh...")
            with self._lock:
                self._switch_to(tab_id)
                self._driver.refresh()
            time.sleep(8)

        # ── Phase 2: Wait for text to stabilize (stop growing) ──
        last_length = 0
        stable_count = 0
        final_text = ""

        while time.time() - start < timeout:
            text = self._get_latest_response_unlocked(platform_info, tab_id)
            current_length = len(text)

            if current_length > 0 and current_length == last_length:
                stable_count += 1
            else:
                stable_count = 0

            last_length = current_length
            final_text = text

            if stable_count >= 4:  # 4 seconds of no change = done
                logger.info(f"✅ Response stabilized ({current_length} chars, {stable_count}s stable)")
                break
            time.sleep(1)

        if not final_text:
            logger.warning(f"Tab '{tab_id}': Harvester returning empty response after {timeout}s")

        return final_text

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────
    def _switch_to(self, tab_id: str):
        """Switch to a tab (caller must hold lock)."""
        handle = self._tabs.get(tab_id)
        if not handle:
            raise RuntimeError(f"Tab '{tab_id}' does not exist. Call get_tab() first.")
        self._driver.switch_to.window(handle)

    def _count_responses(self, platform_info: PlatformInfo) -> int:
        """Count response elements on the current page. Caller must hold lock."""
        for sel in platform_info.response_selectors:
            try:
                elements = self._driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    return len(elements)
            except Exception:
                continue
        return 0

    def _count_responses_unlocked(self, platform_info: PlatformInfo, tab_id: str) -> int:
        """Count response elements without holding the lock (for polling loops)."""
        try:
            for sel in platform_info.response_selectors:
                elements = self._driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    return len(elements)
        except Exception:
            pass
        return 0

    def _get_latest_response_unlocked(self, platform_info: PlatformInfo, tab_id: str) -> str:
        """Read the last assistant message. No lock — used during polling."""
        try:
            for sel in platform_info.response_selectors:
                elements = self._driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    return elements[-1].text.strip()
        except Exception:
            pass
        return ""


# ── Module-level singleton ──
browser_manager = BrowserManager()
