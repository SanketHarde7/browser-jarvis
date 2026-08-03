# Path: backend/modules/skills.py
# Use: Registers and triggers assistant's executable skills.
"""
skills.py — MAX v4.6 (Multi-Skill Executor & URL Cleaner)
"""
from urllib.parse import quote_plus
import re
import os
import time
import json
import asyncio
import threading
import subprocess
import logging
import platform
import webbrowser
from modules.voice_engine import VoiceEngine
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from modules.action_scheduler import ActionScheduler
from typing import Dict, Any, Optional, List, Tuple
from modules.ai_orchestrator.research_agent import DeepResearchAgent
from config import Config       
from api_utils import execute_with_retry

logger = logging.getLogger("MAX.SKILLS")

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except Exception as e:
    # codex-changes detail: pyautogui can raise DISPLAY-related errors in headless environments; keep non-GUI skills available.
    logger.warning(f"pyautogui unavailable; GUI automation skills disabled: {e}")
    pyautogui = None
    PYAUTOGUI_AVAILABLE = False

try:
    import pywhatkit
    PYWHATKIT_AVAILABLE = True
except (ImportError, Exception):
    PYWHATKIT_AVAILABLE = False

DATA_SKILLS = {
    "weather", "search", "note", "timer",
    "find_and_explain", "list_files", "read_file",
    "code_review", "run_code", "search_files",
    "read_screen", "list_windows",
    "email_check", "calendar_today", "calendar_week",
    "browser_scrape", "plugin_list", "list_apps",
    "sysinfo", "top_processes", "reminder_list",
    "kb_search", "kb_list", "kb_stats", "kb_rebuild",
    "ai_ask", "ai_ask_screen", "ai_ask_file", "ai_ask_clipboard",
    "ai_compare", "ai_chain", "ai_route", "ai_workflow",
    "ai_workflow_save", "ai_workflow_list",
    "check_process", "uptime", "get_recent_history"
}

LONG_RESULT_SKILLS = {
    "find_and_explain", "list_files", "read_file",
    "code_review", "run_code", "search_files",
    "read_screen", "list_windows", "browser_scrape",
    "list_apps", "top_processes", "get_recent_history",
    "ai_ask", "ai_ask_screen", "ai_ask_file", "ai_ask_clipboard",
    "ai_compare", "ai_chain", "ai_route", "ai_workflow",
    "ai_workflow_save", "ai_workflow_list"
}

TTS_MAX_CHARS = 280


def focus_target_window(target_name: str = "") -> bool:
    """Bring target window (File Explorer, Downloads, Notepad, Chrome, etc.) to the FOREGROUND in Windows."""
    if platform.system() != "Windows":
        return False
    try:
        import pygetwindow as gw
        import win32gui
        import win32con

        clean_target = (target_name or "").lower().strip()
        search_terms = []
        if clean_target:
            search_terms.append(clean_target)
            if "download" in clean_target:
                search_terms.extend(["downloads", "file explorer", "explorer"])
            elif "explorer" in clean_target:
                search_terms.extend(["file explorer", "explorer", "downloads"])
        else:
            search_terms = ["downloads", "file explorer", "explorer"]

        all_wins = gw.getAllWindows()
        matched_win = None

        for term in search_terms:
            for w in all_wins:
                if w.visible and w.title and term in w.title.lower():
                    matched_win = w
                    break
            if matched_win:
                break

        if matched_win:
            try:
                if matched_win.isMinimized:
                    win32gui.ShowWindow(matched_win._hWnd, win32con.SW_RESTORE)
                win32gui.ShowWindow(matched_win._hWnd, win32con.SW_SHOW)
                win32gui.SetForegroundWindow(matched_win._hWnd)
                time.sleep(0.3)
                return True
            except Exception as win32_err:
                logger.debug(f"win32gui SetForegroundWindow failed ({win32_err}), trying pygetwindow activate...")
                try:
                    matched_win.activate()
                    return True
                except Exception as act_err:
                    logger.debug(f"pygetwindow activate failed: {act_err}")
    except Exception as err:
        logger.warning(f"focus_target_window error: {err}")
    return False


