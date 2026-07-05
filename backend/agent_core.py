# Path: backend/agent_core.py
# Use: Core execution manager for custom plugins and agents.
# agent_core.py — MAX v4.5 (Ghost Mode + Full Trackers - Uncompacted)

import asyncio
import logging
import re
from typing import Dict, Any, List, Optional
from config import config
from modules.llm import get_response, get_response_with_skill_result, get_greeting, get_acknowledgment
from modules.skills import get_skills_engine
from modules.memory import get_memory_manager
from modules.tts import generate_tts, generate_tts_paced
from modules.gatekeeper import get_gatekeeper
from modules.Intent_engine import get_intent_engine
from modules.listening_manager import ListeningManager
from modules.agent_loop import get_agent_loop, is_complex_goal
from modules.orchestrator import (
    DIRECT, NEEDS_APPROVAL, approval_reply_kind, cancel_task, classify_complexity,
    get_status_summary, pop_pending_approval, remember_pending_approval, start_orchestrator_background,
)

logger = logging.getLogger("MAX.AGENT")

# Global WebSocket reference set by main.py at startup
_active_websocket = None
_main_event_loop = None

def set_websocket_globals(websocket, loop):
    """Called by main.py on WebSocket connection to set globals."""
    global _active_websocket, _main_event_loop
    _active_websocket = websocket
    _main_event_loop = loop

def get_active_websocket():
    return _active_websocket

def get_main_loop():
    return _main_event_loop


def _force_open_app_skill(text: str) -> Optional[str]:
    """
    Deterministic fallback for missed open-app skill tags.
    Only runs when IntentEngine classifies as COMMAND.
    """
    text_lower = text.strip().lower()
    patterns = [
        r"(?:\bopen\b|\bkhol(?:o|na|do|de)?\b|\blaunch\b|\bstart\b)\s+([a-zA-Z0-9 ._+\-'\"]{2,40})",
        r"([a-zA-Z0-9 ._+\-'\"]{2,40})\s+(?:\bopen\s+kar(?:o|de)?|\bkhol(?:o|na|do|de)?|\blaunch\s+kar(?:o|de)?)",
        r"(?:open|khol|launch)\s+(?:the\s+)?(?:app\s+)?([a-zA-Z0-9 ._+\-'\"]{2,40})",
    ]
    for pat in patterns:
        m = re.search(pat, text_lower, re.IGNORECASE)
        if m:
            app_name = m.group(1).strip(" .,!?\"'").strip()
            if app_name and len(app_name) > 1:
                non_apps = {"it", "this", "that", "them", "me", "us", "him", "her", "something", "anything", "everything", "nothing"}
                if app_name.lower() in ["screen recording", "recording", "screen record", "screen capture"]:
                    return "[SKILL:screen_record]"
                if app_name.lower() not in non_apps:
                    return f"[SKILL:open_app:{app_name}]"
    return None


_agent_instance = None

