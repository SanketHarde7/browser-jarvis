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
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    import pywhatkit
    PYWHATKIT_AVAILABLE = True
except ImportError:
    PYWHATKIT_AVAILABLE = False

DATA_SKILLS = {
    "weather", "search", "note", "timer", "time_now", "date_today",
    "find_and_explain", "list_files", "read_file",
    "code_review", "run_code", "search_files",
    "read_screen", "list_windows",
    "email_check", "calendar_today", "calendar_week",
    "browser_scrape", "plugin_list", "list_apps",
    "sysinfo", "top_processes", "reminder_list",
    "kb_search", "kb_list", "kb_stats", "kb_rebuild",
    "ai_ask", "ai_ask_screen", "ai_ask_file", "ai_ask_clipboard",
    "ai_compare", "ai_chain", "ai_route", "ai_workflow",
    "ai_workflow_save", "ai_workflow_list"
}

LONG_RESULT_SKILLS = {
    "find_and_explain", "list_files", "read_file",
    "code_review", "run_code", "search_files",
    "read_screen", "list_windows", "browser_scrape",
    "list_apps", "top_processes",
    "ai_ask", "ai_ask_screen", "ai_ask_file", "ai_ask_clipboard",
    "ai_compare", "ai_chain", "ai_route", "ai_workflow",
    "ai_workflow_save", "ai_workflow_list"
}

