# Path: backend/modules/ai_orchestrator/platform_handler.py
# Use: Executes tasks on designated platform integrations.
# platform_handler.py — Selenium driver wrapper for AI Orchestrator using Native Chrome (Humanized Delays)
import os
import time
import logging
import platform as os_platform
import pyperclip
# codex-changes detail: keep orchestrator platform handler importable without optional browser automation dependency.
try:
    import undetected_chromedriver as uc
except ImportError:
    uc = None
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from modules.ai_orchestrator.platform_config import PlatformInfo, PLATFORMS

logger = logging.getLogger("MAX.ORCHESTRATOR.HANDLER")


class AIOrchestratorDriver:
    """Thread-safe singleton driver manager with auto-recovery."""
    _instance = None
    _profile_path = None
    _binary_path = None
    _lock = False

    @classmethod
    def get_driver(cls, profile_path: str = None, binary_path: str = None):
        # codex-changes detail: fail at browser-use time with an actionable dependency message.
        if uc is None:
            raise RuntimeError("undetected-chromedriver is not installed; AI platform browser automation is unavailable.")
        if cls._instance is None:
            cls._profile_path = profile_path
            cls._binary_path = binary_path
            cls._instance = cls._create_driver(profile_path, binary_path)
        else:
            # Check if driver is still alive with health check
            try:
                cls._instance.title
                # Also check if window handles exist (more reliable)
                handles = cls._instance.window_handles
                if not handles:
                    raise Exception("No window handles")
            except Exception:
                logger.info("Existing background driver session dead. Creating new instance...")
                try:
                    cls._instance.quit()
                except Exception:
                    pass
                cls._instance = None
                cls._instance = cls._create_driver(cls._profile_path, cls._binary_path)
        return cls._instance

    @classmethod
    def _detect_chrome_version(cls, binary_path: str = None) -> int:
        """Auto-detect Chrome version for undetected-chromedriver compatibility."""
        try:
            import subprocess
            import re
            
            # Fallback: try google-chrome or chromium directly (ignoring Opera)
            for cmd in ["google-chrome", "chromium", "chromium-browser", "chrome"]:
                try:
                    result = subprocess.run(
                        [cmd, "--version"],
                        capture_output=True, text=True, timeout=10
                    )
                    version_str = result.stdout.strip()
                    match = re.search(r'(\d+)', version_str)
                    if match:
                        version = int(match.group(1))
                        logger.info(f"Detected native Chrome version via {cmd}: {version}")
                        return version
                except Exception:
                    continue
                    
        except Exception as e:
            logger.warning(f"Browser version auto-detection failed: {e}")
        
        # Final fallback: try to get from uc auto-detection
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.core.os_manager import ChromeType
            driver_path = ChromeDriverManager(chrome_type=ChromeType.GOOGLE).install()
            logger.info(f"Using webdriver-manager auto-detected driver: {driver_path}")
            return None  # Let uc auto-detect
        except Exception:
            pass
            
        logger.warning("Could not detect browser version, using auto-detect fallback")
        return None

    @classmethod
    def _create_driver(cls, profile_path: str = None, binary_path: str = None):
        options = uc.ChromeOptions()
        
        # Disable automation flag to bypass cloudflare/bot checks
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--start-minimized")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0")
        
        if binary_path:
            logger.info(f"Config asked for Opera at {binary_path}, but forcing native Google Chrome for stable AI scraping.")
        if profile_path:
            logger.info("Ignoring Opera profile to prevent 'session locked' or 'session not created' errors.")
        
        options.page_load_strategy = 'eager'
        
        try:
            kwargs = {"options": options}
            
            logger.info("Launching background Google Chrome instance with uc auto-version detection...")
            driver = uc.Chrome(**kwargs)
            driver.set_page_load_timeout(30)
            
            # Execute CDP commands to prevent detection
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                '''
            })
            return driver
        except Exception as e:
            logger.error(f"Background Chrome launch failed completely: {e}")
            raise e

    @classmethod
    def close_driver(cls):
        if cls._instance:
            try:
                cls._instance.quit()
            except Exception:
                pass
            cls._instance = None

    @classmethod
    def reset_driver(cls):
        """Force reset driver on critical failure."""
        cls.close_driver()
        time.sleep(1)
        cls._instance = cls._create_driver(cls._profile_path, cls._binary_path)
        return cls._instance


class PlatformHandler:
    def __init__(self, config):
        self.config = config
        self._last_error_count = 0
        self._max_consecutive_errors = 3

    def _check_cloudflare(self, driver, max_wait: int = 15) -> bool:
        """Check and wait for Cloudflare challenge. Returns True if bypassed."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                page_title = driver.title.lower() if driver.title else ""
                page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                
                cf_indicators = [
                    "just a moment", "cloudflare", "checking your browser",
                    "attention required", "ddos protection", "verify you are human"
                ]
                
                if any(kw in page_title for kw in cf_indicators) or any(kw in page_text for kw in cf_indicators):
                    logger.info(f"Cloudflare challenge detected, waiting... ({time.time()-start:.1f}s)")
                    time.sleep(2.5)
                    continue
                    
                # Check for CAPTCHA
                captcha_selectors = [
                    "iframe[src*='recaptcha']", ".g-recaptcha", ".h-captcha",
                    "[data-sitekey]", "iframe[src*='captcha']"
                ]
                for sel in captcha_selectors:
                    try:
                        if driver.find_element(By.CSS_SELECTOR, sel):
                            logger.warning(f"CAPTCHA detected on page - cannot bypass automatically")
                            return False
                    except Exception:
                        continue
                        
                return True
            except Exception:
                time.sleep(1)
        
        logger.warning("Cloudflare/CAPTCHA bypass timeout")
        return False

    def _find_element_with_retry(self, driver, selectors, timeout=10, condition=EC.element_to_be_clickable):
        """Find element trying multiple selectors with wait condition."""
        last_error = None
        for selector in selectors:
            try:
                elem = WebDriverWait(driver, timeout).until(
                    condition((By.CSS_SELECTOR, selector))
                )
                return elem
            except Exception as e:
                last_error = e
                continue
        logger.error(f"Failed to find element with any selector: {selectors}. Last error: {last_error}")
        return None

    def _inject_text(self, textarea, text: str, platform_name: str) -> bool:
        """Safely inject text into textarea using best method."""
        try:
            # Focus and clear
            textarea.click()
            time.sleep(0.3)
            control_key = Keys.COMMAND if os_platform.system() == "Darwin" else Keys.CONTROL
            textarea.send_keys(control_key, 'a')
            time.sleep(0.1)
            textarea.send_keys(Keys.BACKSPACE)
            time.sleep(0.2)

            # Choose injection method based on text length
            if len(text) < 800:
                # Direct send_keys for short text
                textarea.send_keys(text)
            else:
                # Clipboard paste for long text
                try:
                    original_clipboard = pyperclip.paste()
                except Exception:
                    original_clipboard = ""
                
                pyperclip.copy(text)
                textarea.send_keys(control_key, 'v')
                time.sleep(0.3)
                
                # Restore original clipboard
                try:
                    pyperclip.copy(original_clipboard)
                except Exception:
                    pass
                    
            return True
        except Exception as e:
            logger.error(f"Text injection failed for {platform_name}: {e}")
            return False

    def _submit_prompt(self, driver, textarea, platform_info) -> bool:
        """Submit prompt trying multiple methods."""
        # Method 1: Click submit button
        for selector in platform_info.submit_selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                if btn.is_enabled() and btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    logger.info(f"Clicked submit button: {selector}")
                    return True
            except Exception:
                continue
        
        # Method 2: Send ENTER key
        try:
            textarea.send_keys(Keys.ENTER)
            logger.info("Submitted via ENTER key")
            return True
        except Exception as e:
            logger.warning(f"ENTER key submission failed: {e}")
            
        # Method 3: JavaScript form submission
        try:
            driver.execute_script("""
                const event = new KeyboardEvent('keydown', {
                    key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                    bubbles: true, cancelable: true
                });
                document.activeElement.dispatchEvent(event);
            """)
            return True
        except Exception as e:
            logger.error(f"JavaScript submission failed: {e}")
            
        return False

    def execute_query(self, platform_name: str, prompt_data: dict) -> str:
        """
        Executes query on the specified platform synchronously.
        Includes full error recovery and retry logic.
        """
        platform_info = PLATFORMS.get(platform_name.lower())
        if not platform_info:
            raise ValueError(f"Unknown platform: {platform_name}")

        opera_profile = getattr(self.config, 'OPERA_PROFILE_PATH', None)
        opera_binary = getattr(self.config, 'OPERA_BINARY_PATH', None)
        
        # Try with existing driver first, reset on repeated failures
        if self._last_error_count >= self._max_consecutive_errors:
            logger.warning("Too many consecutive errors, resetting driver...")
            driver = AIOrchestratorDriver.reset_driver()
            self._last_error_count = 0
        else:
            driver = AIOrchestratorDriver.get_driver(opera_profile, opera_binary)

        try:
            result = self._execute_with_driver(driver, platform_name, platform_info, prompt_data)
            self._last_error_count = 0  # Reset error count on success
            return result
        except Exception as e:
            self._last_error_count += 1
            logger.error(f"Platform execution failed: {e}")
            # Try one more time with fresh driver
            if self._last_error_count < 2:
                try:
                    driver = AIOrchestratorDriver.reset_driver()
                    result = self._execute_with_driver(driver, platform_name, platform_info, prompt_data)
                    self._last_error_count = 0
                    return result
                except Exception as e2:
                    logger.error(f"Retry also failed: {e2}")
            raise

    def _execute_with_driver(self, driver, platform_name: str, platform_info: PlatformInfo, prompt_data: dict) -> str:
        """Internal execution with given driver instance."""
        
        # 1. Navigate to platform
        logger.info(f"Navigating to {platform_info.name} URL: {platform_info.url_new_chat}")
        try:
            driver.get(platform_info.url_new_chat)
        except Exception as e:
            logger.warning(f"Page load timeout/error: {e}, continuing anyway...")
        
        # ---------------------------------------------------------
        # 🟢 THE FIX 1: 4 SECOND DELAY FOR WEBSITE OPENING 🟢
        # ---------------------------------------------------------
        logger.info("Humanizing delay: Waiting 4 seconds for page load and security checks...")
        time.sleep(4.0)

        # 2. Handle Cloudflare/bot verification
        if not self._check_cloudflare(driver):
            logger.warning("Cloudflare/CAPTCHA could not be bypassed, attempting to continue...")

        chunks = prompt_data.get("chunks", [""])
        image_path = prompt_data.get("image_path")
        is_chunked = prompt_data.get("is_chunked", False)

        from modules.ai_orchestrator.response_harvester import ResponseHarvester
        harvester = ResponseHarvester(driver, platform_info)

        # 3. Inject chunks sequentially
        final_response = ""
        for idx, chunk in enumerate(chunks):
            # Locate Textarea
            textarea = self._find_element_with_retry(
                driver, 
                platform_info.textarea_selectors,
                timeout=15,
                condition=EC.element_to_be_clickable
            )
            
            if not textarea:
                raise Exception(f"Failed to find prompt input textarea on {platform_info.name}.")

            # Prepare prompt with chunking instructions
            current_prompt = chunk
            if is_chunked:
                if idx < len(chunks) - 1:
                    current_prompt = (
                        f"[Part {idx+1}/{len(chunks)}] Please acknowledge receipt only. "
                        f"Do NOT process yet. Wait for the final part.\n\n{chunk}"
                    )
                else:
                    current_prompt = (
                        f"[Final Part {idx+1}/{len(chunks)}] Now process ALL parts together. "
                        f"Here is the final piece:\n\n{chunk}"
                    )

            logger.info(f"Injecting prompt chunk {idx+1}/{len(chunks)} ({len(current_prompt)} chars)")
            
            if not self._inject_text(textarea, current_prompt, platform_info.name):
                raise Exception(f"Failed to inject text into {platform_info.name}")

            # ---------------------------------------------------------
            # 🟢 THE FIX 2: 2 SECOND DELAY AFTER TEXT PASTE 🟢
            # ---------------------------------------------------------
            logger.info("Humanizing delay: Waiting 2 seconds after pasting text...")
            time.sleep(2.0)

            # Upload image on final chunk if supported
            if image_path and idx == len(chunks) - 1 and platform_info.supports_image:
                self._upload_image(driver, image_path, platform_info.name)

            # Submit prompt
            if not self._submit_prompt(driver, textarea, platform_info):
                raise Exception(f"Failed to submit prompt on {platform_info.name}")

            time.sleep(1.5)

            # Wait for response
            timeout = getattr(self.config, 'AI_RESPONSE_TIMEOUT', 120)
            success = harvester.wait_for_response(timeout=timeout)
            
            if not success:
                logger.warning("Timeout waiting for response to complete. Harvesting what we have.")

            # Retrieve response text
            if idx == len(chunks) - 1:
                final_response = harvester.harvest_response()
                if not final_response or len(final_response.strip()) < 5:
                    logger.warning("Empty or very short response harvested, trying fallback...")
                    time.sleep(2)
                    final_response = harvester.harvest_response()
            else:
                # For intermediate chunks, just wait a bit for the ack
                time.sleep(2.0)

        # Cleanup only if configured
        if not getattr(self.config, 'AI_KEEP_BROWSER_OPEN', True):
            AIOrchestratorDriver.close_driver()

        return final_response if final_response else "No response received from the AI platform."

    def _upload_image(self, driver, image_path: str, platform_name: str):
        """Upload image file to the chat interface."""
        try:
            logger.info(f"Uploading file: {image_path}")
            # Try multiple methods to find file input
            file_input = None
            file_selectors = [
                "input[type='file']",
                "input[accept*='image']",
                "input[accept='*']",
                "#file-input"
            ]
            for sel in file_selectors:
                try:
                    elems = driver.find_elements(By.CSS_SELECTOR, sel)
                    for elem in elems:
                        if elem.is_displayed() or elem.get_attribute("type") == "file":
                            file_input = elem
                            break
                    if file_input:
                        break
                except Exception:
                    continue
            
            if file_input:
                file_input.send_keys(image_path)
                logger.info("Image upload initiated")
                time.sleep(3.0)
            else:
                # Try clicking attachment button first
                attach_selectors = [
                    "button[aria-label*='attach']",
                    "button[aria-label*='upload']",
                    "button[aria-label*='file']",
                    "svg[aria-label*='attach']",
                    "[data-testid='attach-button']"
                ]
                for sel in attach_selectors:
                    try:
                        btn = driver.find_element(By.CSS_SELECTOR, sel)
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
                        if file_inputs:
                            file_inputs[0].send_keys(image_path)
                            time.sleep(3.0)
                            logger.info("Image uploaded via attachment button")
                            return
                    except Exception:
                        continue
                logger.warning(f"Could not find file upload mechanism for {platform_name}")
        except Exception as e:
            logger.warning(f"Failed to upload image: {e}. Will proceed without image upload.")
