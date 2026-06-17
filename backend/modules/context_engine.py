# Path: backend/modules/context_engine.py
# Use: Universal Context Engine — captures active window, URL, file, screenshot, and vision analysis.
"""
context_engine.py — MAX Universal Context Engine v1.0

Single-responsibility module that answers: "What is Sanket looking at right now?"
Works across ANY application — browsers, Office, VS Code, WhatsApp, etc.

Pipeline:
  1. get_active_window()   → win32gui + psutil → process name & window title
  2. get_browser_url()     → CDP (Chrome DevTools Protocol) → current tab URL
  3. get_file_context()    → title parsing + psutil fallback → open file path
  4. capture_screenshot()  → Pillow ImageGrab → window-cropped or full screen
  5. ask_vision_model()    → Groq Llama 4 Scout → structured description
  6. get_full_context()    → public orchestrator that assembles everything
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("MAX.CONTEXT_ENGINE")


# ════════════════════════════════════════════════════════
# Browser process names → CDP ports
# ════════════════════════════════════════════════════════
_BROWSER_PROCESSES: Dict[str, int] = {
    "chrome.exe": 9222,
    "opera.exe": 9222,
    "msedge.exe": 9222,
    "brave.exe": 9222,
    "firefox.exe": 0,       # Firefox uses a different protocol (Marionette), skip CDP
    "chromium.exe": 9222,
    "vivaldi.exe": 9222,
}

# File extensions we consider "editable files" for context extraction
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c", ".h",
    ".go", ".rs", ".rb", ".php", ".html", ".css", ".json", ".md",
    ".sql", ".sh", ".ps1", ".yaml", ".yml", ".toml", ".xml",
    ".txt", ".csv", ".log", ".ini", ".cfg", ".env",
}


class ContextEngine:
    """
    Universal context scraper.
    All methods are sync-safe or async. Public entry point: get_full_context().
    """

    def __init__(self) -> None:
        self._screenshot_dir: Optional[Path] = None

    def _ensure_screenshot_dir(self) -> Path:
        """Lazy-init screenshot directory under backend/data/screenshots."""
        if self._screenshot_dir is None:
            from config import config
            self._screenshot_dir = Path(config.DATA_DIR) / "screenshots"
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        return self._screenshot_dir

    # ──────────────────────────────────────────────────
    # METHOD 1: Active Window Detection
    # ──────────────────────────────────────────────────
    def get_active_window(self) -> Dict[str, str]:
        """
        Returns the currently focused window's title and process name.

        Uses:
          - win32gui.GetForegroundWindow()    → active window handle
          - win32gui.GetWindowText(hwnd)      → window title bar text
          - win32process.GetWindowThreadProcessId(hwnd) → PID
          - psutil.Process(pid).name()        → executable name

        Returns:
            {
                "window_title": "React Docs - Opera",
                "process_name": "opera.exe",
                "pid": 12345
            }
        """
        try:
            import win32gui
            import win32process
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            try:
                proc_name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                proc_name = "unknown"

            return {
                "window_title": title,
                "process_name": proc_name.lower(),
                "pid": pid,
            }
        except ImportError as e:
            logger.warning(f"win32gui/psutil not installed: {e}")
            return {"window_title": "", "process_name": "", "pid": 0}
        except Exception as e:
            logger.error(f"get_active_window failed: {e}")
            return {"window_title": "", "process_name": "", "pid": 0}

    # ──────────────────────────────────────────────────
    # METHOD 2: Browser URL via CDP
    # ──────────────────────────────────────────────────
    async def get_browser_url(self, window_title: str, process_name: str) -> Optional[str]:
        """
        If the active window is a browser, use CDP to get the current tab URL.

        Strategy:
          1. Check if process_name is in _BROWSER_PROCESSES
          2. Hit http://localhost:{port}/json to list all tabs
          3. Fuzzy-match: find the tab whose title is contained in window_title
          4. Return that tab's URL

        Returns None if not a browser or CDP unavailable.
        """
        if process_name not in _BROWSER_PROCESSES:
            return None

        port = _BROWSER_PROCESSES[process_name]
        if port == 0:
            # Firefox doesn't support CDP — extract from title if possible
            return self._extract_url_from_title(window_title)

        try:
            import aiohttp
            url = f"http://localhost:{port}/json"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                    if resp.status != 200:
                        logger.debug(f"CDP returned {resp.status}")
                        return None
                    tabs = await resp.json()

            if not tabs:
                return None

            # Fuzzy match: tab title should be a substring of the window title
            # Window title format: "Tab Title - Browser Name"
            best_tab = None
            best_score = 0

            for tab in tabs:
                if tab.get("type") != "page":
                    continue
                tab_title = tab.get("title", "")
                if not tab_title:
                    continue

                # Check if tab_title is contained within window_title
                if tab_title.lower() in window_title.lower():
                    score = len(tab_title)  # Longer match = better
                    if score > best_score:
                        best_score = score
                        best_tab = tab

            if best_tab:
                return best_tab.get("url", "")

            # Fallback: if no fuzzy match, return the first active tab's URL
            for tab in tabs:
                if tab.get("type") == "page":
                    return tab.get("url", "")

            return None

        except ImportError:
            logger.debug("aiohttp not installed — CDP unavailable")
            return None
        except Exception as e:
            logger.debug(f"CDP fetch failed: {e}")
            return None

    def _extract_url_from_title(self, title: str) -> Optional[str]:
        """Last-resort: try to find a URL in the window title itself."""
        url_match = re.search(r"https?://[^\s]+", title)
        return url_match.group(0) if url_match else None

    # ──────────────────────────────────────────────────
    # METHOD 3: File Context (VS Code / Editors)
    # ──────────────────────────────────────────────────
    def get_file_context(self, window_title: str, process_name: str) -> Optional[str]:
        """
        If the active window is an editor (VS Code, Notepad++, etc.),
        extract the file path from the window title.

        VS Code title formats:
          - "filename.py — folder - Visual Studio Code"
          - "● filename.py — folder - Visual Studio Code"  (unsaved)

        Notepad++ title formats:
          - "filename.py - Notepad++"

        Returns the file path string, or None.
        """
        editor_processes = {
            "code.exe": "vscode",
            "code - insiders.exe": "vscode",
            "notepad++.exe": "notepadpp",
            "notepad.exe": "notepad",
            "sublime_text.exe": "sublime",
            "atom.exe": "atom",
            "devenv.exe": "visual_studio",
            "pycharm64.exe": "pycharm",
            "idea64.exe": "intellij",
            "webstorm64.exe": "webstorm",
        }

        if process_name not in editor_processes:
            return None

        editor_type = editor_processes[process_name]

        try:
            if editor_type == "vscode":
                return self._parse_vscode_title(window_title)
            elif editor_type == "notepadpp":
                return self._parse_notepadpp_title(window_title)
            elif editor_type == "notepad":
                return self._parse_notepad_title(window_title)
            else:
                # Generic: try to find a file path in the title
                return self._parse_generic_editor_title(window_title)
        except Exception as e:
            logger.debug(f"File context extraction failed: {e}")
            return None

    def _parse_vscode_title(self, title: str) -> Optional[str]:
        """Extract file path from VS Code title bar."""
        # Remove "Visual Studio Code" suffix
        cleaned = re.sub(r"\s*[-—]\s*Visual Studio Code$", "", title).strip()
        # Remove unsaved indicator
        cleaned = cleaned.lstrip("● ").strip()

        if " — " in cleaned:
            # "filename.py — folder_path"
            parts = cleaned.split(" — ", 1)
            filename = parts[0].strip()
            folder = parts[1].strip()

            # Check if it looks like a file with extension
            if "." in filename:
                ext = "." + filename.rsplit(".", 1)[-1].lower()
                if ext in _CODE_EXTENSIONS:
                    return f"{folder}/{filename}"

        # Fallback: just the cleaned title if it has an extension
        if "." in cleaned:
            return cleaned

        return None

    def _parse_notepadpp_title(self, title: str) -> Optional[str]:
        """Extract file path from Notepad++ title."""
        # "C:\path\file.txt - Notepad++" or "*C:\path\file.txt - Notepad++"
        cleaned = re.sub(r"\s*-\s*Notepad\+\+$", "", title).strip()
        cleaned = cleaned.lstrip("*").strip()
        if os.path.sep in cleaned or "/" in cleaned:
            return cleaned
        return None

    def _parse_notepad_title(self, title: str) -> Optional[str]:
        """Extract filename from Notepad title."""
        # "filename - Notepad" or "*filename - Notepad"
        cleaned = re.sub(r"\s*-\s*Notepad$", "", title).strip()
        cleaned = cleaned.lstrip("*").strip()
        if cleaned and cleaned != "Untitled":
            return cleaned
        return None

    def _parse_generic_editor_title(self, title: str) -> Optional[str]:
        """Try to find a file path pattern in any editor's title."""
        # Look for path patterns: "C:\...\file.ext" or "/home/.../file.ext"
        path_match = re.search(r"([A-Za-z]:[\\\/][^\s]+\.\w+|\/[^\s]+\.\w+)", title)
        if path_match:
            return path_match.group(1)
        return None

    # ──────────────────────────────────────────────────
    # METHOD 4: Screenshot Capture
    # ──────────────────────────────────────────────────
    async def capture_screenshot(self, window_title: str = "") -> Optional[str]:
        """
        Take a screenshot of the active window (or full screen if minimized).

        Edge case handling:
          - GetWindowRect() returns -32000 for minimized windows → capture full screen
          - Crops to window bbox if available
          - Resizes to 1024x1024 max for vision model efficiency
          - Saves as JPEG (quality=70) for smaller payload

        Returns: path to the saved screenshot file, or None on failure.
        """
        try:
            from PIL import Image, ImageGrab

            ss_dir = self._ensure_screenshot_dir()
            path = ss_dir / "context_vision.jpg"
            bbox = None

            # Try to get the active window's bounding box
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                rect = win32gui.GetWindowRect(hwnd)
                left, top, right, bottom = rect

                # Minimized window check: coords will be -32000 or similar garbage
                if left < -10000 or top < -10000 or right <= left or bottom <= top:
                    logger.debug("Window appears minimized → capturing full screen")
                    bbox = None
                else:
                    # Clamp to non-negative (multi-monitor can give negative coords)
                    left = max(0, left)
                    top = max(0, top)
                    bbox = (left, top, right, bottom)
            except Exception as e:
                logger.debug(f"Window rect failed, capturing full screen: {e}")

            def _capture():
                if bbox:
                    img = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
                else:
                    img = ImageGrab.grab(all_screens=True).convert("RGB")
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                img.save(str(path), quality=70, optimize=True)

            await asyncio.to_thread(_capture)
            return str(path)

        except ImportError:
            logger.warning("Pillow not installed — cannot capture screenshot")
            return None
        except Exception as e:
            logger.error(f"Screenshot capture failed: {e}")
            return None

    # ──────────────────────────────────────────────────
    # METHOD 5: Vision Model (Groq Llama 4 Scout)
    # ──────────────────────────────────────────────────
    async def ask_vision_model(
        self,
        image_path: str,
        user_query: str,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """
        Send screenshot + context metadata to Groq Vision for analysis.

        Uses the existing analyze_image_with_prompt from llm.py which handles:
          - Key pool leasing via api_utils.key_pool
          - execute_with_retry for rate-limit resilience
          - Base64 encoding and resizing

        The metadata (window title, URL, process, file) is injected into the
        prompt so the vision model can give contextually rich answers.
        """
        # Build a rich prompt with metadata context
        context_lines = []
        if metadata:
            if metadata.get("window_title"):
                context_lines.append(f"Window: {metadata['window_title']}")
            if metadata.get("process_name"):
                context_lines.append(f"App: {metadata['process_name']}")
            if metadata.get("browser_url"):
                context_lines.append(f"URL: {metadata['browser_url']}")
            if metadata.get("file_path"):
                context_lines.append(f"File: {metadata['file_path']}")

        context_block = ""
        if context_lines:
            context_block = (
                "Context about the user's active window:\n"
                + "\n".join(f"  - {line}" for line in context_lines)
                + "\n\n"
            )

        full_prompt = (
            f"{context_block}"
            f"User's question: {user_query}\n\n"
            "Instructions: Answer in 2-3 SHORT sentences. "
            "Tell the user WHAT app is open and WHAT content is visible. "
            "If there's text, summarize it briefly. If there's code, mention language and purpose. "
            "If it's a webpage, mention the site and what's showing. "
            "DO NOT mention technical details like encoding, zoom level, line endings, "
            "font names, character counts, file format metadata, or UI chrome details. "
            "Speak like a helpful friend describing what they see, not a technical report."
        )

        try:
            from modules.llm import analyze_image_with_prompt
            result = await analyze_image_with_prompt(image_path, full_prompt)
            return result
        except Exception as e:
            logger.error(f"Vision model call failed: {e}")
            return f"Vision analysis failed: {e}"

    # ──────────────────────────────────────────────────
    # METHOD 6: get_full_context() — Public Orchestrator
    # ──────────────────────────────────────────────────
    async def get_full_context(self, user_query: str = "What is on my screen?") -> Dict[str, Any]:
        """
        The main public entry point. Assembles the full context pipeline:

        1. Detect active window (process + title)
        2. If browser → get URL via CDP
        3. If editor → extract file path from title
        4. Capture screenshot of active window
        5. Send screenshot + metadata to vision model
        6. Return structured result

        Returns:
            {
                "window_title": str,
                "process_name": str,
                "app_type": "browser" | "editor" | "office" | "media" | "other",
                "browser_url": str | None,
                "file_path": str | None,
                "screenshot_path": str | None,
                "vision_response": str,
            }
        """
        # Step 1: Active window detection
        window_info = await asyncio.to_thread(self.get_active_window)
        title = window_info.get("window_title", "")
        process = window_info.get("process_name", "")

        logger.info(f"🔍 Active window: [{process}] {title}")

        # Step 2: Classify app type
        app_type = self._classify_app(process, title)

        # Step 3: Browser URL (if applicable)
        browser_url = None
        if app_type == "browser":
            browser_url = await self.get_browser_url(title, process)
            if browser_url:
                logger.info(f"🌐 Browser URL: {browser_url}")

        # Step 4: File context (if applicable)
        file_path = None
        if app_type == "editor":
            file_path = self.get_file_context(title, process)
            if file_path:
                logger.info(f"📄 File: {file_path}")

        # Step 5: Capture screenshot
        screenshot_path = await self.capture_screenshot(title)

        # Step 6: Vision analysis
        vision_response = ""
        if screenshot_path:
            metadata = {
                "window_title": title,
                "process_name": process,
                "app_type": app_type,
                "browser_url": browser_url,
                "file_path": file_path,
            }
            vision_response = await self.ask_vision_model(
                screenshot_path, user_query, metadata
            )
        else:
            vision_response = (
                f"Could not capture screenshot. Active window: {title} ({process})"
            )

        return {
            "window_title": title,
            "process_name": process,
            "app_type": app_type,
            "browser_url": browser_url,
            "file_path": file_path,
            "screenshot_path": screenshot_path,
            "vision_response": vision_response,
        }

    # ──────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────
    def _classify_app(self, process_name: str, window_title: str) -> str:
        """Classify the active application into a category."""
        if process_name in _BROWSER_PROCESSES:
            return "browser"

        editor_processes = {
            "code.exe", "code - insiders.exe", "notepad++.exe", "notepad.exe",
            "sublime_text.exe", "atom.exe", "devenv.exe", "pycharm64.exe",
            "idea64.exe", "webstorm64.exe",
        }
        if process_name in editor_processes:
            return "editor"

        office_processes = {
            "winword.exe", "excel.exe", "powerpnt.exe",
            "outlook.exe", "onenote.exe", "mspub.exe",
            "msaccess.exe",
        }
        if process_name in office_processes:
            return "office"

        media_processes = {
            "spotify.exe", "vlc.exe", "wmplayer.exe",
            "foobar2000.exe", "itunes.exe",
        }
        if process_name in media_processes:
            return "media"

        chat_processes = {
            "whatsapp.exe", "discord.exe", "slack.exe",
            "telegram.exe", "teams.exe", "signal.exe",
        }
        if process_name in chat_processes:
            return "chat"

        terminal_processes = {
            "windowsterminal.exe", "cmd.exe", "powershell.exe",
            "pwsh.exe", "conhost.exe", "wt.exe",
        }
        if process_name in terminal_processes:
            return "terminal"

        return "other"


# ── Singleton ──
_context_engine: Optional[ContextEngine] = None


def get_context_engine() -> ContextEngine:
    """Get or create the singleton ContextEngine instance."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine()
    return _context_engine