TTS_MAX_CHARS = 280


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
    try:
        if platform.system() == "Windows":
            os.startfile(url)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception as e:
        logger.error(f"Native browser open failed: {e}. Falling back to webbrowser.")
        try:
            import webbrowser
            webbrowser.open(url)
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
            self._file_manager = get_file_manager(self.config)
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

    def _load_plugins(self):
        try:
            self.plugin_loader.load_all()
        except Exception as e:
            logger.warning(f"Plugin load failed: {e}")

    def _register_skills(self) -> Dict[str, Any]:
        base = {
            "weather":           self._skill_weather,
            "timer":             self._skill_timer,
            "note":              self._skill_note,
            "search":            self._skill_web_search,
            "youtube_search":    self._skill_youtube_search,
            "youtube_play":      self._skill_youtube_play,
            "time_now":          self._skill_time_now,
            "date_today":        self._skill_date_today,
            "clear_memory":      self._skill_clear_memory,
            "add_rule":          self._skill_add_rule,
            "sysinfo":           self._skill_sysinfo,
            "top_processes":     self._skill_top_processes,
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
            "create_file":       self._skill_create_file,
            "schedule_action":   self._skill_schedule_action,
            # ── AI Orchestrator skills ──────────────────────────────────────
            "ai_ask":            self._skill_ai_ask,
            "ai_chain":          self._skill_ai_chain,
        }
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

        # ── Parallel execution for launch-type skills ──────────────────────
        # open_app / web_open just fire os.startfile or webbrowser — safe to
        # run concurrently. Every other skill runs sequentially as before.
        PARALLEL_SKILLS = {"open_app", "web_open"}

        parallel_matches = [m for m in matches if m.group(1).lower() in PARALLEL_SKILLS]
        serial_matches   = [m for m in matches if m.group(1).lower() not in PARALLEL_SKILLS]

        async def _run_single_match(match):
            skill_name = match.group(1).lower()
            params_str  = match.group(2) or ""
            params = self._parse_parameters(skill_name, params_str)

            if skill_name not in self.skills_registry:
                logger.warning(f"Unknown skill: {skill_name}")
                try:
                    from modules.skill_forge import get_skill_forge
                    get_skill_forge(self.config).record_unknown_skill(skill_name, user_request)
                except Exception as e:
                    logger.error(f"Failed to record unknown skill in SkillForge: {e}")
                return None, skill_name, False

            try:
                logger.info(f"⚙️  Executing {skill_name}({params})")
                func = self.skills_registry[skill_name]
                if asyncio.iscoroutinefunction(func):
                    result = await func(*params)
                else:
                    # Run sync skill in a separate thread to keep the event loop non-blocking
                    result = await asyncio.to_thread(func, *params)
                return str(result) if result else "", skill_name, skill_name in DATA_SKILLS
            except Exception as e:
                import traceback
                logger.error(f"Skill '{skill_name}' failed: {e}\n{traceback.format_exc()}")
                return f"Error executing {skill_name}: {e}", skill_name, False

        # Run all open/web skills at the same time
        if parallel_matches:
            parallel_results = await asyncio.gather(
                *[_run_single_match(m) for m in parallel_matches]
            )
            for res_str, sname, is_d in parallel_results:
                if res_str is not None:
                    results.append(res_str)
                    tts_results.append(_truncate_for_tts(res_str, sname))
                    executed_any = True
                    if is_d:
                        is_data = True

        # Run everything else serially (data/AI skills have side-effects)
        for match in serial_matches:
            skill_name = match.group(1).lower()
            params_str  = match.group(2) or ""
            params = self._parse_parameters(skill_name, params_str)

            if skill_name not in self.skills_registry:
                logger.warning(f"Unknown skill: {skill_name}")
                try:
                    from modules.skill_forge import get_skill_forge
                    get_skill_forge(self.config).record_unknown_skill(skill_name, user_request)
                except Exception as e:
                    logger.error(f"Failed to record unknown skill in SkillForge: {e}")
                continue

            try:
                logger.info(f"⚙️  Executing {skill_name}({params})")
                func = self.skills_registry[skill_name]
                if asyncio.iscoroutinefunction(func):
                    result = await func(*params)
                else:
                    # Run sync skill in a separate thread to keep the event loop non-blocking
                    result = await asyncio.to_thread(func, *params)
                result_str = str(result) if result else ""
                results.append(result_str)
                tts_results.append(_truncate_for_tts(result_str, skill_name))
                executed_any = True
                if skill_name in DATA_SKILLS:
                    is_data = True
            except Exception as e:
                import traceback
                logger.error(f"Skill '{skill_name}' failed: {e}\n{traceback.format_exc()}")
                results.append(f"Error executing {skill_name}: {e}")

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

    def _skill_time_now(self) -> str:
        now = datetime.now()
        return now.strftime("Time: %H:%M")

    def _skill_date_today(self) -> str:
        today = datetime.now()
        return today.strftime("Date: %Y-%m-%d")

    def _skill_sysinfo(self, detail: str = "all") -> str:
        from modules.sysinfo import get_system_info
        return get_system_info(detail)

    def _skill_top_processes(self, n: str = "5") -> str:
        from modules.sysinfo import get_top_processes
        return get_top_processes(int(n) if n.isdigit() else 5)

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
        return self.file_manager.list_files(*args)

    def _skill_read_file(self, *args):        
        return self.file_manager.read_file(*args)

    def _skill_edit_file(self, *args):        
        return self.file_manager.edit_file(*args)

    async def _skill_search_files(self, *args):     
        return await asyncio.to_thread(self.file_manager.search_files, *args)

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
        Executes a deep autonomous research on a given topic using the specified AI platform.
        Usage from intent: [SKILL:deep_research:Black Holes:gemini]
        """
        if not topic:
            return "Please provide a topic for research."

        topic = topic.strip()
        ai_platform = ai_platform.strip().lower()
        
        try:
            from modules.ai_orchestrator.research_agent import DeepResearchAgent
            
            logger = logging.getLogger("MAX.SKILLS.DEEP_RESEARCH")
            logger.info(f"Triggering Deep Research Agent for topic: '{topic}' via {ai_platform}")
            
            # Use existing class config instead of creating a new one
            research_agent = DeepResearchAgent(self.config)
            
            # Run the autonomous process
            research_agent.run_research(topic=topic, urls_to_crawl=[], ai_platform=ai_platform)
            
            # Final TTS success message
            success_message = f"Sir, the deep research on {topic} is complete. The formatted report has been generated and saved to the Jarvis Generated Research folder on your desktop."
            return success_message

        except Exception as e:
            logger.error(f"Deep Research Skill Failed: {e}")
            return f"Sorry sir, I encountered an error while researching {topic}. Please check the system logs."
    
    
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
                if bbox:
                    img = ImageGrab.grab(bbox=bbox, all_screens=True).convert('RGB')
                else:
                    img = ImageGrab.grab(all_screens=True).convert('RGB')
                img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                img.save(str(path), quality=70, optimize=True)

            await asyncio.to_thread(_capture)
            
            from modules.llm import analyze_image_with_prompt
            return await analyze_image_with_prompt(
                str(path),
                f"Describe what's visible on the '{target or 'screen'}'. Read URLs and text."
            )
        except ImportError:
            return "Pillow needed: pip install pillow"
        except Exception as e:
            import traceback
            logger.error(f"Screenshot Error: {e}\n{traceback.format_exc()}")
            return f"Screen read failed: {e}"

    def _skill_list_windows(self, *args) -> str:
        try:
            import pygetwindow as gw
            titles = [t for t in gw.getAllTitles() if t.strip()]
            return "Open windows: " + ", ".join(titles[:10]) if titles else "No active windows."
        except ImportError:
            return "pygetwindow needed"

    async def _skill_screenshot(self, filename: str = "", **kw) -> str:
        try:
            from PIL import ImageGrab
            sd = Path(self.config.DATA_DIR) / "screenshots"
            sd.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = sd / f"{filename.strip() or 'max_screenshot'}_{ts}.png"
            await asyncio.to_thread(pyautogui.screenshot, str(fp))
            return f"Screenshot saved: {fp.name}"
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
        "chrome": "start opera", "google chrome": "start chrome", "firefox": "start firefox",
        "edge": "start msedge", "brave": "start brave", "opera": "start opera",
        "browser": "start opera", "default browser": "start opera",
        "my browser": "start opera", "web browser": "start opera",
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
            app_name = app.strip()
            if not app_name: continue
            
            app_lower = app_name.lower()
            success = False

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
        
    def _skill_whatsapp_message(self, contact: str = "", message: str = "", **kw) -> str:
        if not PYAUTOGUI_AVAILABLE: 
            return "Typing needs: pip install pyautogui"
        if not contact: 
            return "Provide a contact name or number."
        if not message: 
            return "What message should I send?"
            
        contact_clean = contact.strip().lower()
        
        # Check if the input is a direct phone number (contains digits)
        is_number = bool(re.match(r'^[\+\d\s\-]+$', contact_clean))
        
        if not is_number:
            # It's a name! Let's look it up in contacts.json
            contacts_file = Path(self.config.DATA_DIR) / "contacts.json"
            
            if contacts_file.exists():
                try:
                    contacts_dict = json.loads(contacts_file.read_text(encoding='utf-8'))
                    # Dictionary lookup (case-insensitive)
                    resolved_number = contacts_dict.get(contact_clean)
                    
                    if resolved_number:
                        contact = resolved_number
                    else:
                        return f"Sir, I don't have '{contact.title()}' saved in my contacts. Please update the contacts file."
                except Exception as e:
                    logger.error(f"Failed to read contacts JSON: {e}")
                    return "There is an error in reading the contacts file."
            else:
                # Create a template file if it doesn't exist
                template = {
                    "aditya": "+919022306582",
                    "papa": "+919022306582",
                    "me": "+919022306582"
                }
                contacts_file.write_text(json.dumps(template, indent=4), encoding='utf-8')
                return f"Sir, the contacts file was missing so I created one. Please add '{contact.title()}'s number to it."

        # Cleanup the number format before sending
        contact = contact.replace(" ", "").replace("-", "")
        if not contact.startswith("+"): 
            # Defaulting to India (+91) if user just says a 10-digit number
            contact = "+91" + contact 
            
        try:
            logger.info(f"Sending WhatsApp message to {contact}...")
            
            import webbrowser
            from urllib.parse import quote
            
            url = f"https://web.whatsapp.com/send?phone={contact}&text={quote(message)}"
            logger.info(f"Opening browser: {url}")
            webbrowser.open(url)
            
            # Wait for WhatsApp Web page to load (default 15 seconds)
            wait_time = 15
            logger.info(f"Waiting {wait_time} seconds for page to load...")
            time.sleep(wait_time)
            
            # Try focusing browser/whatsapp window
            try:
                import pygetwindow as gw
                windows = [w for w in gw.getAllWindows() if "whatsapp" in w.title.lower()]
                if not windows:
                    browser_keywords = ["chrome", "edge", "opera", "firefox", "brave", "browser"]
                    windows = [w for w in gw.getAllWindows() if any(kw in w.title.lower() for kw in browser_keywords)]
                if windows:
                    logger.info(f"Bringing window to focus: {windows[0].title}")
                    windows[0].activate()
                    time.sleep(0.5)
            except Exception as win_err:
                logger.warning(f"Could not focus browser window: {win_err}")
                
            # Press enter to send the message
            import pyautogui
            logger.info("Pressing Enter to send message...")
            pyautogui.press("enter")
            
            # Wait a few seconds before closing to let message send (increased from 3 to 5 seconds)
            close_time = 5
            logger.info(f"Waiting {close_time} seconds for transmission...")
            time.sleep(close_time)
            
            logger.info("Closing WhatsApp Web tab...")
            pyautogui.hotkey("ctrl", "w")
            
            return f"WhatsApp message successfully sent to {contact_clean.title()}."
        except Exception as e:
            return f"WhatsApp failed: {e}"

    def _skill_whatsapp_screenshot(self, contact: str = "", **kw) -> str:
        if not PYAUTOGUI_AVAILABLE: 
            return "Typing needs: pip install pyautogui"
        if not contact: 
            return "Provide a contact name or number."
            
        contact_clean = contact.strip().lower()
        is_number = bool(re.match(r'^[\+\d\s\-]+$', contact_clean))
        
        if not is_number:
            contacts_file = Path(self.config.DATA_DIR) / "contacts.json"
            if contacts_file.exists():
                try:
                    contacts_dict = json.loads(contacts_file.read_text(encoding='utf-8'))
                    resolved_number = contacts_dict.get(contact_clean)
                    if resolved_number:
                        contact = resolved_number
                    else:
                        return f"Sir, I don't have '{contact.title()}' saved in my contacts."
                except Exception as e:
                    logger.error(f"Failed to read contacts JSON: {e}")
                    return "There is an error in reading the contacts file."
            else:
                return "Contacts file missing."

        contact = contact.replace(" ", "").replace("-", "")
        if not contact.startswith("+"): 
            contact = "+91" + contact 
            
        try:
            logger.info(f"Taking screenshot for WhatsApp to {contact}...")
            from PIL import ImageGrab
            import subprocess
            ss_dir = Path(self.config.DATA_DIR) / "screenshots"
            ss_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = ss_dir / f"whatsapp_ss_{ts}.png"
            ImageGrab.grab(all_screens=True).save(str(fp))
            
            ps_cmd = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{str(fp.absolute())}'))"
            subprocess.run(["powershell", "-command", ps_cmd])
            
            import webbrowser
            url = f"https://web.whatsapp.com/send?phone={contact}"
            logger.info(f"Opening browser: {url}")
            webbrowser.open(url)
            
            time.sleep(15)
            
            try:
                import pygetwindow as gw
                windows = [w for w in gw.getAllWindows() if "whatsapp" in w.title.lower()]
                if not windows:
                    browser_keywords = ["chrome", "edge", "opera", "firefox", "brave", "browser"]
                    windows = [w for w in gw.getAllWindows() if any(kw in w.title.lower() for kw in browser_keywords)]
                if windows:
                    windows[0].activate()
                    time.sleep(0.5)
            except Exception as win_err:
                pass
                
            import pyautogui
            logger.info("Pasting image...")
            pyautogui.hotkey("ctrl", "v")
            time.sleep(2)
            logger.info("Pressing Enter to send message...")
            pyautogui.press("enter")
            
            time.sleep(5)
            logger.info("Closing WhatsApp Web tab...")
            pyautogui.hotkey("ctrl", "w")
            
            return f"Screenshot successfully sent to {contact_clean.title()} on WhatsApp."
        except Exception as e:
            return f"WhatsApp screenshot failed: {e}"

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


# ══════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════

_skills_instance: Optional[SkillsEngine] = None

def get_skills_engine(config) -> SkillsEngine:
    global _skills_instance
    if _skills_instance is None:
        _skills_instance = SkillsEngine(config)
    return _skills_instance