def _url_to_label(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return "Website"
    raw = raw.replace("https://", "").replace("http://", "")
    raw = raw.replace("www.", "")
    raw = raw.split("/")[0]
    raw = raw.split("?")[0].split("#")[0].split(":")[0]
    name = raw.split(".")[0] if raw else "Website"
    return name.capitalize() if name else "Website"


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _truncate_for_tts(result: str, skill_name: str) -> str:
    if skill_name not in LONG_RESULT_SKILLS:
        return result
    lines = result.split('\n')
    content = ' '.join(l for l in lines if l.strip() and not l.startswith(
        ('📄', '📁', '🔍', '📸', '📧', '📅', '🌐', '🔌')))
    if len(content) <= TTS_MAX_CHARS:
        return content
    truncated = content[:TTS_MAX_CHARS]
    last = max(truncated.rfind('. '), truncated.rfind('! '), truncated.rfind('? '))
    if last > TTS_MAX_CHARS // 2:
        truncated = truncated[:last + 1]
    return f"{truncated} Details on screen."


def open_url_in_browser(url: str) -> None:
    logger.info(f"Opening URL in browser: {url}")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        if platform.system() == "Windows":
            try:
                import webbrowser
                webbrowser.open(url, new=2, autoraise=True)
            except Exception as wb_err:
                logger.debug(f"webbrowser module open failed ({wb_err}), trying os.startfile...")
                try:
                    os.startfile(url)
                except Exception as os_err:
                    logger.debug(f"os.startfile failed ({os_err}), falling back to shell start command...")
                    subprocess.Popen(f'start "" "{url}"', shell=True)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception as e:
        logger.error(f"Native browser open failed: {e}. Falling back to webbrowser.")
        try:
            import webbrowser
            webbrowser.open(url, new=2, autoraise=True)
        except Exception as e2:
            logger.error(f"Fallback webbrowser failed: {e2}")


class SkillsEngine:

    SKILL_PATTERN = re.compile(r'\[SKILL:([a-zA-Z_]+)(?::([^\]]*))?\]')

    def __init__(self, config):
        self.config = config
        self._code_engine    = None
        self._file_manager   = None
        self._email_agent    = None
        self._calendar_agent = None
        self._browser_agent  = None
        self._smarthome_agent= None
        self._plugin_loader  = None
        self._app_indexer    = None
        self._pending_links  = []
        self.skills_registry = self._register_skills()
        self._load_plugins()
        self.scheduler = ActionScheduler(self.config, self)
        self.scheduler.start()
        VoiceEngine.get_instance()

    # ── Lazy properties ──────────────────────────────────────

    @property
    def code_engine(self):
        if not self._code_engine:
            from modules.code_engine import get_code_engine
            self._code_engine = get_code_engine(self.config)
        return self._code_engine

    @property
    def file_manager(self):
        if not self._file_manager:
            from modules.file_manager import get_file_manager
            self._file_manager = get_file_manager()
        return self._file_manager

    @property
    def email_agent(self):
        if not self._email_agent:
            from modules.email_agent import get_email_agent
            self._email_agent = get_email_agent()
        return self._email_agent

    @property
    def calendar_agent(self):
        if not self._calendar_agent:
            from modules.calendar_agent import get_calendar_agent
            self._calendar_agent = get_calendar_agent()
        return self._calendar_agent

    @property
    def browser_agent(self):
        if not self._browser_agent:
            from modules.browser_agent import get_browser_agent
            self._browser_agent = get_browser_agent()
        return self._browser_agent

    @property
    def smarthome_agent(self):
        if not self._smarthome_agent:
            from modules.smarthome_agent import get_smarthome_agent
            self._smarthome_agent = get_smarthome_agent()
        return self._smarthome_agent

    @property
    def plugin_loader(self):
        if not self._plugin_loader:
            from modules.plugin_loader import get_plugin_loader
            self._plugin_loader = get_plugin_loader()
        return self._plugin_loader

    @property
    def app_indexer(self):
        if not self._app_indexer:
            from modules.app_indexer import get_app_indexer
            self._app_indexer = get_app_indexer(self.config)
        return self._app_indexer

    def _resolve_user_path(self, path_str: str) -> Path:
        """Resolve keywords like 'desktop', 'documents', 'downloads' to absolute paths."""
        if not path_str:
            return None
        p_clean = path_str.strip()
        p_lower = p_clean.lower()
        home = Path.home()
        onedrive = home / "OneDrive"
        
        # Helper to get the correct base path for a special folder
        def get_base_path(folder: str) -> Path:
            if folder in ["desktop", "documents", "pictures"]:
                # Check if OneDrive has hijacked the folder
                if (onedrive / folder.title()).exists():
                    return onedrive / folder.title()
            return home / folder.title()

        special_folders = ["desktop", "documents", "downloads", "pictures", "music", "videos"]
        
        # Exact match
        if p_lower in special_folders:
            return get_base_path(p_lower)
            
        # Prefix match (e.g., "desktop/my_folder")
        for folder in special_folders:
            if p_lower.startswith(f"{folder}/") or p_lower.startswith(f"{folder}\\"):
                sub_path = p_clean[len(folder)+1:]
                return get_base_path(folder) / sub_path
                
        # Return path object
        return Path(p_clean).expanduser()

    def _load_plugins(self):
        try:
            self.plugin_loader.load_all()
        except Exception as e:
            logger.warning(f"Plugin load failed: {e}")

    def _register_skills(self) -> Dict[str, Any]:
        base = {
            "weather":           self._skill_weather,
            "timer":             self._skill_timer,
            "alarm":             self._skill_alarm,
            "note":              self._skill_note,
            "note_delete":       self._skill_note_delete,
            "note_clear":        self._skill_note_clear,
            "search":            self._skill_web_search,
            "youtube_search":    self._skill_youtube_search,
            "youtube_play":      self._skill_youtube_play,
            "clear_memory":      self._skill_clear_memory,
            "add_rule":          self._skill_add_rule,
            "media":             self._skill_media,
            "reminder_set":      self._skill_reminder_set,
            "reminder_list":     self._skill_reminder_list,
            "reminder_clear":    self._skill_reminder_clear,
            "write_code":        self._skill_write_code,
            "run_code":          self._skill_run_code,
            "code_review":       self._skill_code_review,
            "fix_code":          self._skill_fix_code,
            "project_scaffold":  self._skill_project_scaffold,
            "find_and_explain":  self._skill_find_and_explain,
            "list_files":        self._skill_list_files,
            "read_file":         self._skill_read_file,
            "edit_file":         self._skill_edit_file,
            "search_files":      self._skill_search_files,
            "read_screen":       self._skill_read_screen,
            "list_windows":      self._skill_list_windows,
            "screenshot":        self._skill_screenshot,
            "screen_record":     self._skill_screen_record,
            "open_link":         self._skill_open_link,
            "open_link_select":  self._skill_open_link_select,
            "open_app":          self._skill_open_app,
            "list_apps":         self._skill_list_apps,
            "rebuild_app_index": self._skill_rebuild_app_index,
            "web_open":          self._skill_web_open,
            "volume":            self._skill_volume_control,
            "brightness":        self._skill_brightness,
            "clipboard":         self._skill_clipboard,
            "lock_pc":           self._skill_lock_pc,
            "system_shutdown":   self._skill_system_shutdown,
            "system_restart":    self._skill_system_restart,
            "whatsapp_message":  self._skill_whatsapp_message,
            "whatsapp_screenshot": self._skill_whatsapp_screenshot,
            "type_text":         self._skill_type_text,
            "key_press":         self._skill_key_press,
            "press_key":         self._skill_key_press,
            "quit_max":          self._skill_quit_max,  
            "email_send":        self._skill_email_send,
            "email_check":       self._skill_email_check,
            "calendar_today":    self._skill_calendar_today,
            "calendar_add":      self._skill_calendar_add,
            "calendar_week":     self._skill_calendar_week,
            "browser_open":      self._skill_browser_open,
            "browser_click":     self._skill_browser_click,
            "browser_type":      self._skill_browser_type,
            "browser_scrape":    self._skill_browser_scrape,
            "fan":               self._skill_fan,
            "smart_light":       self._skill_smart_light,
            "smart_ac":          self._skill_smart_ac,
            "plugin_list":       self._skill_plugin_list,
            "plugin_reload":     self._skill_plugin_reload,
            "kb_search":         self._skill_kb_search,
            "kb_rebuild":        self._skill_kb_rebuild,
            "kb_list":           self._skill_kb_list,
            "kb_stats":          self._skill_kb_stats,
            "deep_research":     self._skill_deep_research,
            "research":          self._skill_research,
            "create_file":       self._skill_create_file,
            "schedule_action":   self._skill_schedule_action,
            # ── AI Orchestrator skills ──────────────────────────────────────
            "ai_ask":            self._skill_ai_ask,
            "ai_chain":          self._skill_ai_chain,
            "count":             self._skill_count,
            # ── File Operations ────────────────────────────────────────────
            "save_as":           self._skill_save_as,
            "rename_file":       self._skill_rename_file,
            "delete_file":       self._skill_delete_file,
            "move_file":         self._skill_file_move,
            "copy_file":         self._skill_file_copy,
            "open_file":         self._skill_open_file,
            "file_find":         self._skill_file_find,
            "file_send_whatsapp": self._skill_file_send_whatsapp,
            "file_upload_browser": self._skill_file_upload_browser,
            "file_list_by_date": self._skill_file_list_by_date,
            "file_list_whatsapp": self._skill_file_list_whatsapp,
            "folder_screenshot_whatsapp": self._skill_folder_screenshot_whatsapp,
            "vscode_git_push":  self._skill_vscode_git_push,
            "vscode_close_folder": self._skill_vscode_close_folder,
            "close_app":         self._skill_close_app,
            "close_window":      self._skill_close_window,
            "key_chord":         self._skill_key_chord,
            # ── System Toggles ─────────────────────────────────────────────
            "wifi_toggle":       self._skill_wifi_toggle,
            "bluetooth_toggle":  self._skill_bluetooth_toggle,
            "night_light":       self._skill_night_light,
            "uptime":            self._skill_uptime,
            "check_process":     self._skill_check_process,
            "get_recent_history": self._skill_get_recent_history,
        }

        # ── DYNAMIC MODULE LOADING ──
        try:
            import importlib
            import pkgutil
            import inspect
            from skills.base_skill import BaseSkill
            import skills

            for loader, module_name, is_pkg in pkgutil.iter_modules(skills.__path__):
                if module_name == "base_skill": continue
                full_module_name = f"skills.{module_name}"
                try:
                    mod = importlib.import_module(full_module_name)
                    for name, obj in inspect.getmembers(mod):
                        if inspect.isclass(obj) and issubclass(obj, BaseSkill) and obj is not BaseSkill:
                            skill_instance = obj()
                            base[skill_instance.name] = skill_instance.execute
                            logger.info(f"Dynamically registered skill: {skill_instance.name}")
                except Exception as e:
                    logger.error(f"Failed to load dynamic skill module {module_name}: {e}")
        except Exception as e:
            logger.error(f"Dynamic skill loading crashed: {e}")

        try:
            pl = self.plugin_loader
            for name in pl.handlers:
                base[name] = lambda *args, n=name: pl.execute(n, *args)
        except Exception:
            pass
        return base
    def _parse_parameters(self, skill_name: str, params_str: str) -> list:
        skill_name = skill_name.lower().strip()
        params_str = params_str.strip()
        if not params_str:
            return []
            
        SINGLE_TEXT_SKILLS = {
            "search", "web_search", "youtube_play", "youtube_search", 
            "type_text", "kb_search", "research", "web_open", "browser_open"
        }
        if skill_name in SINGLE_TEXT_SKILLS:
            return [params_str]
            
        # Try to resolve parameter count from function signature
        if skill_name in self.skills_registry:
            func = self.skills_registry[skill_name]
            try:
                import inspect
                sig = inspect.signature(func)
                params_count = 0
                has_var_positional = False
                for param in sig.parameters.values():
                    if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                        params_count += 1
                    elif param.kind == inspect.Parameter.VAR_POSITIONAL:
                        has_var_positional = True
                
                if params_count > 0 and not has_var_positional:
                    parts = params_str.split(":", params_count - 1)
                    return [p.strip() for p in parts]
            except Exception as e:
                logger.warning(f"Failed to inspect signature for {skill_name}: {e}")
                
        # Fallback to standard colon splitting
        return [p.strip() for p in params_str.split(":") if p.strip()]

    # ════════════════════════════════════════════
    # DISPATCHER (MULTI-SKILL SUPPORT)
    # ════════════════════════════════════════════

    async def parse_and_execute(self, response_text: str, memory_context: str = "", user_request: str = "") -> Dict[str, Any]:
        if user_request:
            self.last_user_request = user_request
        matches = list(self.SKILL_PATTERN.finditer(response_text))
        
        if not matches:
            return {"executed": False, "clean_text": response_text, "is_data_skill": False}

        # Deduplicate redundant skills (e.g. open_app:youtube + youtube_play:music)
        filtered_matches = []
        has_youtube_play = any(m.group(1).lower() in ("youtube_play", "youtube_search") for m in matches)
        has_whatsapp_msg = any(m.group(1).lower() in ("whatsapp_message", "whatsapp_screenshot") for m in matches)
        
        for m in matches:
            name = m.group(1).lower()
            params = m.group(2) or ""
            
            if name in ("open_app", "web_open"):
                param_lower = params.lower()
                if has_youtube_play and "youtube" in param_lower:
                    logger.info(f"Skipping redundant {name}:{params} because specific youtube skill is active.")
                    continue
                if has_whatsapp_msg and "whatsapp" in param_lower:
                    logger.info(f"Skipping redundant {name}:{params} because whatsapp skill is active.")
                    continue
            filtered_matches.append(m)
        
        matches = filtered_matches
        
        if not matches:
            return {"executed": False, "clean_text": response_text, "is_data_skill": False}

        results = []
        tts_results = []
        executed_any = False
        is_data = False

        clean_text = re.sub(r' {2,}', ' ', self.SKILL_PATTERN.sub("", response_text)).strip()

        # ── Execution Strategy ──
        # If 'count' is in the requested skills, we force strict sequential execution
        # to respect the blocking timer nature of count. Otherwise, we run safe skills
        # (like open_app) in parallel for speed.
        has_count = any(m.group(1).lower() == "count" for m in matches)
        
        PARALLEL_SKILLS = {"open_app", "web_open"}
        
        async def _run_single_match(match):
            skill_name = match.group(1).lower()
            params_str  = match.group(2) or ""
            params = self._parse_parameters(skill_name, params_str)

            # 🚨 MULTI-DEVICE SECURITY GATE
            from agent_core import get_active_device
            if get_active_device() == "phone":
                # User requested all skills except Orchestrator/Research
                blocked_mobile_skills = {"orchestrator", "deep_research"}
                if skill_name in blocked_mobile_skills:
                    logger.warning(f"Blocked skill execution on phone: {skill_name}")
                    return {"success": False, "result": "Deep research and orchestrator tasks are restricted on mobile.", "tts_text": "I cannot do deep research from the phone."}

            if skill_name not in self.skills_registry:
                logger.warning(f"Unknown skill: {skill_name}")
                try:
                    from modules.skill_forge import get_skill_forge
                    get_skill_forge(self.config).record_unknown_skill(skill_name, user_request)
                except Exception as e:
                    logger.error(f"Failed to record unknown skill: {e}")
                return None, skill_name, False

            try:
                logger.info(f"⚙️  Executing {skill_name}({params})")
                func = self.skills_registry[skill_name]
                if asyncio.iscoroutinefunction(func):
                    result = await func(*params)
                else:
                    result = await asyncio.to_thread(func, *params)
                return str(result) if result else "", skill_name, skill_name in DATA_SKILLS
            except Exception as e:
                import traceback
                logger.error(f"Skill '{skill_name}' failed: {e}\n{traceback.format_exc()}")
                return f"Error executing {skill_name}: {e}", skill_name, False

        def _handle_match_result(res_str, sname, is_d):
            nonlocal executed_any, is_data
            if res_str is not None:
                results.append(res_str)
                tts_results.append(_truncate_for_tts(res_str, sname))
                executed_any = True
                if is_d:
                    is_data = True

        if has_count:
            # STRICT SEQUENTIAL MODE
            from agent_core import get_active_websocket
            from modules.tts import generate_tts
            import base64
            import os
            
            for i, match in enumerate(matches):
                res_str, sname, is_d = await _run_single_match(match)
                _handle_match_result(res_str, sname, is_d)
                
                # If we just finished counting and there are more skills to execute, play a transition audio
                ws = get_active_websocket()
                if sname == "count" and i < len(matches) - 1 and ws:
                    try:
                        next_skill = matches[i+1].group(1).lower()
                        trans_text = f"Counting is done, executing {next_skill.replace('_', ' ')}"
                        trans_wav = await generate_tts(trans_text)
                        if trans_wav and os.path.exists(trans_wav):
                            with open(trans_wav, "rb") as f:
                                b64 = base64.b64encode(f.read()).decode('utf-8')
                                await ws.send_json({"event": "audio_response", "audio": b64})
                            os.remove(trans_wav)
                    except Exception as e:
                        logger.error(f"Transition TTS failed: {e}")
        else:
            # FAST PARALLEL MODE (Default)
            parallel_matches = [m for m in matches if m.group(1).lower() in PARALLEL_SKILLS]
            serial_matches   = [m for m in matches if m.group(1).lower() not in PARALLEL_SKILLS]
            
            if parallel_matches:
                parallel_results = await asyncio.gather(*[_run_single_match(m) for m in parallel_matches])
                for res_str, sname, is_d in parallel_results:
                    _handle_match_result(res_str, sname, is_d)
                    
            for match in serial_matches:
                res_str, sname, is_d = await _run_single_match(match)
                _handle_match_result(res_str, sname, is_d)

        if not executed_any:
            return {"executed": False, "clean_text": clean_text, "is_data_skill": False}

        return {
            "executed": True,
            "skill_name": "multiple_skills" if len(matches) > 1 else matches[0].group(1),
            "params": [],
            "result": "\n\n".join(results),
            "tts_result": " ".join(tts_results),
            "clean_text": clean_text,
            "is_data_skill": is_data,
        }

    # ════════════════════════════════════════════
    # QUIT SKILL (Rust Dictator Mode)
    # ════════════════════════════════════════════
    
    def _skill_quit_max(self, *args) -> str:
        """Sends hibernate signal to frontend. Rust will handle the actual kill."""
        from modules.conversation_store import clear_history
        from modules.emotion_tracker import reset_emotion
        from modules.personality_engine import reset_message_count, reset_topic
        clear_history()
        reset_emotion()
        reset_message_count()
        reset_topic()
        logger.info("Sending HIBERNATE signal. Handing over kill authority to Rust Tauri.")
        return "[ACTION:HIBERNATE] I am going to sleep now. Just click my tray icon if you need me!"

    # ════════════════════════════════════════════
    # SYSTEM INFO SKILLS
    # ════════════════════════════════════════════

    # Note: time_now, date_today, sysinfo, top_processes have been extracted to backend/skills/core_skills.py

    async def _skill_media(self, action: str = "play", *args) -> str:
        # If args are provided (e.g., [SKILL:media:play:arijit singh]), route to the intelligent media engine
        if args and action == "play":
            query = " ".join(args).strip()
            if query:
                from modules.media_engine import media_engine
                return await media_engine.play_media(query)
                
        # Otherwise, fall back to OS-level media keys (play/pause/next/volume)
        from modules.media_control import media_action
        return media_action(action)

    def _skill_reminder_set(self, *args) -> str:
        from modules.reminder_agent import set_reminder
        if len(args) < 2:
            return "Usage: reminder_set:text:YYYY-MM-DD:HH:MM"
        text     = args[0]
        date_str = args[1]
        time_str = args[2] if len(args) > 2 else "09:00"
        return set_reminder(self.config, text, date_str, time_str)

    def _skill_reminder_list(self, *args) -> str:
        from modules.reminder_agent import list_reminders
        return list_reminders(self.config)

    def _skill_reminder_clear(self, *args) -> str:
        from modules.reminder_agent import clear_reminders
        return clear_reminders(self.config)

    def _skill_write_code(self, *args):       
        return self.code_engine.write_code(*args)

    def _skill_run_code(self, *args):         
        return self.code_engine.run_code(*args)

    def _skill_code_review(self, *args):      
        return self.code_engine.code_review(*args)

    def _skill_fix_code(self, *args):         
        return self.code_engine.fix_code(*args)

    def _skill_project_scaffold(self, *args): 
        return self.code_engine.project_scaffold(*args)

    async def _skill_find_and_explain(self, *args): 
        return await asyncio.to_thread(self.file_manager.find_and_explain, *args)

    def _skill_list_files(self, *args):       
        folder = " ".join(args).strip() if args else "downloads"
        return self.file_manager.list_files(folder)

    def _skill_read_file(self, *args):        
        return self.file_manager.read_file(*args)

    def _skill_edit_file(self, *args):        
        return self.file_manager.edit_file(*args)

    async def _skill_search_files(self, *args):     
        return await asyncio.to_thread(self.file_manager.search_files, *args)

    def _skill_count(self, start: str, end: str, reverse: str = "False") -> str:
        try:
            start_num = int(start.strip())
            end_num = int(end.strip())
            is_reverse = str(reverse).strip().lower() in ['true', 'yes', '1']
            
            if is_reverse or start_num > end_num:
                step = -1
                if start_num < end_num:
                    start_num, end_num = end_num, start_num
            else:
                step = 1
                
            if abs(end_num - start_num) > 1000:
                return "That's too many numbers for me to count."
                
            numbers = [str(i) for i in range(start_num, end_num + step, step)]
            return ", ".join(numbers)
        except ValueError:
            return "Please provide valid numbers to count."

    def _skill_weather(self, city: str = "auto") -> str:
        try:
            import httpx
            url = f"https://wttr.in/{city.strip() or 'auto'}?format=3&lang=en"
            with httpx.Client(timeout=7.0) as c:
                r = c.get(url, headers={"User-Agent": "curl/7.68.0"})
                return r.text.strip() if r.status_code == 200 else f"Weather unavailable for {city}."
        except Exception:
           return "Could not reach weather server."
    
    
    def _skill_schedule_action(self, date_str: str, time_str: str, skill_name: str, *params) -> str:
        execute_at = f"{date_str.strip()} {time_str.strip()}"
        return self.scheduler.add_task(execute_at, skill_name, list(params))


    def _skill_deep_research(self, topic: str = "", ai_platform: str = "gemini") -> str:
        """
        Executes a deep autonomous research on a given topic.
        Now routes through the new Dynamic Master Orchestrator instead of the old broken agent.
        Usage from intent: [SKILL:deep_research:Black Holes:gemini]
        """
        if not topic:
            return "Please provide a topic for research."

        topic = topic.strip()

        try:
            from modules.orchestrator import start_orchestrator_background

            logger = logging.getLogger("MAX.SKILLS.DEEP_RESEARCH")
            logger.info(f"Routing deep research to Master Orchestrator for topic: '{topic}'")

            # Start the orchestrator in background — it handles everything dynamically
            task_id = start_orchestrator_background(topic)

            return f"Deep research on '{topic}' has been started in the background using the Master Orchestrator. Task ID: {task_id}. You can ask for status anytime."

        except Exception as e:
            logger.error(f"Deep Research Skill Failed: {e}")
            return f"Sorry sir, I encountered an error while starting research on {topic}. Please check the system logs."
    
    
    def _skill_web_search(self, *args) -> str:
        import httpx
        query = " ".join(args).strip()
        if not query:
            return "What should I search for?"
        try:
            encoded = quote_plus(query)
            rss = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
            with httpx.Client(timeout=7.0) as c:
                resp = c.get(rss, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    root  = ET.fromstring(resp.content)
                    items = root.findall('.//item')[:4]
                    headlines = []
                    for item in items:
                        t = item.find('title')
                        if t is not None and t.text:
                            title = t.text.strip()
                            if " - " in title:
                                title = title.rsplit(" - ", 1)[0]
                            headlines.append(title)
                    if headlines:
                        return ". ".join(headlines[:3])
        except Exception as e:
            logger.warning(f"News RSS failed: {e}")
        try:
            params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
            with httpx.Client(timeout=5.0) as c:
                data = c.get("https://api.duckduckgo.com/", params=params).json()
                abstract = data.get("AbstractText", "").strip()
                if abstract:
                    return abstract[:300]
        except Exception:
            pass
        open_url_in_browser(f"https://duckduckgo.com/?q={quote_plus(query)}")
        return f"Opened browser search for '{query}'."

    def _skill_youtube_search(self, *args) -> str:
        query = " ".join(args).strip()
        open_url_in_browser(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
        return "YouTube search opened."

    def _skill_clear_memory(self) -> str:
        from modules.conversation_store import clear_history
        from modules.emotion_tracker import reset_emotion
        from modules.personality_engine import reset_message_count, reset_topic
        clear_history()
        reset_emotion()
        reset_message_count()
        reset_topic()
        return "Memory cleared."

    def _skill_add_rule(self, *args) -> str:
        import json
        text = " ".join(args).strip()
        if not text:
            return "Rule text is missing."
        path = Path(self.config.DATA_DIR) / "permanent_rules.json"
        rules = json.loads(path.read_text()) if path.exists() else []
        rules.append({"rule": text, "added_at": datetime.now().isoformat()})
        path.write_text(json.dumps(rules, indent=2))
        return "Rule saved."

    def _skill_timer(self, seconds: str = "60", label: str = "Timer") -> str:
        try:
            secs = int(seconds)
            if secs <= 0:
                return "Timer needs a positive duration."

            def _countdown():
                time.sleep(secs)
                msg = f"MAX: {label} done! ({secs}s)"
                try:
                    # pyrefly: ignore [missing-import]
                    from plyer import notification
                    notification.notify(title="MAX Timer", message=msg, timeout=8)
                    return
                except ImportError:
                    pass
                if platform.system() == "Windows":
                    subprocess.run([
                        "powershell", "-Command",
                        f"Add-Type -AssemblyName System.Windows.Forms; "
                        f"[System.Windows.Forms.MessageBox]::Show('{msg}','MAX')"
                    ], capture_output=True)

            threading.Thread(target=_countdown, daemon=True).start()
            mins, rem = divmod(secs, 60)
            return f"Timer set: {f'{mins}m {rem}s' if mins else f'{secs}s'}."
        except ValueError:
            return "Provide duration in seconds."

    def _skill_alarm(self, time_str: str = "", label: str = "Alarm", *args) -> str:
        """Set an alarm for a specific time (e.g., 7:00 AM, 14:30)."""
        if not time_str:
            return "What time should I set the alarm for?"
        try:
            from datetime import timedelta
            now = datetime.now()
            time_str_clean = time_str.strip().upper().replace('.', ':')
            # Parse time formats: "7:00 AM", "7AM", "14:30", "7:00"
            parsed_time = None
            for fmt in ["%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M", "%H"]:
                try:
                    parsed_time = datetime.strptime(time_str_clean, fmt).time()
                    break
                except ValueError:
                    continue
            if parsed_time is None:
                return f"Could not understand time format: {time_str}. Try '7:00 AM' or '14:30'."
            target = now.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)  # Set for tomorrow if time already passed
            delta_secs = int((target - now).total_seconds())
            full_label = (label + " " + " ".join(args)).strip() if args else label

            def _alarm_ring():
                time.sleep(delta_secs)
                msg = f"MAX: {full_label}! It's {target.strftime('%I:%M %p')}."
                try:
                    from plyer import notification
                    notification.notify(title="MAX Alarm", message=msg, timeout=10)
                    return
                except ImportError:
                    pass
                if platform.system() == "Windows":
                    subprocess.run([
                        "powershell", "-Command",
                        f"Add-Type -AssemblyName System.Windows.Forms; "
                        f"[System.Windows.Forms.MessageBox]::Show('{msg}','MAX Alarm')"
                    ], capture_output=True)

            threading.Thread(target=_alarm_ring, daemon=True).start()
            return f"Alarm set for {target.strftime('%I:%M %p')} ({delta_secs // 60} minutes from now)."
        except Exception as e:
            return f"Alarm failed: {e}"

    def _skill_note(self, *args) -> str:
        try:
            text = " ".join(args).strip()
            if not text:
                return "Note content is empty."
            notes_file = Path(self.config.DATA_DIR) / "notes.txt"
            notes_file.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(notes_file, 'a', encoding='utf-8') as f:
                f.write(f"[{ts}] {text}\n")
            return "Note saved."
        except Exception as e:
            return f"Note save failed: {e}"

    def _skill_note_delete(self, *args) -> str:
        """Delete the last note from notes.txt."""
        try:
            notes_file = Path(self.config.DATA_DIR) / "notes.txt"
            if not notes_file.exists():
                return "No notes file found."
            lines = notes_file.read_text(encoding='utf-8').strip().split('\n')
            lines = [l for l in lines if l.strip()]
            if not lines:
                return "Notes are already empty."
            removed = lines.pop()
            notes_file.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
            return f"Last note deleted: {removed[:80]}"
        except Exception as e:
            return f"Note delete failed: {e}"

    def _skill_note_clear(self, *args) -> str:
        """Clear all notes."""
        try:
            notes_file = Path(self.config.DATA_DIR) / "notes.txt"
            if not notes_file.exists():
                return "No notes file found."
            notes_file.write_text('', encoding='utf-8')
            return "All notes cleared."
        except Exception as e:
            return f"Note clear failed: {e}"

    async def _skill_read_screen(self, *args) -> str:
        target = " ".join(args).strip()
        try:
            from PIL import Image, ImageGrab
            ss_dir = Path(self.config.DATA_DIR) / "screenshots"
            ss_dir.mkdir(parents=True, exist_ok=True)
            path = ss_dir / "vision_debug.jpg"
            bbox = None
            generic_targets = ["all", "screen", "window", "display", "monitor", "current"]
            if target and target.lower() not in generic_targets:
                try:
                    import pygetwindow as gw
                    wins = gw.getWindowsWithTitle(target)
                    if wins:
                        w = wins[0]
                        try: 
                            w.activate()
                            time.sleep(0.7)
                        except Exception: 
                            pass
                        bbox = (w.left, w.top, w.left + w.width, w.top + w.height)
                except ImportError:
                    pass
            
            def _capture():
                img = None
                if bbox:
                    try:
                        img = ImageGrab.grab(bbox=bbox, all_screens=True).convert('RGB')
                    except Exception:
                        try:
                            img = ImageGrab.grab(bbox=bbox).convert('RGB')
                        except Exception:
                            pass
                if not img:
                    try:
                        img = ImageGrab.grab(all_screens=True).convert('RGB')
                    except Exception:
                        try:
                            img = ImageGrab.grab().convert('RGB')
                        except Exception:
                            pass
                if not img and os.name == 'nt':
                    try:
                        import ctypes
                        import ctypes.wintypes
                        user32 = ctypes.windll.user32
                        gdi32 = ctypes.windll.gdi32
                        try:
                            user32.SetProcessDPIAware()
                        except Exception:
                            pass
                        width = user32.GetSystemMetrics(0)
                        height = user32.GetSystemMetrics(1)
                        hdesktop = user32.GetDesktopWindow()
                        desktop_dc = user32.GetDC(hdesktop)
                        img_dc = gdi32.CreateCompatibleDC(desktop_dc)
                        mem_img = gdi32.CreateCompatibleBitmap(desktop_dc, width, height)
                        gdi32.SelectObject(img_dc, mem_img)
                        gdi32.BitBlt(img_dc, 0, 0, width, height, desktop_dc, 0, 0, 0x00CC0020)

                        class BITMAPINFOHEADER(ctypes.Structure):
                            _fields_ = [
                                ('biSize', ctypes.wintypes.DWORD),
                                ('biWidth', ctypes.wintypes.LONG),
                                ('biHeight', ctypes.wintypes.LONG),
                                ('biPlanes', ctypes.wintypes.WORD),
                                ('biBitCount', ctypes.wintypes.WORD),
                                ('biCompression', ctypes.wintypes.DWORD),
                                ('biSizeImage', ctypes.wintypes.DWORD),
                                ('biXPelsPerMeter', ctypes.wintypes.LONG),
                                ('biYPelsPerMeter', ctypes.wintypes.LONG),
                                ('biClrUsed', ctypes.wintypes.DWORD),
                                ('biClrImportant', ctypes.wintypes.DWORD)
                            ]

                        bmi = BITMAPINFOHEADER()
                        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                        bmi.biWidth = width
                        bmi.biHeight = -height
                        bmi.biPlanes = 1
                        bmi.biBitCount = 32
                        bmi.biCompression = 0
                        buffer = ctypes.create_string_buffer(width * height * 4)
                        gdi32.GetDIBits(img_dc, mem_img, 0, height, buffer, ctypes.byref(bmi), 0)
                        gdi32.DeleteObject(mem_img)
                        gdi32.DeleteDC(img_dc)
                        user32.ReleaseDC(hdesktop, desktop_dc)
                        img = Image.frombytes('RGBA', (width, height), buffer.raw, 'raw', 'BGRA').convert('RGB')
                    except Exception as win_err:
                        logger.warning(f"Win32 GDI capture fallback failed: {win_err}")

                if not img and PYAUTOGUI_AVAILABLE:
                    try:
                        img = pyautogui.screenshot().convert('RGB')
                    except Exception:
                        pass
                if not img:
                    raise RuntimeError("Could not capture desktop screen. Ensure display permissions are granted.")

                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                img.save(str(path), quality=70, optimize=True)

            await asyncio.to_thread(_capture)
            
            vision_prompt = target.strip() if target else ""
            generic_targets = ["all", "screen", "window", "display", "monitor", "current"]
            if not vision_prompt or vision_prompt.lower() in generic_targets:
                if hasattr(self, 'last_user_request') and self.last_user_request:
                    vision_prompt = self.last_user_request
                else:
                    vision_prompt = "Describe what is visible on the screen in detail. Read all text, code, or visual elements."

            from modules.llm import analyze_image_with_prompt
            return await analyze_image_with_prompt(str(path), vision_prompt)
        except ImportError:
            return "Pillow needed: pip install pillow"
        except Exception as e:
            import traceback
            logger.error(f"Screenshot Error: {e}\n{traceback.format_exc()}")
            return f"Screen read failed: {e}"

    def _skill_list_windows(self, *args) -> str:
        try:
            titles = []
            try:
                import pygetwindow as gw
                titles = [t for t in gw.getAllTitles() if t.strip() and t.strip() not in ['Program Manager', 'Settings']]
            except Exception:
                pass

            if titles:
                return f"Open windows ({len(titles)}): " + ", ".join(titles[:10])

            # Fallback to psutil for running GUI applications
            import psutil
            apps = set()
            for proc in psutil.process_iter(['name']):
                try:
                    name = proc.info['name']
                    if name and name.lower().endswith('.exe'):
                        base = name[:-4].lower()
                        if base in ['chrome', 'msedge', 'firefox', 'code', 'spotify', 'notepad', 'explorer', 'discord', 'slack', 'whatsapp']:
                            apps.add(base.capitalize())
                except Exception:
                    pass

            if apps:
                return f"Active applications running: " + ", ".join(sorted(apps))
            return "No active windows found."
        except Exception as e:
            return f"Window listing failed: {e}"

    async def _skill_screenshot(self, filename: str = "", location: str = "default", **kw) -> str:
        try:
            from PIL import ImageGrab
            
            # Resolve destination
            loc_clean = location.strip().lower()
            if loc_clean in ["", "default"]:
                sd = Path(self.config.DATA_DIR) / "screenshots"
            else:
                sd = self._resolve_user_path(loc_clean)
                if not sd.is_absolute():
                    sd = Path(self.config.DATA_DIR) / "screenshots"
            sd.mkdir(parents=True, exist_ok=True)
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = filename.strip() or 'max_screenshot'
            if not fname.lower().endswith(".png"):
                fname += f"_{ts}.png"
                
            # If screenshot request is for a folder (like downloads), bring Explorer to foreground first
            combined_req = (filename + " " + location + " " + getattr(self, 'last_user_request', '')).lower()
            if any(k in combined_req for k in ["download", "explorer", "folder", "desktop", "document"]):
                focus_target_window("downloads")
                await asyncio.sleep(1.0)

            fp = sd / fname
            def _do_grab():
                # Primary 1: mss (Fastest, zero display-lock dependency)
                try:
                    import mss
                    with mss.mss() as sct:
                        sct.shot(output=str(fp))
                        if os.path.exists(str(fp)) and os.path.getsize(str(fp)) > 0:
                            return
                except Exception as mss_err:
                    logger.warning(f"mss capture failed: {mss_err}")

                # Primary 2: PIL ImageGrab
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grab()
                    img.save(str(fp))
                    if os.path.exists(str(fp)) and os.path.getsize(str(fp)) > 0:
                        return
                except Exception:
                    pass

                # Primary 3: PyAutoGUI
                try:
                    pyautogui.screenshot(str(fp))
                    if os.path.exists(str(fp)) and os.path.getsize(str(fp)) > 0:
                        return
                except Exception:
                    pass

                # Primary 4: PowerShell .NET System.Drawing (Bulletproof Windows Fallback)
                try:
                    ps_cmd = (
                        f"[Reflection.Assembly]::LoadWithPartialName('System.Drawing') | Out-Null; "
                        f"[Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
                        f"$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                        f"$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height; "
                        f"$graphics = [System.Drawing.Graphics]::FromImage($bmp); "
                        f"$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); "
                        f"$bmp.Save('{str(fp)}', [System.Drawing.Imaging.ImageFormat]::Png); "
                        f"$graphics.Dispose(); $bmp.Dispose();"
                    )
                    subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
                except Exception as ps_err:
                    logger.error(f"PowerShell screenshot capture failed: {ps_err}")

            await asyncio.to_thread(_do_grab)
            return f"Screenshot saved at {fp}"
        except Exception as e:
            return f"Screenshot failed: {e}"

    def _skill_screen_record(self, *args) -> str:
        # Method 1: Win32 ctypes keybd_event (Low-level, highly reliable on Windows)
        try:
            import ctypes
            import time
            VK_LWIN = 0x5B
            VK_MENU = 0x12  # Alt
            VK_R = 0x52
            KEYEVENTF_KEYUP = 0x0002
            
            # Press keys
            ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MENU, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_R, 0, 0, 0)
            
            time.sleep(0.1)  # Brief pause to let Windows register the hotkey
            
            # Release keys
            ctypes.windll.user32.keybd_event(VK_R, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
            return "Screen recording toggled ."
        except Exception as e_ctypes:
            logger.warning(f"ctypes screen record hotkey failed: {e_ctypes}")

        # Method 2: Global keyboard library
        try:
            import keyboard
            keyboard.send('win+alt+r')
            return "Screen recording toggled ."
        except Exception as e_kb:
            logger.warning(f"keyboard library hotkey failed: {e_kb}")

        # Method 3: PyAutoGUI fallback
        if PYAUTOGUI_AVAILABLE:
            try:
                pyautogui.hotkey('win', 'alt', 'r')
                return "Screen recording toggled ."
            except Exception as e_py:
                return f"All hotkey methods failed. Last error: {e_py}"

        return "Failed to press hotkey. Dependencies or Windows APIs are unavailable."

    def _extract_urls(self, text: str) -> list[str]:
        # Match standard URLs starting with http:// or https:// or domain names
        pattern = r'(https?://[^\s()<>]+|(?:www\.)?(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|edu|gov|io|co|in|dev|ai|me|info|tv|xyz)(?:/[^\s()<>]*)*)'
        candidates = re.findall(pattern, text)
        urls = []
        for c in candidates:
            c_clean = c.strip(".,?!;:()[]{}'")
            # Avoid extensions that look like files if it doesn't have a path slash or www./http(s)://
            if c_clean.endswith(('.py', '.js', '.ts', '.css', '.html', '.json', '.md', '.txt', '.png', '.jpg', '.jpeg', '.gif', '.pdf')):
                if "/" not in c_clean and not any(c_clean.startswith(prefix) for prefix in ("www.", "http://", "https://")):
                    continue
            if not c_clean.startswith(("http://", "https://")):
                urls.append("https://" + c_clean)
            else:
                urls.append(c_clean)
        return urls

    async def _skill_open_link(self, source: str = "clipboard", *args) -> str:
        source_lower = source.strip().lower()
        
        urls = []
        if source_lower in ("clipboard", "copied", "copied link", "clipboard link"):
            try:
                import pyperclip
                content = pyperclip.paste()
                urls = self._extract_urls(content)
                if not urls:
                    return "No URLs found in the clipboard."
            except Exception as e:
                return f"Failed to read clipboard: {e}"
                
        elif source_lower == "screen":
            try:
                from PIL import Image, ImageGrab
                ss_dir = Path(self.config.DATA_DIR) / "screenshots"
                ss_dir.mkdir(parents=True, exist_ok=True)
                path = ss_dir / "vision_link_debug.jpg"
                
                def _capture_link_screenshot():
                    img = ImageGrab.grab(all_screens=True).convert('RGB')
                    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    img.save(str(path), quality=70, optimize=True)

                await asyncio.to_thread(_capture_link_screenshot)
                
                from modules.llm import analyze_image_with_prompt
                screen_text = await analyze_image_with_prompt(
                    str(path),
                    "Find and list all website URLs and links visible on this screen. List only the URLs space-separated, or say 'None'."
                )
                urls = self._extract_urls(screen_text)
                if not urls:
                    return "No links could be identified on the screen."
            except Exception as e:
                return f"Failed to read screen: {e}"
                
        elif source_lower.startswith("file:"):
            filepath_str = source[5:].strip()
            if not filepath_str and args:
                filepath_str = args[0]
            if not filepath_str:
                return "Please specify a file path."
                
            try:
                filepath = Path(filepath_str).expanduser().resolve()
                if not filepath.is_absolute():
                    search_dirs = getattr(self.config, 'SEARCH_DIRS', [Path.home() / "Desktop"])
                    filepath = search_dirs[0] / filepath_str
                    
                if not filepath.exists():
                    return f"File not found: {filepath_str}"
                    
                content = filepath.read_text(encoding='utf-8', errors='replace')
                urls = self._extract_urls(content)
                if not urls:
                    return f"No URLs found in file '{filepath_str}'."
            except Exception as e:
                return f"Failed to read file: {e}"
                
        else:
            filepath_str = source.strip()
            try:
                filepath = Path(filepath_str).expanduser().resolve()
                if not filepath.is_absolute():
                    search_dirs = getattr(self.config, 'SEARCH_DIRS', [Path.home() / "Desktop"])
                    filepath = search_dirs[0] / filepath_str
                if filepath.exists():
                    content = filepath.read_text(encoding='utf-8', errors='replace')
                    urls = self._extract_urls(content)
                    if not urls:
                        return f"No URLs found in file '{filepath_str}'."
                else:
                    return f"Unknown source or file not found: {source}"
            except Exception as e:
                return f"Failed to process source '{source}': {e}"

        if not urls:
            return "No links could be identified."

        if len(urls) == 1:
            url = urls[0]
            try:
                open_url_in_browser(url)
                return f"Opened link: {url}"
            except Exception as e:
                return f"Failed to open link {url}: {e}"
        else:
            self._pending_links = urls
            numbered_list = ", ".join(f"{i}. {url}" for i, url in enumerate(urls, 1))
            return f"Found {len(urls)} links: {numbered_list}. Which one should I open?"

    def _skill_open_link_select(self, number: str = "", *args) -> str:
        if not hasattr(self, "_pending_links") or not self._pending_links:
            return "No pending links to select from."
        
        num_str = number.strip()
        if not num_str and args:
            num_str = args[0].strip()
            
        word_to_num = {
            "first": 1, "one": 1, "1st": 1, "1": 1,
            "second": 2, "two": 2, "2nd": 2, "2": 2,
            "third": 3, "three": 3, "3rd": 3, "3": 3,
            "fourth": 4, "four": 4, "4th": 4, "4": 4,
            "fifth": 5, "five": 5, "5th": 5, "5": 5,
        }
        
        idx = word_to_num.get(num_str.lower())
        if idx is None:
            digits = re.findall(r'\d+', num_str)
            if digits:
                idx = int(digits[0])
            else:
                return f"Please specify a valid link number. I have {len(self._pending_links)} pending links."
                
        if idx < 1 or idx > len(self._pending_links):
            return f"Invalid choice '{idx}'. Please select a number between 1 and {len(self._pending_links)}."
            
        selected_url = self._pending_links[idx - 1]
        try:
            open_url_in_browser(selected_url)
            return f"Opened link {idx}: {selected_url}"
        except Exception as e:
            return f"Failed to open link {selected_url}: {e}"

    # ════════════════════════════════════════════
    # MULTI-APP OPENER
    # ════════════════════════════════════════════

    _WEB_DIRECT: Dict[str, str] = {
        "google": "https://www.google.com",
        "google.com": "https://www.google.com",
        "youtube": "https://www.youtube.com",
        "youtube.com": "https://www.youtube.com",
        "chatgpt": "https://chatgpt.com",
        "gemini": "https://gemini.google.com",
        "github": "https://github.com",
        "instagram": "https://www.instagram.com",
        "facebook": "https://www.facebook.com",
        "twitter": "https://x.com",
        "x": "https://x.com",
        "linkedin": "https://www.linkedin.com",
        "reddit": "https://www.reddit.com",
        "gmail": "https://mail.google.com",
        "maps": "https://maps.google.com",
        "drive": "https://drive.google.com",
        "whatsapp web": "https://web.whatsapp.com",
    }

    _WIN_PROTOCOLS: Dict[str, str] = {
        "whatsapp": "whatsapp:", "spotify": "spotify:", "discord": "discord:",
        "teams": "msteams:", "ms-teams": "msteams:", "microsoft teams": "msteams:",
        "slack": "slack:", "zoom": "zoommtg://", "telegram": "tg:", "skype": "skype:",
    }
    _WIN_DIRECT: Dict[str, str] = {
        "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
        "paint": "mspaint.exe", "cmd": "cmd.exe", "command prompt": "cmd.exe",
        "terminal": "wt.exe", "windows terminal": "wt.exe", "powershell": "powershell.exe",
        "explorer": "explorer.exe", "file explorer": "explorer.exe",
        "task manager": "taskmgr.exe", "taskmgr": "taskmgr.exe",
        "chrome": "start chrome", "google chrome": "start chrome", "firefox": "start firefox",
        "edge": "start msedge", "brave": "start brave", "opera": "start opera",
        "browser": "start chrome", "default browser": "start chrome",
        "my browser": "start chrome", "web browser": "start chrome",
        "vscode": "code", "vs code": "code", "visual studio code": "code",
        "word": "start winword", "excel": "start excel", "powerpoint": "start powerpnt",
        "outlook": "start outlook", "vlc": "start vlc", "obs": "start obs64",
        "pycharm": "pycharm64", "postman": "start postman", "figma": "start figma",
        "settings": "start ms-settings:", "control panel": "control.exe",
        "snipping tool": "snippingtool.exe", "screen recorder": "start ms-screenclip:",
    }

    # Words that should NEVER be treated as web domains in the fallback resolver
    _NON_WEB_WORDS = {
        "browser", "app", "application", "settings", "system", "desktop",
        "screen", "window", "folder", "file", "document", "music",
        "video", "photo", "camera", "store", "help", "search",
        "terminal", "console", "editor", "player", "recorder",
        "manager", "monitor", "control", "panel", "tool",
        "default browser", "my browser", "web browser",
    }

    async def _skill_open_app(self, *args, **kw) -> str:
        if not args:
            return "Which app should I open?"

        # Split by comma OR "and"/"aur" — both are valid list separators in voice commands
        raw_joined = " ".join(args)
        apps_to_open = re.split(r",\s*|\s+(?:and|aur)\s+", raw_joined)
        system = platform.system()
        web_map = getattr(self.config, 'WEB_FALLBACK_MAP', {})
        mac_map = getattr(self.config, 'MAC_APP_MAP', {})

        results = []
        for app in apps_to_open:
            app_raw = app.strip()
            if not app_raw: continue
            
            # Clean trailing filler words ("for me", "please", "karo", "kholo", "do", "de", "na", "pls")
            app_clean = re.sub(r'\b(for me|for us|please|pls|now|karo|kholo|khol|do|de|na|bhai)\b', '', app_raw, flags=re.IGNORECASE).strip()
            app_name = app_clean if app_clean else app_raw
            app_lower = app_name.lower()
            success = False

            # 0. Check if this is a known direct website (google, youtube, chatgpt, etc.)
            direct_url = self._WEB_DIRECT.get(app_lower)
            if direct_url:
                open_url_in_browser(direct_url)
                results.append(f"{app_name.capitalize()} opened in browser.")
                success = True
                continue

            # 0b. Check if this is a known folder path (downloads, desktop, documents, pictures, etc.)
            folder_aliases = ["downloads", "desktop", "documents", "pictures", "workspace"]
            clean_folder_name = app_lower.replace("my ", "").replace(" folder", "").replace("directory", "").strip()
            if clean_folder_name in folder_aliases:
                from modules.file_manager import get_file_manager
                fm = get_file_manager()
                target_path = fm.resolve_folder_alias(clean_folder_name)
                if target_path and target_path.exists():
                    if platform.system() == "Windows":
                        os.startfile(str(target_path.resolve()))
                        await asyncio.sleep(0.5)
                        focus_target_window(clean_folder_name)
                        await asyncio.sleep(1.0)
                    else:
                        os.system(f'explorer "{target_path}"')
                    results.append(f"Opened '{clean_folder_name.capitalize()}' folder in File Explorer.")
                    success = True
                    continue

            # 0c. Context-aware Explorer folder resolution (if LLM output open_app:explorer for "open downloads folder")
            if app_lower in ["explorer", "file explorer", "windows explorer", "my explorer"]:
                last_req = getattr(self, 'last_user_request', '').lower()
                target_f = None
                for f_alias in ["downloads", "desktop", "documents", "pictures", "workspace"]:
                    if f_alias in last_req:
                        target_f = f_alias
                        break
                if target_f:
                    from modules.file_manager import get_file_manager
                    fm = get_file_manager()
                    target_path = fm.resolve_folder_alias(target_f)
                    if target_path and target_path.exists():
                        if platform.system() == "Windows":
                            os.startfile(str(target_path.resolve()))
                            await asyncio.sleep(0.5)
                            focus_target_window(target_f)
                            await asyncio.sleep(1.0)
                        else:
                            os.system(f'explorer "{target_path}"')
                        results.append(f"Opened '{target_f.capitalize()}' folder in File Explorer.")
                        success = True
                        continue

            # Check if this app request is actually an explicit web URL/domain
            is_web = (
                app_lower.startswith(("http://", "https://", "www.")) or
                ("." in app_lower and " " not in app_lower)
            )

            if is_web:
                # Bypass local launching and use 3-layer system to resolve/open URL
                try:
                    from modules.web_autopilot import WebAutopilotEngine
                    autopilot = WebAutopilotEngine(self.config)
                    verified_url = await asyncio.to_thread(autopilot.resolve_accurate_url_sync, app_name)
                    if verified_url:
                        open_url_in_browser(verified_url)
                        clean_name = verified_url.replace("https://", "").replace("http://", "").replace("www.", "")
                        clean_name = clean_name.split("/")[0].split(".")[0].capitalize()
                        results.append(f"{app_name} opened in browser.")
                        success = True
                except Exception as url_err:
                    logger.warning(f"Failed to resolve {app_name} via 3-layer system: {url_err}")

            if not success and not is_web:
                if system == "Windows":
                    # 1. Try protocol handlers (whatsapp:, spotify:, etc.)
                    proto = self._WIN_PROTOCOLS.get(app_lower)
                    if proto:
                        try:
                            os.startfile(proto)
                            results.append(f"{app_name} opened.")
                            success = True
                        except Exception as e:
                            logger.warning(f"Protocol launch failed for {app_name}: {e}")

                    # 2. Try direct executables
                    if not success:
                        exe = self._WIN_DIRECT.get(app_lower)
                        if exe:
                            try:
                                proc = subprocess.Popen(exe, shell=True)
                                # Give it a moment to fail
                                import time
                                time.sleep(0.3)
                                if proc.poll() is None or proc.returncode == 0:
                                    results.append(f"{app_name} opened.")
                                    success = True
                                else:
                                    logger.warning(f"Direct exe launch failed for {app_name}: exit code {proc.returncode}")
                            except Exception as e:
                                logger.warning(f"Direct exe launch error for {app_name}: {e}")

                    # 3. Try app indexer (fuzzy match installed apps)
                    if not success:
                        try:
                            match = self.app_indexer.find_app(app_lower)
                            if match:
                                matched_name, app_path = match
                                os.startfile(app_path)
                                results.append(f"{matched_name.title() or app_name} opened.")
                                success = True
                        except Exception as e:
                            logger.warning(f"App indexer launch failed for {app_name}: {e}")

                    # 4. Last resort: try raw command
                    if not success:
                        try:
                            proc = subprocess.Popen(app_lower, shell=True)
                            import time
                            time.sleep(0.3)
                            if proc.poll() is None or proc.returncode == 0:
                                results.append(f"{app_name} opened.")
                                success = True
                            else:
                                logger.warning(f"Raw command launch failed for {app_name}: exit code {proc.returncode}")
                        except Exception as e:
                            logger.warning(f"Raw command launch error for {app_name}: {e}")

                elif system == "Darwin":
                    try:
                        subprocess.run(["open", "-a", mac_map.get(app_lower, app_name)], check=True)
                        results.append(f"{app_name} opened.")
                        success = True
                    except Exception as e:
                        logger.warning(f"macOS launch failed for {app_name}: {e}")

                else:
                    try:
                        subprocess.Popen([app_lower])
                        results.append(f"{app_name} opened.")
                        success = True
                    except Exception as e:
                        logger.warning(f"Linux launch failed for {app_name}: {e}")

            if not success:
                # Don't try web fallback for generic/ambiguous words
                if app_lower not in self._NON_WEB_WORDS:
                    # If local search failed, try 3-layer system as ultimate fallback
                    try:
                        from modules.web_autopilot import WebAutopilotEngine
                        autopilot = WebAutopilotEngine(self.config)
                        verified_url = await asyncio.to_thread(autopilot.resolve_accurate_url_sync, app_name)
                        if verified_url:
                            open_url_in_browser(verified_url)
                            clean_name = verified_url.replace("https://", "").replace("http://", "").replace("www.", "")
                            clean_name = clean_name.split("/")[0].split(".")[0].capitalize()
                            results.append(f"{app_name} not found locally. Opened in browser.")
                            success = True
                    except Exception as url_err:
                        logger.warning(f"Fallback resolve for {app_name} failed: {url_err}")

                if not success:
                    results.append(f"Could not find '{app_name}'.")
                    logger.error(f"All launch methods failed for: {app_name}")

        return "\n".join(results)


    def _skill_list_apps(self, *args) -> str:
        query = " ".join(args).strip()
        try:
            apps = self.app_indexer.list_apps(query, limit=30)
            if not apps:
                return f"No apps found for '{query}'. Try rebuilding the index."
            label = f"Apps matching '{query}':" if query else f"Installed apps ({len(apps)} shown):"
            return label + "\n" + "\n".join(f"  • {a}" for a in apps)
        except Exception as e:
            return f"App list failed: {e}"

    def _skill_rebuild_app_index(self, *args) -> str:
        try:
            count = self.app_indexer.build_index()
            return f"App index rebuilt. {count} apps indexed."
        except Exception as e:
            return f"Rebuild failed: {e}"

    # ════════════════════════════════════════════
    # MULTI-TAB WEB OPENER (CLEAN TTS URLS)
    # ════════════════════════════════════════════

    # ── MUTLI-TAB WEB OPENER (UPGRADED WITH 3-LAYER ACCURACY ENGINE) ──────────

    # ── MULTI-TAB WEB OPENER (ASYNC EVENT LOOP FIX) ──────────────────────────

    # ── MULTI-TAB WEB OPENER (BULLETPROOF SYNCHRONOUS RESOLVER) ──────────────

    async def _skill_web_open(self, url: str = "", **kw) -> str:
        if not url:
            return "Provide a URL."
            
        urls_to_open = url.split(",")
        results = []
        
        import time
        from modules.web_autopilot import WebAutopilotEngine
        autopilot = WebAutopilotEngine(self.config)

        for u in urls_to_open:
            u = u.strip()
            if not u: continue
            
            try:
                # 🔥 Wrap the synchronous call in asyncio.to_thread
                verified_url = await asyncio.to_thread(autopilot.resolve_accurate_url_sync, u)
                
                clean_name = verified_url.replace("https://", "").replace("http://", "").replace("www.", "")
                clean_name = clean_name.split("/")[0].split(".")[0].capitalize()
                
                open_url_in_browser(verified_url)
                await asyncio.sleep(0.4) 
                results.append(f"{clean_name} opened")
                
            except Exception as e:
                logger.error(f"Failed to open verified route for {u}: {e}")
                fallback_url = u if u.startswith(("http://", "https://")) else f"https://{u}"
                open_url_in_browser(fallback_url)
                results.append(f"{u} opened")
                
        return ", ".join(results) + "."

    def _skill_volume_control(self, action: str = "up", value: str = "10", **kw) -> str:
        try:
            system = platform.system()
            al = action.lower()
            if system == "Windows":
                try:
                    import comtypes
                    comtypes.CoInitialize()
                    from ctypes import cast, POINTER
                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                    devices  = AudioUtilities.GetSpeakers()
                    if hasattr(devices, "EndpointVolume"):
                        vol = devices.EndpointVolume
                    else:
                        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                        vol = cast(interface, POINTER(IAudioEndpointVolume))
                    step = int(value) / 100.0
                    if al == "up":    
                        vol.SetMasterVolumeLevelScalar(min(1.0, vol.GetMasterVolumeLevelScalar() + step), None)
                    elif al == "down": 
                        vol.SetMasterVolumeLevelScalar(max(0.0, vol.GetMasterVolumeLevelScalar() - step), None)
                    elif al == "mute": 
                        vol.SetMute(not vol.GetMute(), None)
                    elif al == "set":  
                        vol.SetMasterVolumeLevelScalar(min(1.0, int(value) / 100.0), None)
                    comtypes.CoUninitialize()   
                    return f"Volume {al}."
                except ImportError as e:
                    return f"Volume control missing dependency: {e}. Try: pip install pycaw comtypes"
            elif system == "Darwin":
                if al == "mute": 
                    subprocess.run(["osascript", "-e", "set volume output muted true"])
                else: 
                    subprocess.run(["osascript", "-e", f"set volume output volume {max(0,min(100,int(value)))}"])
                return "Volume adjusted."
            else:
                subprocess.run(["amixer", "-D", "pulse", "sset", "Master", f"{value}%"])
                return f"Volume set to {value}%."
        except Exception as e:
            return f"Volume control failed: {e}"

    def _skill_brightness(self, action: str = "up", value: str = "10") -> str:
        try:
            if platform.system() == "Windows":
                import wmi
                w = wmi.WMI(namespace='wmi')
                methods = w.WmiMonitorBrightnessMethods()[0]
                current = w.WmiMonitorBrightness()[0].CurrentBrightness
                step = int(value)
                new_val = (min(100, current + step) if action.lower() == "up"
                           else max(0, current - step) if action.lower() == "down"
                           else max(0, min(100, int(value))))
                methods.WmiSetBrightness(new_val, 0)
                return f"Brightness set to {new_val}%."
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", "tell application \"System Events\" to key code 144"])
                return "Brightness adjusted."
            else:
                subprocess.run(["brightnessctl", "set", f"{value}%"])
                return f"Brightness {value}%."
        except ImportError:
            return "Brightness needs: pip install wmi pywin32"
        except Exception as e:
            return f"Brightness failed: {str(e)[:120]}"

    def _skill_clipboard(self, action: str = "get", text: str = "") -> str:
        try:
            import pyperclip
            if action.lower() == "get":
                content = pyperclip.paste()
                return f"Clipboard: {content[:200]}" if content else "Clipboard is empty."
            elif action.lower() == "set":
                if not text: 
                    return "What should I copy to clipboard?"
                pyperclip.copy(text)
                return "Copied to clipboard."
            return "Use 'get' or 'set'."
        except ImportError:
            return "Clipboard needs: pip install pyperclip"
        except Exception as e:
            return f"Clipboard error: {str(e)[:120]}"

    def _skill_lock_pc(self, *args) -> str:
        try:
            system = platform.system()
            if system == "Windows":
                subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
            elif system == "Darwin":
                subprocess.run(["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession", "-suspend"])
            else:
                subprocess.run(["gnome-screensaver-command", "-l"])
            return "PC locked."
        except Exception as e:
            return f"Lock failed: {str(e)[:120]}"
    
    async def _skill_youtube_play(self, *args) -> str:
        query = " ".join(args).strip()
        if not query:
            return "What should I play on YouTube?"
        
        from modules.media_engine import media_engine
        return await media_engine.play_media(query)
        
    def _resolve_contact_number(self, contact: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Dynamically resolves contact input (name or number) to a validated phone number.
        Returns (phone_number, error_message).
        """
        if not contact or not contact.strip():
            return None, "Please provide a contact name or phone number."

        contact_clean = contact.strip().lower()
        
        # 1. Direct phone number check
        digits_only = re.sub(r'[^\d]', '', contact_clean)
        if len(digits_only) >= 10:
            num = contact_clean.replace(" ", "").replace("-", "")
            if not num.startswith("+"):
                num = "+91" + num
            return num, None

        # 2. Dynamic user self-reference detection
        # Fetch user's dynamic name from memory user_facts if available
        user_name = ""
        user_facts = {}
        try:
            mem_file = Path(self.config.MEMORY_FILE)
            if mem_file.exists():
                mem_data = json.loads(mem_file.read_text(encoding='utf-8'))
                user_facts = mem_data.get("user_facts", {})
                user_name = user_facts.get("name", "").strip().lower()
        except Exception as e:
            logger.debug(f"Memory facts lookup notice: {e}")

        # Check self-referential terms dynamically
        self_terms = {"me", "myself", "mera", "my whatsapp", "my number", "mera number", "self"}
        if user_name:
            self_terms.add(user_name)

        is_self_target = any(term in contact_clean for term in self_terms) or (user_name and user_name in contact_clean)

        if is_self_target:
            my_num = (
                getattr(self.config, "MY_WHATSAPP_NUMBER", "").strip() or 
                os.getenv("MY_WHATSAPP_NUMBER", "").strip() or
                user_facts.get("whatsapp_number", "").strip() or
                user_facts.get("phone", "").strip()
            )
            
            if not my_num:
                contacts_file = Path(self.config.DATA_DIR) / "contacts.json"
                if contacts_file.exists():
                    try:
                        c_dict = json.loads(contacts_file.read_text(encoding='utf-8'))
                        my_num = c_dict.get("my_whatsapp") or c_dict.get("me") or c_dict.get("my_number")
                        if not my_num and user_name:
                            my_num = c_dict.get(user_name)
                    except Exception as e:
                        logger.warning(f"Error reading contacts JSON: {e}")

            if my_num:
                num = my_num.replace(" ", "").replace("-", "")
                if not num.startswith("+"):
                    num = "+91" + num
                return num, None
            else:
                return None, ("I don't have your WhatsApp number saved yet. "
                              "You can add MY_WHATSAPP_NUMBER=+91XXXXXXXXXX in your .env file, "
                              "save 'my_whatsapp': '+91XXXXXXXXXX' in contacts.json, "
                              "or tell me 'My WhatsApp number is +91XXXXXXXXXX'.")

        # 3. Dynamic Fuzzy Contact Search in contacts.json
        contacts_file = Path(self.config.DATA_DIR) / "contacts.json"
        if contacts_file.exists():
            try:
                c_dict = json.loads(contacts_file.read_text(encoding='utf-8'))
                matched_number = None
                
                # Pass 1: Exact key match
                for k, v in c_dict.items():
                    if k.lower().strip() == contact_clean:
                        matched_number = v
                        break
                
                # Pass 2: Substring key match (e.g. "aditya" matches "aditya bhai" or "aditya")
                if not matched_number:
                    for k, v in c_dict.items():
                        k_clean = k.lower().strip()
                        if contact_clean in k_clean or k_clean in contact_clean:
                            matched_number = v
                            break
                            
                # Pass 3: Word token overlap match
                if not matched_number:
                    query_words = set(contact_clean.split())
                    for k, v in c_dict.items():
                        key_words = set(k.lower().strip().split())
                        if query_words.intersection(key_words):
                            matched_number = v
                            break

                if matched_number:
                    num = str(matched_number).replace(" ", "").replace("-", "")
                    if not num.startswith("+"):
                        num = "+91" + num
                    return num, None
                else:
                    return None, f"I don't have '{contact.title()}' saved in your contacts.json file. Please add '{contact.title()}': '+91XXXXXXXXXX' to backend/data/contacts.json or provide their phone number."
            except Exception as e:
                return None, f"Error reading contacts file: {e}"
        else:
            return None, f"Contacts file missing. Please create backend/data/contacts.json and add '{contact.title()}'s number."

    def _verify_whatsapp_network(self) -> bool:
        """Quick socket check to verify network reachability before WhatsApp Web operations."""
        import socket
        try:
            sock = socket.create_connection(("web.whatsapp.com", 443), timeout=2.5)
            sock.close()
            return True
        except Exception as e:
            logger.debug(f"Direct WhatsApp socket check failed ({e}), attempting fallback DNS check...")
            try:
                sock = socket.create_connection(("1.1.1.1", 53), timeout=2.5)
                sock.close()
                return True
            except Exception as net_err:
                logger.warning(f"Network verification failed: {net_err}")
                return False

    def _focus_whatsapp_window(self) -> "tuple[object | None, bool]":
        """Detect and focus a WhatsApp Web browser window.
        
        Returns:
            (window_object | None, was_already_open: bool)
        """
        try:
            import pygetwindow as gw
        except ImportError:
            logger.warning("pygetwindow is not installed – cannot detect WhatsApp window.")
            return None, False

        # Pass 1: Look for window with "whatsapp" in title (handles Chrome, Opera, Edge, etc.)
        wa_wins = [w for w in gw.getAllWindows() if w.title and "whatsapp" in w.title.lower()]
        
        if not wa_wins:
            # Pass 2: Fallback – look for any known browser window
            wa_wins = [w for w in gw.getAllWindows()
                       if w.title and any(b in w.title.lower()
                                          for b in ["chrome", "edge", "opera", "firefox", "brave"])]

        if not wa_wins:
            return None, False

        win = wa_wins[0]
        was_already_open = "whatsapp" in win.title.lower()

        # Activate the window (with minimize/restore fallback for pygetwindow quirks)
        for attempt in range(3):
            try:
                if win.isMinimized:
                    win.restore()
                    time.sleep(0.5)
                win.activate()
                time.sleep(0.6)
                return win, was_already_open
            except Exception as act_err:
                logger.debug(f"Window activate attempt {attempt+1} failed: {act_err}")
                try:
                    win.minimize()
                    time.sleep(0.3)
                    win.restore()
                    time.sleep(0.5)
                except Exception:
                    pass

        # Even if activate failed, still return the window so caller can proceed
        logger.warning("Could not reliably activate WhatsApp window, proceeding anyway.")
        return win, was_already_open

    def _skill_whatsapp_message(self, contact: str = "", message: str = "", **kw) -> str:
        if not PYAUTOGUI_AVAILABLE: 
            return "Typing needs: pip install pyautogui"
        
        # 1. Parameter swap detection (in case contact and message args were reversed by LLM)
        phone_num, err = self._resolve_contact_number(contact)
        actual_message = message
        
        if err and message:
            swap_num, swap_err = self._resolve_contact_number(message)
            if swap_num and not swap_err:
                phone_num = swap_num
                actual_message = contact
                err = None

        if err:
            return err
        if not actual_message or not actual_message.strip():
            return "What message should I send?"

        # 2. Network connectivity pre-check
        if not self._verify_whatsapp_network():
            return "❌ Low network speed or offline: Cannot connect to WhatsApp Web right now. Please check your internet connection."

        try:
            import webbrowser
            from urllib.parse import quote
            import pyautogui

            # 3. Check if WhatsApp window is ALREADY open
            existing_win, already_open = self._focus_whatsapp_window()

            # 4. Open WhatsApp URL (always needed – the URL carries the message text)
            url = f"https://web.whatsapp.com/send?phone={phone_num}&text={quote(actual_message)}"
            logger.info(f"Opening WhatsApp URL (already_open={already_open}): {url}")
            webbrowser.open(url)

            # 5. Wait for chat panel to load
            wait_seconds = 5.0 if already_open else 13.0
            logger.info(f"Waiting {wait_seconds}s for WhatsApp Web chat panel...")
            time.sleep(wait_seconds)

            # 6. Re-focus the WhatsApp window after URL load
            focused_win, _ = self._focus_whatsapp_window()
            if not focused_win:
                logger.warning("WhatsApp window not found after opening URL – trying to proceed anyway.")
                time.sleep(2.0)

            # 7. Press Enter to send message
            logger.info("Pressing Enter to send message...")
            pyautogui.press("enter")
            
            # Wait for transmission
            time.sleep(3.5)

            # Close tab cleanly
            pyautogui.hotkey("ctrl", "w")
            
            contact_label = contact.title() if contact else phone_num
            return f"✅ WhatsApp message successfully sent to {contact_label} ({phone_num})."

        except Exception as e:
            return f"❌ WhatsApp execution error: {e}"

    def _copy_image_to_clipboard(self, image_path: Path) -> bool:
        """Copies an image file directly into the Windows Clipboard in native CF_DIB format."""
        try:
            from PIL import Image
            import io
            import win32clipboard

            img = Image.open(image_path)
            output = io.BytesIO()
            img.convert("RGB").save(output, "BMP")
            data = output.getvalue()[14:]  # Strip 14-byte BMP header to form CF_DIB
            output.close()

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            win32clipboard.CloseClipboard()
            logger.info("Screenshot image loaded into Windows Clipboard (CF_DIB format).")
            return True
        except Exception as e:
            logger.warning(f"win32clipboard failed ({e}), falling back to PowerShell silent clip...")
            try:
                ps_script = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{str(image_path.absolute())}'))"
                # 0x08000000 = CREATE_NO_WINDOW (prevents popup/explorer triggers)
                subprocess.run(["powershell", "-Sta", "-Command", ps_script], creationflags=0x08000000)
                return True
            except Exception as ps_err:
                logger.error(f"Failed to copy image to clipboard: {ps_err}")
                return False

    def _attempt_send_screenshot(self, phone_num: str, fp: Path, already_open: bool) -> bool:
        """Helper to paste and send an existing screenshot file to WhatsApp Web tab."""
        import pyautogui
        import webbrowser

        # 1. Place image in Windows Clipboard
        if not self._copy_image_to_clipboard(fp):
            logger.error("Clipboard copy failed.")
            return False

        # 2. Open WhatsApp chat URL
        url = f"https://web.whatsapp.com/send?phone={phone_num}"
        logger.info(f"Opening WhatsApp URL: {url}")
        webbrowser.open(url)

        # 3. Wait for chat DOM to fully render
        wait_seconds = 5.0 if already_open else 12.0
        logger.info(f"Waiting {wait_seconds}s for WhatsApp Web chat panel to load...")
        time.sleep(wait_seconds)

        # 4. Focus WhatsApp window using centralized helper (handles Opera, minimize/restore, etc.)
        wa_win, _ = self._focus_whatsapp_window()
        if not wa_win:
            # Last resort: wait a bit more and try once more
            logger.warning("WhatsApp window not detected, waiting 5s extra...")
            time.sleep(5.0)
            wa_win, _ = self._focus_whatsapp_window()
            if not wa_win:
                logger.error("WhatsApp browser window was not found or could not be activated.")
                return False

        # 5. Paste screenshot image (Ctrl+V) – with retry if first paste doesn't trigger preview
        for paste_attempt in range(2):
            logger.info(f"Pasting screenshot via Ctrl+V (attempt {paste_attempt + 1})...")
            # Re-load clipboard before each paste attempt (ensures clipboard is fresh)
            if paste_attempt > 0:
                self._copy_image_to_clipboard(fp)
                time.sleep(0.5)
                # Re-focus window before retry paste
                self._focus_whatsapp_window()
                time.sleep(0.5)

            pyautogui.hotkey("ctrl", "v")

            # 6. Wait for WhatsApp media preview dialog to appear
            # This dialog takes 3-5s to fully render (shows image preview + send button)
            time.sleep(4.0)

            # 7. Press Enter to confirm send
            logger.info("Pressing Enter to send screenshot...")
            pyautogui.press("enter")

            # 8. Wait for media upload transmission
            time.sleep(5.0)

            # Check if the media preview dialog consumed the Enter (success indicator):
            # If we're still on the page after 5s, likely it went through
            # Note: there's no reliable programmatic check here, so we trust timing
            break  # Single attempt if paste goes through

        # 9. Close tab
        pyautogui.hotkey("ctrl", "w")
        return True

    def _skill_whatsapp_screenshot(self, contact: str = "", **kw) -> str:
        if not PYAUTOGUI_AVAILABLE: 
            return "Typing needs: pip install pyautogui"
            
        phone_num, err = self._resolve_contact_number(contact)
        if err:
            return err

        if not self._verify_whatsapp_network():
            return "❌ Low network speed or offline: Cannot connect to WhatsApp Web right now."

        # Take ONE single screenshot (saved to fp)
        ss_dir = Path(self.config.DATA_DIR) / "screenshots"
        ss_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = ss_dir / f"whatsapp_ss_{ts}.png"
        
        try:
            from PIL import ImageGrab
            ImageGrab.grab(all_screens=True).save(str(fp))
            logger.info(f"Captured single screenshot at: {fp}")
        except Exception as ss_err:
            return f"❌ Screenshot capture failed: {ss_err}"

        # Check if WhatsApp window is ALREADY open using centralized helper
        _, already_open = self._focus_whatsapp_window()

        # Attempt 1: Send screenshot
        logger.info("Attempt 1: Sending screenshot to WhatsApp...")
        success = self._attempt_send_screenshot(phone_num, fp, already_open)

        # Fail Case: If Attempt 1 fails, RETRY using SAME screenshot (no new capture!)
        if not success:
            logger.warning("Attempt 1 failed! Retrying with SAME captured screenshot...")
            time.sleep(3.0)
            _, already_open = self._focus_whatsapp_window()
            success = self._attempt_send_screenshot(phone_num, fp, already_open)

        contact_label = contact.title() if contact else phone_num

        if success:
            return f"✅ Screenshot successfully sent to {contact_label} ({phone_num}) on WhatsApp."
        else:
            return f"❌ Failed to send screenshot to {contact_label} ({phone_num}) after 2 attempts. WhatsApp window could not be focused or loaded."

    def _skill_type_text(self, *args) -> str:
        if not PYAUTOGUI_AVAILABLE: 
            return "Typing needs: pip install pyautogui"
        text = " ".join(args).strip()
        if not text: 
            return "What should I type?"
        try:
            time.sleep(1.5)
            pyautogui.write(text, interval=0.04)
            return "Typed."
        except Exception as e:
            return f"Typing failed: {e}"

    def _skill_key_press(self, key_name: str = "enter", *args) -> str:
        if not PYAUTOGUI_AVAILABLE:
            return "Key press needs: pip install pyautogui"
        key = (key_name or "enter").lower().strip()
        try:
            time.sleep(0.5)
            pyautogui.press(key)
            return f"Pressed {key}."
        except Exception as e:
            return f"Key press failed: {e}"

    def _skill_system_shutdown(self, delay: str = "30", **kw) -> str:
        try:
            secs = max(0, int(delay))
            if platform.system() == "Windows": 
                subprocess.run(["shutdown", "/s", "/t", str(secs)], check=True)
            else: 
                subprocess.run(["sudo", "shutdown", "-h", f"+{max(1,secs//60)}"], check=True)
            return f"Shutting down in {secs}s. Save your work."
        except Exception as e: 
            return f"Shutdown failed: {e}"

    def _skill_system_restart(self, delay: str = "30", **kw) -> str:
        try:
            secs = max(0, int(delay))
            if platform.system() == "Windows": 
                subprocess.run(["shutdown", "/r", "/t", str(secs)], check=True)
            else: 
                subprocess.run(["sudo", "shutdown", "-r", f"+{max(1,secs//60)}"], check=True)
            return f"Restarting in {secs}s."
        except Exception as e: 
            return f"Restart failed: {e}"

    def _skill_email_send(self, to="", subject="", body=""): 
        return self.email_agent.send_email(to, subject, body)

    def _skill_email_check(self, *args): 
        return self.email_agent.check_emails()

    def _skill_calendar_today(self, *args): 
        return self.calendar_agent.today()

    def _skill_calendar_week(self, *args): 
        return self.calendar_agent.week()

    def _skill_calendar_add(self, title: str = "", date: str = "", time: str = ""):
        if not title or not date: 
            return "Usage: calendar_add:title:YYYY-MM-DD:HH:MM"
        return self.calendar_agent.add_event(title, date, time)

    def _skill_browser_open(self, *args): 
        return self.browser_agent.open_url(args[0] if args else "")

    def _skill_browser_click(self, *args): 
        return self.browser_agent.click(args[0] if args else "")

    def _skill_browser_type(self, *args):
        if len(args) < 2: 
            return "Usage: browser_type:selector:text"
        return self.browser_agent.type_text(args[0], args[1])

    def _skill_browser_scrape(self, *args):
        if len(args) < 2: 
            return "Usage: browser_scrape:url:query"
        return self.browser_agent.scrape(args[0], args[1])

    def _skill_fan(self, *args): 
        return self.smarthome_agent.fan_control(args[0] if args else "on", args[1] if len(args)>1 else "")

    def _skill_smart_light(self, *args): 
        return self.smarthome_agent.light_control(args[0] if args else "on", args[1] if len(args)>1 else "")

    def _skill_smart_ac(self, *args): 
        return self.smarthome_agent.ac_control(args[0] if args else "on", args[1] if len(args)>1 else "")

    def _skill_plugin_list(self, *args): 
        return self.plugin_loader.list_plugins()

    def _skill_plugin_reload(self, *args):
        self.plugin_loader.reload()
        self.skills_registry = self._register_skills()
        return "Plugins reloaded."

    def _skill_kb_search(self, *args) -> str:
        query = " ".join(args).strip()
        if not query:
            return "What should I search in the knowledge base?"
        try:
            from modules.knowledge_base import get_knowledge_base
            kb  = get_knowledge_base(self.config)
            ctx = kb.query(query, top_k=3, min_similarity=0.20)
            if ctx:
                return ctx
            return f"Nothing relevant found in knowledge base for: '{query}'."
        except Exception as e:
            return f"KB search failed: {e}"

    def _skill_kb_rebuild(self, *args) -> str:
        try:
            from modules.knowledge_base import get_knowledge_base
            result = get_knowledge_base(self.config).build_index()
            if "error" in result:
                return result["error"]
            files  = result.get("files", 0)
            chunks = result.get("chunks", 0)
            indexed = result.get("indexed", [])
            msg = f"Knowledge base rebuilt: {files} file(s), {chunks} chunks indexed."
            if indexed:
                msg += "\n" + "\n".join(f"  • {f}" for f in indexed)
            return msg
        except Exception as e:
            return f"KB rebuild failed: {e}"

    def _skill_kb_list(self, *args) -> str:
        try:
            from modules.knowledge_base import get_knowledge_base
            return get_knowledge_base(self.config).list_documents()
        except Exception as e:
            return f"KB list failed: {e}"

    def _skill_kb_stats(self, *args) -> str:
        try:
            from modules.knowledge_base import get_knowledge_base
            stats = get_knowledge_base(self.config).get_stats()
            if not stats.get("ready"):
                return f"Knowledge base not ready. {stats.get('error', '')}"
            return (
                f"Knowledge base stats:\n"
                f"  • Indexed chunks : {stats['chunks']}\n"
                f"  • .md files      : {stats['md_files']}\n"
                f"  • KB folder      : {stats['kb_dir']}\n"
                f"  • ChromaDB path  : {stats['chroma_dir']}"
            )
        except Exception as e:
            return f"KB stats failed: {e}"

    # ════════════════════════════════════════════
    # AI ORCHESTRATOR SKILLS
    # ════════════════════════════════════════════

    async def _skill_ai_ask(self, *args) -> str:
        """Send a query to a specific AI platform via the orchestrator.
        Usage: [SKILL:ai_ask:chatgpt:Write me a Python sort function]
               [SKILL:ai_ask:gemini:Explain async/await]
        First arg = platform name, rest = the query.
        """
        if not args:
            return "Which platform and query? Usage: ai_ask:platform:your question"

        platform = args[0].strip().lower()
        query    = " ".join(args[1:]).strip()

        if not query:
            return f"What should I ask {platform}?"

        try:
            from modules.ai_orchestrator.orchestrator import get_orchestrator
            orchestrator = get_orchestrator(self.config)
            logger.info(f"ai_ask → platform={platform!r}, query={query[:80]!r}")
            result = await orchestrator.ask_ai(platform, query)
            return result
        except Exception as e:
            logger.error(f"ai_ask skill failed: {e}", exc_info=True)
            return f"Could not reach {platform}: {e}"

    async def _skill_ai_chain(self, *args) -> str:
        """Chain two AI platforms: p1 answers, its response is fed to p2 as context.
        Usage: [SKILL:ai_chain:chatgpt:gemini:Write a login page in React]
        First arg = source platform, second = refine platform, rest = query.
        """
        if len(args) < 3:
            return "Usage: ai_chain:platform1:platform2:your query"

        p1    = args[0].strip().lower()
        p2    = args[1].strip().lower()
        query = " ".join(args[2:]).strip()

        if not query:
            return f"What task should I chain between {p1} and {p2}?"

        try:
            from modules.ai_orchestrator.orchestrator import get_orchestrator
            orchestrator = get_orchestrator(self.config)
            logger.info(f"ai_chain → {p1} ➜ {p2}, query={query[:80]!r}")
            result = await orchestrator.chain_ai(p1, p2, query)
            return result
        except Exception as e:
            logger.error(f"ai_chain skill failed: {e}", exc_info=True)
            return f"Chain between {p1} and {p2} failed: {e}"

    # ════════════════════════════════════════════
    # RESEARCH SKILL (Bridge to WebAutopilot)
    # ════════════════════════════════════════════

    def _skill_research(self, *args) -> str:
        """Launch background agentic research via WebAutopilotEngine.
        Uses asyncio.run_coroutine_threadsafe to safely stream results
        back to the frontend WebSocket from the selenium background thread."""
        query = " ".join(args).strip()
        if not query:
            return "What topic should I research?"

        from modules.web_autopilot import WebAutopilotEngine

        def tts_callback(text: str, metadata: dict = None):
            """Thread-safe callback that targets the global main_loop and
            active_websocket objects from main.py without crashing."""
            try:
                # Import the globals lazily to avoid circular import at module load
                import main as _main_module
                ws = _main_module.active_websocket
                loop = _main_module.main_loop
                if not ws or not loop:
                    logger.warning("Research callback: no active WebSocket or event loop")
                    return

                async def _push():
                    try:
                        payload = {
                            "event": "response_text",
                            "text": text,
                            "skill_used": "research",
                        }
                        if metadata:
                            payload["metadata"] = metadata
                        await ws.send_json(payload)

                        # Also generate and stream TTS for the text
                        from modules.tts import generate_tts
                        import base64, os
                        tts_path = await generate_tts(text[:3000])
                        if tts_path and os.path.exists(tts_path):
                            with open(tts_path, "rb") as f:
                                encoded = base64.b64encode(f.read()).decode("utf-8")
                                await ws.send_json({
                                    "event": "audio_response",
                                    "audio": encoded,
                                })
                    except Exception as e:
                        logger.error(f"Research TTS callback push failed: {e}")

                asyncio.run_coroutine_threadsafe(_push(), loop)
            except Exception as e:
                logger.error(f"Research tts_callback outer error: {e}")

        autopilot = WebAutopilotEngine(self.config)
        autopilot.run_background_research(query, tts_callback)
        return f"Boss, main background mein {query} pe research shuru kar rahi hu. Aap apna kaam continue karo."

    # ════════════════════════════════════════════
    # CREATE FILE SKILL (Plain text/document files)
    # ════════════════════════════════════════════

    async def _skill_create_file(self, *args) -> str:
        """Create a plain text/document file with AI-generated content about a topic.
        NOT for code — use write_code for that."""
        if not args:
            return "What should I write about? Give me a filename and topic."

        # Parse args: filename:topic or just topic
        if len(args) >= 2:
            filename = args[0].strip()
            topic = " ".join(args[1:]).strip()
        else:
            topic = args[0].strip()
            # Auto-generate filename from topic
            safe_name = re.sub(r'[^a-zA-Z0-9\s]', '', topic)[:30].strip().replace(' ', '_')
            filename = f"{safe_name}.txt" if safe_name else "document.txt"

        # Ensure .txt extension if no extension given
        if '.' not in filename:
            filename += '.txt'

        try:
            prompt = f"""Write a detailed, well-structured document about: {topic}

Rules:
- Write in a clear, natural, human-friendly tone
- Use proper headings, sections, and paragraphs for readability
- Be comprehensive, informative, and detailed
- Include key facts, explanations, and insights
- Do NOT write code — write plain readable text content only
- Do NOT use markdown code blocks or any programming syntax
- Organize information logically with clear section headers
- Write in English"""

            async def call():
                from groq import AsyncGroq
                client = AsyncGroq(api_key=self.config.get_active_api_key())
                return await client.chat.completions.create(
                    model=self.config.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6,
                    max_tokens=2500,
                )

            response = await execute_with_retry(call)
            content = response.choices[0].message.content.strip()

            # Check if filename includes a directory path to resolve
            resolved_path = self._resolve_user_path(filename)
            if resolved_path and resolved_path.parent != Path.home(): 
                # Meaning it resolved to a specific folder like Desktop
                file_path = resolved_path
                save_dir = file_path.parent
                save_dir.mkdir(parents=True, exist_ok=True)
            else:
                # Save to CODE_SAVE_DIR (where MAX saves generated files)
                save_dir = self.config.CODE_SAVE_DIR
                save_dir.mkdir(parents=True, exist_ok=True)
                file_path = save_dir / filename

            # Avoid overwriting existing files
            if file_path.exists():
                stem = file_path.stem
                ext = file_path.suffix
                counter = 1
                while file_path.exists():
                    file_path = save_dir / f"{stem}_{counter}{ext}"
                    counter += 1

            file_path.write_text(content, encoding='utf-8')

            try:
                rel_path = file_path.relative_to(self.config.WORKSPACE_DIR)
            except ValueError:
                rel_path = file_path.name

            logger.info(f"Text file created: {file_path} ({len(content)} chars)")
            return f"File created: {rel_path}"

        except Exception as e:
            logger.error(f"create_file error: {e}")
            return f"File create karne mein error aaya: {str(e)}"


    async def _skill_count(self, start: str, end: str, reverse: str = "False") -> str:
        """Count numbers from start to end with natural pauses and stream via WebSocket."""
        print(f"🔢 [COUNT DEBUG] _skill_count called: start={start}, end={end}, reverse={reverse}")
        try:
            start_num = int(start.strip())
            end_num = int(end.strip())
            is_reverse = str(reverse).strip().lower() in ['true', 'yes', '1']
            
            if is_reverse or start_num > end_num:
                step = -1
                if start_num < end_num:
                    start_num, end_num = end_num, start_num
            else:
                step = 1
                
            if abs(end_num - start_num) > 1000:
                return "That's too many numbers for me to count."
                
            numbers = list(range(start_num, end_num + step, step))
            print(f"🔢 [COUNT DEBUG] Numbers to count: {numbers}")
            
            try:
                from agent_core import get_active_websocket
                from modules.tts import generate_tts
                import base64
                import os
                
                ws = get_active_websocket()
                print(f"🔢 [COUNT DEBUG] WebSocket available: {ws is not None}")
                if not ws:
                    print("🔴 [COUNT DEBUG] WebSocket is None! Cannot stream audio.")
                    return ""
                
                for num in numbers:
                    # Check for interruption
                    from agent_core import _agent_instance
                    if _agent_instance and not _agent_instance.listening_manager.continuous_mode:
                        logger.info("Counting interrupted by user stop command.")
                        break
                        
                    num_word = _number_to_words(num)
                    print(f"🔢 [COUNT DEBUG] Generating TTS for: {num_word}")
                    tts_path = await generate_tts(f"{num_word}.")
                    print(f"🔢 [COUNT DEBUG] TTS path: {tts_path}")
                    
                    if tts_path and os.path.exists(tts_path):
                        with open(tts_path, "rb") as f:
                            encoded_audio = base64.b64encode(f.read()).decode('utf-8')
                            await ws.send_json({"event": "audio_response", "audio": encoded_audio})
                        print(f"🔢 [COUNT DEBUG] Sent audio for number {num}")
                        try:
                            os.remove(tts_path)
                        except Exception:
                            pass
                    
                    await asyncio.sleep(self.config.COUNTING_PAUSE)
                
                print("🔢 [COUNT DEBUG] Counting loop complete!")
                return ""
            except Exception as e:
                import traceback
                print(f"🔴 [COUNT DEBUG] Stream count EXCEPTION: {e}")
                logger.error(f"Stream count failed: {e}\n{traceback.format_exc()}")
                return ""
            
        except ValueError as ve:
            print(f"🔴 [COUNT DEBUG] ValueError: {ve}")
            return "Please provide valid numbers to count."

    # ════════════════════════════════════════════
    # FILE OPERATIONS SKILLS
    # ════════════════════════════════════════════

    def _skill_save_as(self, filename: str = "", *args) -> str:
        """Save the current file with a new name using Ctrl+Shift+S dialog."""
        if not PYAUTOGUI_AVAILABLE:
            return "Save As needs: pip install pyautogui"
        full_name = (filename + " " + " ".join(args)).strip() if args else filename.strip()
        if not full_name:
            return "What filename should I save as?"
        try:
            # Check if there is a slash, meaning it contains a directory
            if "/" in full_name or "\\" in full_name:
                resolved_path = self._resolve_user_path(full_name)
                # Ensure it's returned as a string for PyAutoGUI
                typing_str = str(resolved_path) if resolved_path else full_name
            else:
                typing_str = full_name

            pyautogui.hotkey('ctrl', 'shift', 's')
            time.sleep(1.5)  # Wait for Save As dialog to open
            # Clear existing filename field and type new name
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.2)
            pyautogui.typewrite(typing_str, interval=0.03)
            time.sleep(0.3)
            pyautogui.press('enter')
            return f"File saved as '{typing_str}'."
        except Exception as e:
            return f"Save As failed: {e}"

    def _skill_rename_file(self, old_name: str = "", new_name: str = "", *args) -> str:
        """Rename a file or folder."""
        if not old_name:
            return "Which file should I rename?"
        if not new_name:
            return "What should the new name be?"
        try:
            old_path = Path(old_name.strip()).expanduser().resolve()
            if not old_path.is_absolute():
                for d in self.file_manager.search_dirs:
                    candidate = d / old_name.strip()
                    if candidate.exists():
                        old_path = candidate
                        break
            if not old_path.exists():
                return f"File not found: {old_name}"
            new_path = old_path.parent / new_name.strip()
            if new_path.exists():
                return f"A file named '{new_name}' already exists in that folder."
            old_path.rename(new_path)
            return f"Renamed '{old_path.name}' to '{new_name.strip()}'."
        except Exception as e:
            return f"Rename failed: {e}"

    def _skill_delete_file(self, filepath: str = "", *args) -> str:
        """Delete a file (sends to recycle bin if possible)."""
        full_path_str = (filepath + " " + " ".join(args)).strip() if args else filepath.strip()
        if not full_path_str:
            return "Which file should I delete?"
        try:
            path = Path(full_path_str).expanduser().resolve()
            if not path.is_absolute():
                for d in self.file_manager.search_dirs:
                    candidate = d / full_path_str
                    if candidate.exists():
                        path = candidate
                        break
            if not path.exists():
                return f"File not found: {full_path_str}"
            name = path.name
            try:
                from send2trash import send2trash
                send2trash(str(path))
                return f"'{name}' moved to Recycle Bin."
            except ImportError:
                path.unlink()
                return f"'{name}' deleted permanently (send2trash not installed for Recycle Bin)."
        except Exception as e:
            return f"Delete failed: {e}"

    def _skill_move_file(self, source: str = "", destination: str = "", *args) -> str:
        """Move a file or folder to a new location."""
        import shutil
        if not source:
            return "Which file should I move?"
        if not destination:
            return "Where should I move it to?"
        try:
            src = Path(source.strip()).expanduser().resolve()
            if not src.is_absolute():
                for d in self.file_manager.search_dirs:
                    candidate = d / source.strip()
                    if candidate.exists():
                        src = candidate
                        break
            if not src.exists():
                return f"Source not found: {source}"
            
            # Resolve destination
            dst_str = destination.strip()
            resolved_dst = self._resolve_user_path(dst_str)
            if resolved_dst:
                dst = resolved_dst
            else:
                dst = Path(dst_str).expanduser().resolve()
                if not dst.is_absolute():
                    dst = self.file_manager.search_dirs[0] / dst_str

            if dst.is_dir():
                dst = dst / src.name
            shutil.move(str(src), str(dst))
            return f"Moved '{src.name}' to '{dst.parent.name}/{dst.name}'."
        except Exception as e:
            return f"Move failed: {e}"

    def _skill_copy_file(self, source: str = "", destination: str = "", *args) -> str:
        """Copy a file to a new location."""
        import shutil
        if not source:
            return "Which file should I copy?"
        if not destination:
            return "Where should I copy it to?"
        try:
            src = Path(source.strip()).expanduser().resolve()
            if not src.is_absolute():
                for d in self.file_manager.search_dirs:
                    candidate = d / source.strip()
                    if candidate.exists():
                        src = candidate
                        break
            if not src.exists():
                return f"Source not found: {source}"

            # Resolve destination
            dst_str = destination.strip()
            resolved_dst = self._resolve_user_path(dst_str)
            if resolved_dst:
                dst = resolved_dst
            else:
                dst = Path(dst_str).expanduser().resolve()
                if not dst.is_absolute():
                    dst = self.file_manager.search_dirs[0] / dst_str

            if dst.is_dir():
                dst = dst / src.name
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))
            return f"Copied '{src.name}' to '{dst.parent.name}/{dst.name}'."
        except Exception as e:
            return f"Copy failed: {e}"

    # ════════════════════════════════════════════
    # OPEN FILE SKILL (opens file with default OS app)
    # ════════════════════════════════════════════

    def _skill_open_file(self, filepath: str = "", *args) -> str:
        """Open a file with its default OS application."""
        full_path_str = (filepath + " " + " ".join(args)).strip() if args else filepath.strip()
        if not full_path_str:
            return "Which file should I open?"
        try:
            path = Path(full_path_str).expanduser().resolve()
            if not path.is_absolute():
                for d in self.file_manager.search_dirs:
                    candidate = d / full_path_str
                    if candidate.exists():
                        path = candidate
                        break
            if not path.exists():
                return f"File not found: {full_path_str}"
            os.startfile(str(path))
            return f"Opened '{path.name}'."
        except AttributeError:
            # os.startfile is Windows only, use xdg-open for Linux
            try:
                subprocess.Popen(["xdg-open", str(path)])
                return f"Opened '{path.name}'."
            except Exception as e:
                return f"Open failed: {e}"
        except Exception as e:
            return f"Open failed: {e}"

    # ════════════════════════════════════════════
    # SYSTEM TOGGLE SKILLS
    # ════════════════════════════════════════════

    def _skill_wifi_toggle(self, state: str = "on", *args) -> str:
        """Toggle WiFi on or off (Windows only)."""
        if platform.system() != "Windows":
            return "WiFi toggle is only supported on Windows."
        action = "enable" if state.strip().lower() in ["on", "enable", "start"] else "disable"
        try:
            result = subprocess.run(
                ["netsh", "interface", "set", "interface", "Wi-Fi", action],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return f"WiFi {action}d."
            return f"WiFi toggle failed: {result.stderr.strip()}"
        except Exception as e:
            return f"WiFi toggle failed: {e}"

    def _skill_bluetooth_toggle(self, state: str = "on", *args) -> str:
        """Toggle Bluetooth on or off (Windows only)."""
        if platform.system() != "Windows":
            return "Bluetooth toggle is only supported on Windows."
        action_bool = "true" if state.strip().lower() in ["on", "enable", "start"] else "false"
        try:
            ps_cmd = (
                "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
                "[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null; "
                "$radios = [Windows.Devices.Radios.Radio]::GetRadiosAsync().AsTask().Result; "
                "$bt = $radios | Where-Object { $_.Kind -eq 'Bluetooth' }; "
                f"if ($bt) {{ $bt.SetStateAsync([Windows.Devices.Radios.RadioState]::"
                f"{'On' if action_bool == 'true' else 'Off'}).AsTask().Wait() }}"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=15
            )
            return f"Bluetooth turned {'on' if action_bool == 'true' else 'off'}."
        except Exception as e:
            return f"Bluetooth toggle failed: {e}"

    def _skill_night_light(self, state: str = "on", *args) -> str:
        """Toggle Night Light on or off (Windows only)."""
        if platform.system() != "Windows":
            return "Night Light toggle is only supported on Windows."
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.bluelightreductionstate\windows.data.bluelightreduction.bluelightreductionstate"
            # Simplest approach: open Night Light settings
            os.startfile("ms-settings:nightlight")
            return f"Night Light settings opened. Please toggle it {'on' if state.strip().lower() in ['on', 'enable'] else 'off'}."
        except Exception as e:
            return f"Night Light toggle failed: {e}"

    # ════════════════════════════════════════════
    # MAX UPTIME SKILL
    # ════════════════════════════════════════════

    _start_time = datetime.now()

    def _skill_uptime(self, *args) -> str:
        """Report how long MAX has been running."""
        delta = datetime.now() - self._start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if not parts:
            parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
        return f"Been running for {', '.join(parts)}."

    # ════════════════════════════════════════════
    # CHECK PROCESS SKILL
    # ════════════════════════════════════════════

    def _skill_check_process(self, app_name: str = "", *args) -> str:
        """Check if a specific application is currently running."""
        full_name = (app_name + " " + " ".join(args)).strip() if args else app_name.strip()
        if not full_name:
            return "Which app should I check?"
        try:
            import psutil
            app_lower = full_name.lower()
            for proc in psutil.process_iter(['name']):
                try:
                    if app_lower in proc.info['name'].lower():
                        return f"Yes, {full_name} is running (process: {proc.info['name']})."
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return f"No, {full_name} is not running."
        except ImportError:
            return "Process check needs: pip install psutil"
        except Exception as e:
            return f"Process check failed: {e}"

    def _skill_get_recent_history(self, *args) -> str:
        """Recall extended past conversation history on-demand."""
        limit = 20
        from modules.memory import get_memory_manager
        mm = get_memory_manager(self.config)
        return mm.get_recent_history(limit=limit)

    async def _skill_file_find(self, *args) -> str:
        """Fuzzy search for a file in user directories (Desktop, Downloads, Documents, Pictures)."""
        query = " ".join(args).strip()
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, msg, file_path = fm.find_file(query)
        if success and file_path:
            return f"Found file: '{file_path.name}' at location '{file_path}'"
        return msg

    async def _skill_file_copy(self, *args) -> str:
        """Copy a file to target user directory."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        query = parts[0] if parts else raw
        dest = parts[1] if len(parts) > 1 else "desktop"
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, msg = fm.copy_file(query, dest)
        return msg

    async def _skill_file_move(self, *args) -> str:
        """Move a file to target user directory."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        query = parts[0] if parts else raw
        dest = parts[1] if len(parts) > 1 else "desktop"
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, msg = fm.move_file(query, dest)
        return msg

    async def _skill_file_send_whatsapp(self, *args) -> str:
        """Locate file and trigger WhatsApp attachment pipeline."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        query = parts[0] if parts else raw
        recipient = parts[1] if len(parts) > 1 else ""
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, msg, file_path = fm.find_file(query)
        if not success or not file_path:
            return f"WhatsApp send failed: {msg}"
        
        web_res = await self._skill_web_open("whatsapp.com")
        return f"File '{file_path.name}' found at '{file_path}'. Opened WhatsApp. Target contact: '{recipient or 'Current Chat'}'."

    async def _skill_file_upload_browser(self, *args) -> str:
        """Locate file and trigger browser upload + dictation pipeline."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        query = parts[0] if parts else raw
        prompt_text = parts[1] if len(parts) > 1 else ""
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, msg, file_path = fm.find_file(query)
        if not success or not file_path:
            return f"Browser upload failed: {msg}"

        return f"File '{file_path.name}' ready for browser upload at '{file_path}'. Dictated prompt: '{prompt_text}'."

    async def _skill_file_list_by_date(self, *args) -> str:
        """List files in folder filtered by relative days ago (e.g. 2 din pehle, kal, aaj)."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        folder = parts[0] if parts else "downloads"
        days_ago = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, summary, files = fm.list_files_by_relative_date(folder, days_ago)
        return summary

    async def _skill_file_list_whatsapp(self, *args) -> str:
        """List documents from relative date and send report over WhatsApp."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        folder = parts[0] if parts else "documents"
        days_ago = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        recipient = parts[2] if len(parts) > 2 else ""
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        success, summary, files = fm.list_files_by_relative_date(folder, days_ago)
        web_res = await self._skill_web_open("whatsapp.com")
        return f"Document summary generated: '{summary}'. Opened WhatsApp for contact '{recipient or 'Current Chat'}'."

    async def _skill_folder_screenshot_whatsapp(self, *args) -> str:
        """Open target folder window, capture screenshot, and send via WhatsApp."""
        raw = " ".join(args).strip()
        parts = [p.strip() for p in raw.split("|") if p.strip()]
        folder = parts[0] if parts else "downloads"
        recipient = parts[1] if len(parts) > 1 else ""
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        folder_path = fm.resolve_folder_alias(folder)
        if platform.system() == "Windows":
            os.startfile(str(folder_path.resolve()))
        else:
            os.system(f'explorer "{folder_path}"')
        await asyncio.sleep(1.2)
        ss_res = await self._skill_read_screen("current")
        web_res = await self._skill_web_open("whatsapp.com")
        return f"Captured screenshot of folder '{folder_path.name}'. Opened WhatsApp for '{recipient or 'Current Chat'}'."

    async def _skill_vscode_git_push(self, *args) -> str:
        """Open VS Code, open terminal, and auto-push files to GitHub."""
        commit_msg = " ".join(args).strip() or "Auto update via MAX"
        from modules.file_manager import get_file_manager
        fm = get_file_manager()
        ws_dir = fm.folder_map["workspace"]
        os.system(f'code "{ws_dir}"')
        await asyncio.sleep(1.5)
        try:
            import pyautogui
            pyautogui.hotkey('ctrl', '`')
            await asyncio.sleep(0.8)
            pyautogui.typewrite(f'git add . && git commit -m "{commit_msg}" && git push\n', interval=0.03)
            return f"Opened VS Code and executed 'git push' in terminal with commit message: '{commit_msg}'."
        except Exception as e:
            return f"VS Code Git push executed via CLI: {e}"

    async def _skill_vscode_close_folder(self, *args) -> str:
        """Close active open folder in VS Code via PyAutoGUI hotkeys."""
        try:
            import pyautogui
            pyautogui.hotkey('ctrl', 'k')
            await asyncio.sleep(0.3)
            pyautogui.press('f')
        except Exception as e:
            return f"VS Code close folder execution: {e}"

    async def _skill_key_chord(self, *args) -> str:
        """
        Press and HOLD simultaneous key combinations (e.g., Alt+F4, Ctrl+W, Ctrl+Shift+Esc, Win+D).
        Usage: [SKILL:key_chord:alt|f4] or [SKILL:key_chord:ctrl|w]
        """
        raw = " ".join(args).strip()
        keys = [k.strip().lower() for k in raw.replace("+", "|").split("|") if k.strip()]
        if not keys:
            return "No keys provided for chord."

        try:
            import pyautogui
            for key in keys[:-1]:
                pyautogui.keyDown(key)
            pyautogui.press(keys[-1])
            for key in reversed(keys[:-1]):
                pyautogui.keyUp(key)

            combo_str = " + ".join(keys).upper()
            logger.info(f"🎹 Executed Key Chord: {combo_str}")
            return f"Successfully executed key shortcut '{combo_str}'."
        except Exception as e:
            return f"Key chord error: {e}"

    async def _skill_close_window(self, *args) -> str:
        """Close current active window via Alt+F4 simultaneous key chord."""
        return await self._skill_key_chord("alt|f4")

    async def _skill_close_app(self, *args) -> str:
        """
        Close a specific app by name (e.g., 'notepad', 'chrome', 'vscode', 'whatsapp', 'calc')
        or close current window if no app name provided.
        """
        app_name = " ".join(args).strip().lower()
        if not app_name or app_name in ["current", "active", "this", "this window", "window"]:
            return await self._skill_close_window()

        process_map = {
            "notepad": "notepad.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "vscode": "code.exe",
            "vs code": "code.exe",
            "code": "code.exe",
            "whatsapp": "WhatsApp.exe",
            "calc": "calculatorapp.exe",
            "calculator": "calculatorapp.exe",
            "edge": "msedge.exe",
            "browser": "msedge.exe",
            "spotify": "spotify.exe",
            "vlc": "vlc.exe",
            "word": "winword.exe",
            "excel": "excel.exe"
        }

        proc_exe = None
        for k, exe in process_map.items():
            if k in app_name:
                proc_exe = exe
                break

        if proc_exe:
            os.system(f"taskkill /IM {proc_exe} /F >nul 2>&1")
            return f"Closed application '{app_name}' ({proc_exe})."

        try:
            import pygetwindow as gw
            wins = gw.getWindowsWithTitle(app_name)
            if wins:
                wins[0].activate()
                await asyncio.sleep(0.3)
                return await self._skill_close_window()
        except Exception:
            pass

        os.system(f"taskkill /IM {app_name}.exe /F >nul 2>&1")
        return f"Triggered close command for '{app_name}'."

def _number_to_words(n: int) -> str:
    """Convert integer to English words for better TTS pronunciation."""
    if n == 0:
        return "zero"
    
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    
    if n < 0:
        return "minus " + _number_to_words(-n)
    
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 != 0 else "")
    if n < 1000:
        return ones[n // 100] + " hundred" + (" and " + _number_to_words(n % 100) if n % 100 != 0 else "")
    if n < 1000000:
        return _number_to_words(n // 1000) + " thousand" + (" " + _number_to_words(n % 1000) if n % 1000 != 0 else "")
    return str(n)  # Fallback to digits for huge numbers

# ══════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════

_skills_instance: Optional[SkillsEngine] = None

def get_skills_engine(config) -> SkillsEngine:
    global _skills_instance
    if _skills_instance is None:
        _skills_instance = SkillsEngine(config)
    return _skills_instance