class MaxAgent:

    def __init__(self):
        global _agent_instance
        _agent_instance = self
        
        self.config = config
        self.memory = get_memory_manager(config)
        self.skills = get_skills_engine(config)
        self.gatekeeper = get_gatekeeper()
        self.intent_engine = get_intent_engine(config)
        self.listening_manager = ListeningManager()
        
        # 👻 GHOST MODE INITIALIZED CORRECTLY (NOT COMMENTED OUT)
        self.ghost_mode = False
        self.typing_mode = False

        
        # Real-time reminder scheduler
        try:
            from modules.reminder_scheduler import get_scheduler
            get_scheduler(config).start()
            logger.info("Reminder scheduler started")
        except Exception as e:
            logger.debug(f"Reminder scheduler not available: {e}")

    async def _send_ack_via_websocket(self, ack_text: str, use_tts: bool):
        global _active_websocket, _main_event_loop
        if not ack_text or not ack_text.strip():
            return
        try:
            ws = _active_websocket
            loop = _main_event_loop
            if not ws or not loop:
                return  
            
            try:
                # ONLY send audio for acknowledgement, do not print on screen.
                if use_tts:
                    import os
                    import base64
                    tts_path = await generate_tts(ack_text)
                    if tts_path and os.path.exists(tts_path):
                        with open(tts_path, "rb") as f:
                            encoded_audio = base64.b64encode(f.read()).decode('utf-8')
                            await ws.send_json({"event": "audio_response", "audio": encoded_audio})
                        try:
                            os.remove(tts_path)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Ack text/audio send failed: {e}")
                return  
        except Exception as e:
            logger.debug(f"Ack dispatch failed: {e}")

    async def _send_event_via_websocket(self, payload: dict):
        """Push an additive event (plan_update etc.) to the client. Never raises."""
        global _active_websocket
        ws = _active_websocket
        if not ws:
            return
        try:
            await ws.send_json(payload)
        except Exception as e:
            logger.debug(f"Event send failed: {e}")

    async def process_text_input(self, text: str, use_tts: bool = True, input_source: str = "unknown") -> Dict[str, Any]:
        print(f"\n🟢 [TRACKER: 1] Pipeline started! Input: '{text}' | Source: {input_source}")
        
        if not text or not text.strip():
            print("🔴 [TRACKER: END] Text is empty.")
            return {"response": "", "tts_path": "", "skill_used": None, "intent": "empty"}
        
        try:
            # 🚨 1. GHOST MODE INTERACTION BYPASS
            print("🟢 [TRACKER: 2] Checking Ghost Mode...")
            ghost_result = await self.process_ghost_mode_test(text)
            if ghost_result is not None:
                print(f"🟢 [TRACKER: 3] Ghost Mode Triggered! Result: {ghost_result}")
                if use_tts:
                    tts_path = await generate_tts(ghost_result["response"])
                    ghost_result["tts_path"] = tts_path
                else:
                    ghost_result["tts_path"] = ""
                ghost_result["intent"] = "ghost_mode"
                return ghost_result

            # Step 1: Listening Manager (ONLY FOR VOICE)
            print(f"🟢 [TRACKER: 4] Source is {input_source}. Checking ListeningManager...")
            if input_source == "voice":
                lm_result = self.listening_manager.process_transcript(text)
                action = lm_result.get("action")
                
                if action == "ignore":
                    print("🔴 [SILENT KILLER] ListeningManager dropped it (Missing wake word / background noise).")
                    return {"response": "", "tts_path": "", "skill_used": None, "intent": "ignored"}
                    
                if action == "reserved":
                    cmd = lm_result.get("command", "")
                    if cmd in ["stop listening", "sunna band karo", "cancel", "abort", "emergency stop"]:
                        self.listening_manager.continuous_mode = False
                    elif cmd in ["start listening", "sunna shuru karo"]:
                        self.listening_manager.continuous_mode = True
                    print(f"🟢 [TRACKER] Reserved command triggered: {cmd}")
                    return {"response": f"Reserved command triggered: {cmd}", "tts_path": "", "skill_used": f"reserved:{cmd}", "intent": "reserved"}
                    
                if action == "reply":
                    resp_text = lm_result.get("response", "")
                    tts_path = ""
                    if use_tts and resp_text:
                        tts_path = await generate_tts(resp_text)
                    print(f"🟢 [TRACKER] Quick reply triggered: {resp_text}")
                    return {"response": resp_text, "tts_path": tts_path, "skill_used": None, "intent": "reply"}

                if action == "execute":
                    skill_tag = lm_result.get("skill_tag")
                    print(f"🟢 [TRACKER] Fast Brain Execute -> {skill_tag}")
                    
                    try:
                        fast_ack = await asyncio.wait_for(get_acknowledgment(text), timeout=1.0)
                        if fast_ack:
                            asyncio.create_task(self._send_ack_via_websocket(fast_ack, False))
                    except Exception:
                        pass
                        
                    memory_context = self.memory.get_context()
                    skill_result = await self.skills.parse_and_execute(skill_tag, memory_context, text)
                    if skill_result.get("executed"):
                        final_response = skill_result.get("result", "").strip() or "Done."
                    else:
                        error = skill_result.get("error", "Skill failed")
                        final_response = f"Could not execute. Error: {error}"
                    tts_path = ""
                    if use_tts and final_response:
                        tts_path = await generate_tts(self.gatekeeper.filter_for_tts(final_response))
                        print(f"🟢 [TRACKER: FAST-9] Audio Generated! Path: {tts_path}")
                    return {"response": final_response, "tts_path": tts_path, "skill_used": skill_tag, "intent": "fast_brain"}

                # Resolve text
                text = lm_result.get("resolved_text", text)

            print("🟢 [TRACKER: 5] Adding to memory & Fact Extraction...")
            await self.memory.add_message("user", text)
            try:
                await self.memory.extract_and_store_facts(text)
            except Exception:
                pass

            memory_context = self.memory.get_context()

            print("🟢 [TRACKER: 6] Getting KB Context...")
            kb_prefix = ""
            try:
                from modules.knowledge_base import get_knowledge_base
                kb_ctx = await asyncio.to_thread(get_knowledge_base(self.config).query, text, top_k=3, min_similarity=0.30)
                if kb_ctx:
                    kb_prefix = kb_ctx + "\n\n"
            except Exception:
                pass
            combined_context = kb_prefix + memory_context

            # 🧠 ORCHESTRATOR APPROVAL GATE — after Ghost Mode, before normal LLM routing.
            lowered = text.lower().strip()
            if any(p in lowered for p in ["status", "what's the status", "what is the status", "status kya", "kya chal raha", "what is happening"]):
                status_text = get_status_summary()
                if use_tts and status_text:
                    tts_path = await generate_tts(self.gatekeeper.filter_for_tts(status_text))
                else:
                    tts_path = ""
                return {"response": status_text, "tts_path": tts_path, "skill_used": "orchestrator_status", "intent": "orchestrator_status"}

            if any(p in lowered for p in ["stop the task", "cancel the research", "cancel task", "abort task"]):
                cancel_text = cancel_task()
                tts_path = await generate_tts(cancel_text) if use_tts else ""
                return {"response": cancel_text, "tts_path": tts_path, "skill_used": "orchestrator_cancel", "intent": "orchestrator_cancel"}

            pending = pop_pending_approval()
            if pending:
                approval = approval_reply_kind(text)
                if approval == "yes":
                    async def _orchestrator_done_notify(msg: str):
                        """Called by orchestrator when background task completes."""
                        try:
                            audio = await generate_tts(self.gatekeeper.filter_for_tts(msg))
                            if audio:
                                print(f"🔔 [ORCHESTRATOR NOTIFY] {msg}")
                                # Audio will be picked up by the frontend via the normal TTS path
                        except Exception as e:
                            print(f"🔴 [ORCHESTRATOR NOTIFY ERROR] {e}")

                    task_id = start_orchestrator_background(
                        pending.get("query", text),
                        pending.get("context", combined_context),
                        notify_callback=_orchestrator_done_notify,
                    )
                    response = f"Deep Orchestrator started. Task ID: {task_id}. You can ask for status anytime."
                    tts_response = "Deep Orchestrator started. You can ask for status anytime."
                    tts_path = await generate_tts(self.gatekeeper.filter_for_tts(tts_response)) if use_tts else ""
                    return {"response": response, "tts_path": tts_path, "skill_used": "orchestrator", "intent": "orchestrator_started", "task_id": task_id}
                if approval == "no":
                    text = pending.get("query", text)
                    combined_context = pending.get("context", combined_context)
                else:
                    remember_pending_approval(pending.get("query", text), pending.get("context", combined_context))
                    response = "Choose normal mode or deep Orchestrator mode for that task."
                    tts_path = await generate_tts(response) if use_tts else ""
                    return {"response": response, "tts_path": tts_path, "skill_used": None, "intent": "orchestrator_approval"}

            complexity = await classify_complexity(text, combined_context)
            if complexity == NEEDS_APPROVAL:
                remember_pending_approval(text, combined_context)
                response = "This looks like a deep task. Should MAX use normal mode or go deep with the Orchestrator? Deep mode is more thorough and takes longer."
                tts_path = await generate_tts(self.gatekeeper.filter_for_tts(response)) if use_tts else ""
                return {"response": response, "tts_path": tts_path, "skill_used": None, "intent": "orchestrator_approval"}

            print("🟢 [TRACKER: 7] Checking Intent...")
            intent = await self.intent_engine.classify(text)
            allow_skills = intent.should_execute_skill

            # 🤖 AGENT LOOP — multi-step goals get planned & executed autonomously
            if allow_skills and is_complex_goal(text):
                print("🟢 [TRACKER: 7.5] Complex goal detected → Agent Loop engaged.")
                try:
                    try:
                        ack = await asyncio.wait_for(get_acknowledgment(text), timeout=1.0)
                        if ack:
                            asyncio.create_task(self._send_ack_via_websocket(ack, use_tts))
                    except Exception:
                        pass

                    loop_result = await get_agent_loop(self.config, self.skills).run(
                        text, combined_context, self._send_event_via_websocket
                    )
                    final_response = self.gatekeeper.filter(loop_result["response"])
                    await self.memory.add_message("assistant", final_response)
                    await self.memory.save_memory()

                    tts_path = ""
                    if use_tts and final_response:
                        try:
                            tts_text = self.gatekeeper.filter_for_tts(final_response)
                            tts_path = await asyncio.wait_for(generate_tts(tts_text), timeout=15.0)
                        except Exception as e:
                            print(f"🔴 [TRACKER: ERROR] TTS Crashed: {e}")

                    print("🟢 [TRACKER: 7.9] Agent Loop complete. Returning to main.")
                    return {
                        "response": final_response,
                        "tts_path": tts_path,
                        "skill_used": loop_result.get("skills_used"),
                        "intent": "agent_loop",
                    }
                except Exception as e:
                    logger.error(f"Agent loop failed — falling back to single-shot: {e}", exc_info=True)
                    print(f"🔴 [TRACKER: 7.5 ERROR] Agent Loop failed ({e}). Using normal path.")

            print("🟢 [TRACKER: 8] Acknowledgment & LLM Call...")
            ack_task = None
            if allow_skills:  
                try:
                    ack = await asyncio.wait_for(get_acknowledgment(text), timeout=3.0)
                    if ack:
                        ack_task = asyncio.create_task(self._send_ack_via_websocket(ack, use_tts))
                except Exception:
                    pass

            result = await get_response(text, combined_context, allow_skills=allow_skills)
            llm_response = result["response"]
            skill_tag = result.get("skill") if allow_skills else None
            print(f"🟢 [TRACKER: 9] LLM Replied. Skill: {skill_tag}")

            if allow_skills and not skill_tag:
                skill_tag = _force_open_app_skill(text)

            final_response = llm_response
            if skill_tag:
                print(f"🟢 [TRACKER: 10] Executing Skill: {skill_tag}")
                skill_result = await self.skills.parse_and_execute(skill_tag, combined_context, text)
                if skill_result.get("executed"):
                    skill_output = skill_result.get("result", "").strip()
                    
                    skill_failed = False
                    fail_indicators = ["could not find", "failed", "error", "not found", "not installed", "needed:", "missing", "unable to", "cannot", "does not exist", "no such"]
                    if skill_output:
                        lower_output = skill_output.lower()
                        skill_failed = any(ind in lower_output for ind in fail_indicators)
                    
                    if skill_result.get("is_data_skill"):
                        summary = await get_response_with_skill_result(text, skill_output, combined_context)
                        final_response = summary["response"]
                        await self.memory.update_personality(len(final_response), skill_result.get("skill_name", ""))
                    elif skill_failed:
                        final_response = skill_output
                    else:
                        final_response = skill_output or llm_response
                else:
                    error = skill_result.get("error", "Skill failed")
                    final_response = f"{llm_response} (Error: {error[:60]})"
            else:
                await self.memory.update_personality(len(final_response), "")

            filtered = self.gatekeeper.filter(final_response)
            print("🟢 [TRACKER: 11] Filtering done. Text ready for TTS.")

            try:
                from modules.skill_forge import get_skill_forge
                get_skill_forge(self.config).record_gap(text, filtered)
            except Exception:
                pass

            await self.memory.add_message("assistant", filtered)
            await self.memory.save_memory()

            tts_path = ""
            if use_tts and filtered:
                print("🟢 [TRACKER: 12] Generating Audio from Kokoro...")
                try:
                    tts_text = self.gatekeeper.filter_for_tts(filtered)
                    tts_path = await asyncio.wait_for(generate_tts(tts_text), timeout=15.0)
                    print(f"🟢 [TRACKER: 13] Audio Generated! Path: {tts_path}")
                except Exception as e:
                    print(f"🔴 [TRACKER: ERROR] TTS Crashed: {e}")

            if ack_task and not ack_task.done():
                try:
                    await asyncio.wait_for(ack_task, timeout=2.0)
                except Exception:
                    pass

            print("🟢 [TRACKER: 14] Pipeline Complete. Returning to main.")
            return {
                "response": filtered,
                "tts_path": tts_path,
                "skill_used": skill_tag,
                "intent": intent.type.value,
            }

        except Exception as e:
            print(f"🔴 [FATAL ERROR] process_text_input crashed: {e}")
            logger.error(f"process_text_input error: {e}", exc_info=True)
            return {
                "response": "Something went wrong. Try again?",
                "tts_path": "",
                "skill_used": None,
                "intent": "error"
            }

    async def get_greeting(self) -> str:
        greeting = self.gatekeeper.filter(await get_greeting())
        try:
            await self.memory.update_user_fact("last_greeting", greeting)
        except Exception:
            pass
        return greeting

    async def clear_memory(self) -> str:
        try:
            success = await self.memory.clear_memory()
            return "Memory cleared." if success else "Could not clear memory."
        except Exception as e:
            logger.error(f"Memory clear failed: {e}")
            return f"Error clearing memory: {str(e)}"
    
    async def process_ghost_mode_test(self, user_text: str) -> Optional[dict]:
        import pyautogui
        import re
        pyautogui.FAILSAFE = False

        # Clean STT artifacts: remove punctuation, lowercase, strip
        text_clean = re.sub(r'[^\w\s]', '', user_text.lower()).strip()

        # ═══════════════════════════════════════════════════════
        # 1. DEACTIVATION — MUST be checked BEFORE activation
        #    ("deactivate ghost mode" contains "activate ghost mode" as substring)
        # ═══════════════════════════════════════════════════════
        deactivation_phrases = [
            "exit ghost mode", "terminate protocol", "ghost mode off",
            "stop ghost mode", "disable ghost mode", "deactivate ghost mode",
            "ghost mode band karo", "ghost mode hatao",
        ]
        if self.ghost_mode and any(phrase in text_clean for phrase in deactivation_phrases):
            self.ghost_mode = False
            self.typing_mode = False
            logger.info("🚫 Ghost Mode Deactivated")
            return {"response": "Ghost mode deactivated.", "skill_used": "ghost_deactivate"}

        # ═══════════════════════════════════════════════════════
        # 2. ACTIVATION
        # ═══════════════════════════════════════════════════════
        activation_phrases = [
            "activate ghost mode", "ghost mode on",
            "start ghost mode", "enable ghost mode",
        ]
        if any(phrase in text_clean for phrase in activation_phrases):
            self.ghost_mode = True
            logger.info("👻 Ghost Mode Activated")
            return {"response": "Ghost mode active.", "skill_used": "ghost_activate"}

        # Not in ghost mode → skip entirely
        if not self.ghost_mode:
            return None

        # ═══════════════════════════════════════════════════════
        # TYPING MODE TOGGLES
        # ═══════════════════════════════════════════════════════
        if any(cmd in text_clean for cmd in ["start typing", "typing shuru karo", "typing on", "start dictation"]):
            self.typing_mode = True
            logger.info("⌨️ Typing Mode Activated")
            return {"response": "", "skill_used": "typing_start"}

        if any(cmd in text_clean for cmd in ["stop typing", "typing band karo", "typing off", "stop dictation"]):
            self.typing_mode = False
            logger.info("🚫 Typing Mode Deactivated")
            return {"response": "", "skill_used": "typing_stop"}

        # ═══════════════════════════════════════════════════════
        # SECTION 15 STEP 0: STOP COMMAND (clears vision engine state)
        # Per plan: check BEFORE anything else in active ghost mode
        # ═══════════════════════════════════════════════════════
        stop_phrases = ["stop", "ruk", "band kar", "cancel"]
        if any(phrase in text_clean for phrase in stop_phrases):
            if self.vision_engine is not None:
                self.vision_engine.stop_requested = True
                self.vision_engine.pending_approval = None
            return {"response": "Ruk gaya.", "skill_used": "stop"}

        # ═══════════════════════════════════════════════════════
        # SECTION 15 STEP 0.5: PENDING APPROVAL CHECK
        # If vision engine is waiting for haan/nahi, route there first
        # ═══════════════════════════════════════════════════════
        if self.vision_engine is not None and self.vision_engine.pending_approval is not None:
            result = await self.vision_engine.vision_click(user_text)
            return result

        # ═══════════════════════════════════════════════════════
        # SECTION 15 STEP 1: VISION CLICK TRIGGERS
        # Check BEFORE existing shortcut matching (per plan Section 15)
        # ═══════════════════════════════════════════════════════


        # ═══════════════════════════════════════════════════════
        # GHOST MODE IS ACTIVE — FULL HANDS-FREE CONTROL BELOW
        # ═══════════════════════════════════════════════════════
        try:
            # ─── HELPER: Release stuck modifiers ──────────────
            # Prevents bugs where global shortcuts leave Win/Ctrl/Alt stuck,
            # causing pyautogui.write to trigger OS shortcuts instead of typing text.
            def _release_modifiers():
                for mod in ['win', 'ctrl', 'alt', 'shift']:
                    pyautogui.keyUp(mod)

            # ─── REPEAT COUNT PARSER ──────────────────────────
            # Handles: "press enter 10 times", "backspace five times", "3 baar"
            _WORD_TO_NUM = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
            }
            def _get_repeat_count(text):
                # Try digit: "10 times", "5 baar"
                m = re.search(r'(\d+)\s*(?:times?|baar|bar)', text)
                if m:
                    return min(int(m.group(1)), 50)
                # Try digit standalone at end: "backspace 5"
                m = re.search(r'(\d+)\s*$', text)
                if m:
                    return min(int(m.group(1)), 50)
                # Try word number: "five times"
                for word, num in _WORD_TO_NUM.items():
                    if word in text:
                        return num
                return 1

            repeat = _get_repeat_count(text_clean)

            # ─── 3. KEY COMMANDS (silent, hardware-level) ─────
            # Enter
            if any(cmd in text_clean for cmd in ["press enter", "enter maro", "hit enter", "enter daba", "enter press karo"]):
                _release_modifiers()
                pyautogui.press('enter', presses=repeat, interval=0.02)
                return {"response": "", "skill_used": "key_enter"}

            # Tab
            if any(cmd in text_clean for cmd in ["press tab", "tab maro", "tab daba", "tab press karo"]):
                _release_modifiers()
                pyautogui.press('tab', presses=repeat, interval=0.02)
                return {"response": "", "skill_used": "key_tab"}

            # Backspace
            if any(cmd in text_clean for cmd in ["press backspace", "backspace maro", "backspace daba", "peeche jao", "backspace"]):
                _release_modifiers()
                pyautogui.press('backspace', presses=repeat, interval=0.02)
                return {"response": "", "skill_used": "key_backspace"}

            # Escape
            if any(cmd in text_clean for cmd in ["press escape", "escape", "escape daba", "cancel karo"]):
                pyautogui.press('escape')
                return {"response": "", "skill_used": "key_escape"}

            # Space
            if any(cmd in text_clean for cmd in ["press space", "space daba", "space maro"]):
                pyautogui.press('space')
                return {"response": "", "skill_used": "key_space"}

            # Delete
            if any(cmd in text_clean for cmd in ["press delete", "delete karo", "delete daba"]):
                pyautogui.press('delete')
                return {"response": "", "skill_used": "key_delete"}

            # Arrow keys
            if any(cmd in text_clean for cmd in ["arrow up", "upar jao", "up arrow"]):
                pyautogui.press('up')
                return {"response": "", "skill_used": "key_up"}
            if any(cmd in text_clean for cmd in ["arrow down", "neeche jao", "down arrow"]):
                pyautogui.press('down')
                return {"response": "", "skill_used": "key_down"}
            if any(cmd in text_clean for cmd in ["arrow left", "left arrow", "baayen jao"]):
                pyautogui.press('left')
                return {"response": "", "skill_used": "key_left"}
            if any(cmd in text_clean for cmd in ["arrow right", "right arrow", "daayen jao"]):
                pyautogui.press('right')
                return {"response": "", "skill_used": "key_right"}

            # ─── 4. HOTKEY COMBOS (silent) ────────────────────
            # Switch window (Alt+Tab)
            if any(cmd in text_clean for cmd in ["switch window", "window badlo", "alt tab", "dusri window"]):
                pyautogui.hotkey('alt', 'tab')
                return {"response": "", "skill_used": "hotkey_alt_tab"}

            # Minimize
            if any(cmd in text_clean for cmd in ["minimize app", "minimize window", "minimize karo", "minimize"]):
                pyautogui.hotkey('win', 'down')
                return {"response": "", "skill_used": "hotkey_minimize"}

            # Maximize
            if any(cmd in text_clean for cmd in ["maximize app", "maximize window", "maximize karo", "maximize", "full screen"]):
                pyautogui.hotkey('win', 'up')
                return {"response": "", "skill_used": "hotkey_maximize"}

            # Close app (Alt+F4)
            if any(cmd in text_clean for cmd in ["close app", "close window", "app band karo", "window band karo", "alt f4"]):
                pyautogui.hotkey('alt', 'F4')
                return {"response": "", "skill_used": "hotkey_close_app"}

            # Volume
            if any(cmd in text_clean for cmd in ["volume up", "aawaz badhao", "increase volume", "volume badha"]):
                pyautogui.press('volumeup')
                return {"response": "", "skill_used": "volume_up"}
            if any(cmd in text_clean for cmd in ["volume down", "aawaz kam karo", "decrease volume", "volume kam"]):
                pyautogui.press('volumedown')
                return {"response": "", "skill_used": "volume_down"}
            if any(cmd in text_clean for cmd in ["mute", "volume mute", "awaaz band karo", "sound off"]):
                pyautogui.press('volumemute')
                return {"response": "", "skill_used": "volume_mute"}

            # Browser tab control
            if any(cmd in text_clean for cmd in ["new tab", "naya tab", "naya tab kholo"]):
                pyautogui.hotkey('ctrl', 't')
                return {"response": "", "skill_used": "hotkey_new_tab"}
            if any(cmd in text_clean for cmd in ["close tab", "tab band karo", "tab close karo"]):
                pyautogui.hotkey('ctrl', 'w')
                return {"response": "", "skill_used": "hotkey_close_tab"}
            if any(cmd in text_clean for cmd in ["next tab", "agla tab", "tab switch karo"]):
                pyautogui.hotkey('ctrl', 'tab')
                return {"response": "", "skill_used": "hotkey_next_tab"}
            if any(cmd in text_clean for cmd in ["previous tab", "pichla tab", "pehle wala tab"]):
                pyautogui.hotkey('ctrl', 'shift', 'tab')
                return {"response": "", "skill_used": "hotkey_prev_tab"}

            # Clipboard & Edit shortcuts
            if any(cmd in text_clean for cmd in ["copy", "copy karo", "ctrl c"]):
                pyautogui.hotkey('ctrl', 'c')
                return {"response": "", "skill_used": "hotkey_copy"}
            if any(cmd in text_clean for cmd in ["paste", "paste karo", "ctrl v", "chipkao"]):
                pyautogui.hotkey('ctrl', 'v')
                return {"response": "", "skill_used": "hotkey_paste"}
            if any(cmd in text_clean for cmd in ["select all", "sab select karo", "ctrl a"]):
                pyautogui.hotkey('ctrl', 'a')
                return {"response": "", "skill_used": "hotkey_select_all"}
            if any(cmd in text_clean for cmd in ["undo karo", "ctrl z", "wapas karo"]):
                pyautogui.hotkey('ctrl', 'z')
                return {"response": "", "skill_used": "hotkey_undo"}
            if any(cmd in text_clean for cmd in ["redo karo", "ctrl y"]):
                pyautogui.hotkey('ctrl', 'y')
                return {"response": "", "skill_used": "hotkey_redo"}
            if any(cmd in text_clean for cmd in ["save karo", "save", "ctrl s", "file save"]):
                pyautogui.hotkey('ctrl', 's')
                return {"response": "", "skill_used": "hotkey_save"}
            if any(cmd in text_clean for cmd in ["find", "search karo", "kuch dhundna", "ctrl f"]):
                pyautogui.hotkey('ctrl', 'f')
                return {"response": "", "skill_used": "hotkey_find"}
            if any(cmd in text_clean for cmd in ["print karo", "print this", "ctrl p"]):
                pyautogui.hotkey('ctrl', 'p')
                return {"response": "", "skill_used": "hotkey_print"}
            if any(cmd in text_clean for cmd in ["zoom in", "bada karo", "zoom badhao"]):
                pyautogui.hotkey('ctrl', '+')
                return {"response": "", "skill_used": "hotkey_zoom_in"}
            if any(cmd in text_clean for cmd in ["zoom out", "chota karo", "zoom kam karo"]):
                pyautogui.hotkey('ctrl', '-')
                return {"response": "", "skill_used": "hotkey_zoom_out"}

            # Scroll
            if any(cmd in text_clean for cmd in ["scroll up", "upar scroll", "page up"]):
                pyautogui.scroll(5)
                return {"response": "", "skill_used": "scroll_up"}
            if any(cmd in text_clean for cmd in ["scroll down", "neeche scroll", "page down"]):
                pyautogui.scroll(-5)
                return {"response": "", "skill_used": "scroll_down"}

            # ─── 5. 👁️ VISION TRIGGERS ───────────────────────
            vision_triggers = [
                "screen pe kya hai", "screen par kya hai", "kya dikh raha hai",
                "what is on my screen", "whats on my screen", "what do you see",
                "screen padho", "screen read karo", "read my screen",
                "ye kya hai", "what is this", "screen dekho", "look at my screen",
                "screen batao", "tell me whats on screen", "describe my screen",
                "screen me kya chal raha", "kya open hai", "whats happening on screen",
                "kya chal raha hai", "check my screen",
                "see my screen", "can you see my screen", "meri screen dekho",
            ]
            if any(trigger in text_clean for trigger in vision_triggers):
                try:
                    from modules.context_engine import get_context_engine
                    ctx = get_context_engine()
                    result = await ctx.get_full_context(user_query=user_text)
                    vision_text = result.get("vision_response", "")
                    if vision_text:
                        return {"response": vision_text, "skill_used": "ghost_vision"}
                    else:
                        return {"response": "Could not read your screen right now.", "skill_used": "ghost_vision_fail"}
                except Exception as e:
                    logger.error(f"Ghost Vision failed: {e}")
                    return {"response": f"Vision error: {e}", "skill_used": "ghost_vision_error"}

            # ─── 6. SKILL COMMANDS (delegate to skills engine) ──

            # 6a. Open app: "open notepad", "chrome kholo", "launch excel"
            open_match = re.search(
                r"\b(?:open|khol(?:o|na|do|de)?|launch|start|chalu\s*kar(?:o|do)?)\s+(.+)",
                text_clean,
            )
            if open_match:
                app_name = open_match.group(1).strip()
                # Filter out non-app words
                non_apps = {"it", "this", "that", "them", "me", "something", "anything", "karo", "do"}
                if app_name and len(app_name) > 1 and app_name not in non_apps:
                    if app_name.lower() in ["screen recording", "recording", "screen record", "screen capture"]:
                        skill_tag = "[SKILL:screen_record]"
                    else:
                        skill_tag = f"[SKILL:open_app:{app_name}]"
                    try:
                        memory_context = self.memory.get_context()
                        skill_result = await self.skills.parse_and_execute(skill_tag, memory_context, user_text)
                        if skill_result.get("executed"):
                            return {"response": "", "skill_used": f"ghost_open:{app_name}"}
                        else:
                            error = skill_result.get("error", "")
                            return {"response": f"Could not open {app_name}. {error}", "skill_used": "ghost_open_fail"}
                    except Exception as e:
                        logger.error(f"Ghost open_app failed: {e}")
                        return {"response": f"Could not open {app_name}.", "skill_used": "ghost_open_fail"}

            # 6b. Play on YouTube: "play lofi music", "ye gaana chalao"
            play_match = re.search(
                r"\b(?:play|baja(?:o)?|chala(?:o)?|youtube\s*pe\s*chala(?:o)?)\s+(.+)",
                text_clean,
            )
            if play_match:
                query = play_match.group(1).strip()
                # Remove trailing "on youtube" / "youtube pe"
                query = re.sub(r"\s*(?:on\s+youtube|youtube\s*pe)\s*$", "", query).strip()
                if query and len(query) > 1:
                    skill_tag = f"[SKILL:youtube_play:{query}]"
                    try:
                        memory_context = self.memory.get_context()
                        skill_result = await self.skills.parse_and_execute(skill_tag, memory_context, user_text)
                        if skill_result.get("executed"):
                            return {"response": "", "skill_used": f"ghost_play:{query}"}
                    except Exception as e:
                        logger.error(f"Ghost youtube_play failed: {e}")

            # 6c. Search: "search python tutorials", "google machine learning"
            search_match = re.search(
                r"\b(?:search|google|look\s*up|dhoondh(?:o)?|khoj(?:o)?)\s+(.+)",
                text_clean,
            )
            if search_match:
                query = search_match.group(1).strip()
                if query and len(query) > 1:
                    skill_tag = f"[SKILL:search:{query}]"
                    try:
                        memory_context = self.memory.get_context()
                        skill_result = await self.skills.parse_and_execute(skill_tag, memory_context, user_text)
                        if skill_result.get("executed"):
                            result_text = skill_result.get("result", "")
                            return {"response": result_text, "skill_used": f"ghost_search:{query}"}
                    except Exception as e:
                        logger.error(f"Ghost search failed: {e}")

            # 6d. Open website: "go to youtube", "github pe jao"
            web_match = re.search(
                r"\b(?:go\s*to|visit|navigate\s*to|pe\s*jao|jao)\s+(.+)",
                text_clean,
            )
            if web_match:
                site = web_match.group(1).strip()
                if site and len(site) > 1:
                    skill_tag = f"[SKILL:web_open:{site}]"
                    try:
                        memory_context = self.memory.get_context()
                        skill_result = await self.skills.parse_and_execute(skill_tag, memory_context, user_text)
                        if skill_result.get("executed"):
                            return {"response": "", "skill_used": f"ghost_web:{site}"}
                    except Exception as e:
                        logger.error(f"Ghost web_open failed: {e}")

            # ─── 7. EXPLICIT DICTATION ("type X" / "likh X") ──
            type_match = re.search(
                r"\b(?:type|likh(?:o)?|likho|write)\s+(.+)",
                text_clean,
            )
            if type_match:
                # Use original text to preserve casing
                orig_match = re.search(r"(?:type|likh(?:o)?|likho|write)\s+(.+)", user_text, re.IGNORECASE)
                if orig_match:
                    type_text = orig_match.group(1).strip()
                else:
                    type_text = type_match.group(1).strip()
                if type_text:
                    _release_modifiers()
                    import time
                    time.sleep(1.5)
                    pyautogui.write(type_text + " ", interval=0.01)
                    return {"response": "", "skill_used": "ghost_dictation"}

            # ─── 8. TYPING MODE FALLBACK OR BLOCK ─────────────
            if self.typing_mode:
                from modules.listening_manager import LocalFastBrain
                final_type_text = LocalFastBrain.strip_wake_word(user_text)
                if final_type_text:
                    _release_modifiers()
                    import time
                    time.sleep(1.5)
                    pyautogui.write(final_type_text + " ", interval=0.01)
                return {"response": "", "skill_used": "ghost_typing_mode"}
            else:
                logger.info(f"👻 Ghost Mode blocked non-command: '{text_clean[:50]}'")
                return {"response": "I'm in ghost mode right now. Say 'exit ghost mode' to chat with me.", "skill_used": "ghost_blocked"}

        except Exception as e:
            logger.error(f"Ghost Mode error: {e}")
            return {"response": "", "skill_used": "ghost_error"}

# Singleton
_agent: Optional[MaxAgent] = None

def get_agent() -> MaxAgent:
    global _agent
    if _agent is None:
        _agent = MaxAgent()
    return _agent