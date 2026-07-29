# Path: backend/modules/listening_manager.py
# Use: Coordinates mic recording states and wake words.
"""
listening_manager.py — MAX-AILE (v2 — Simplified)

Design Philosophy:
  The frontend VAD (Voice Activity Detection) is the noise filter.
  If audio made it here, a human spoke clearly enough to trigger VAD.
  Our job is NOT to second-guess whether they meant to talk to us.
  Our job IS to:
    1. Catch reserved system commands (start/stop listening)
    2. Route simple commands locally (Tier 1/2) for zero-latency execution
    3. Protect against dangerous actions (shutdown, restart)
    4. Send everything else to the LLM — it's smart enough to handle it

Pipeline:
  Reserved Commands → Safe Action Check → Pronoun Resolution → Fast Brain → LLM
"""

import re
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("MAX.LISTENING")

RESERVED_COMMANDS = {
    "start listening", "stop listening", "cancel", "abort",
    "emergency stop", "sunna band karo", "sunna shuru karo"
}

RISKY_ACTIONS = {"system_shutdown", "system_restart"}

WAKE_WORDS = ["hey max", "hello max", "hi max", "oye max", "ok max", "max"]


class ReservedCommandLayer:
    @staticmethod
    def check(text: str) -> Optional[str]:
        clean = re.sub(r'[^\w\s]', '', text.lower().strip())
        for cmd in RESERVED_COMMANDS:
            if cmd in clean:
                return cmd
        return None


class ConversationMemoryLayer:
    def __init__(self):
        self.recent_entities: list[str] = []

    def add_entity(self, entity: str):
        if entity in self.recent_entities:
            self.recent_entities.remove(entity)
        self.recent_entities.insert(0, entity)
        if len(self.recent_entities) > 5:
            self.recent_entities.pop()

    def resolve_pronouns(self, text: str) -> str:
        if not self.recent_entities:
            return text
        lower_text = text.lower()
        pronouns = ["that", "it", "use", "usko"]
        for p in pronouns:
            if f" {p} " in f" {lower_text} " or lower_text.endswith(f" {p}"):
                return text.replace(p, self.recent_entities[0], 1)
        return text


class LocalFastBrain:
    """
    Tier 1: Instant local commands (no LLM, no network)
    Tier 2: Skill-based routing (no LLM)
    Tier 3: Everything else → LLM
    """
    @staticmethod
    def strip_wake_word(text: str) -> str:
        lower = text.lower().strip()
        # Sort by length descending so "hey max" is checked before "max"
        for w in sorted(WAKE_WORDS, key=len, reverse=True):
            if lower.startswith(w):
                lower = lower[len(w):].strip()
                break
        return lower

    @staticmethod
    def route(text: str) -> Tuple[int, Optional[str]]:
        lower = LocalFastBrain.strip_wake_word(text)
        lower = re.sub(r'[^\w\s]', '', lower).strip()

        # Tier 1: Instant media/volume/system
        tier1_map = {
            # Media
            "pause": "[SKILL:media:pause]",
            "pause music": "[SKILL:media:pause]",
            "stop music": "[SKILL:media:pause]",
            "roko": "[SKILL:media:pause]",
            "gaana roko": "[SKILL:media:pause]",
            "play": "[SKILL:media:play]",
            "resume": "[SKILL:media:play]",
            "play music": "[SKILL:media:play]",
            "chalao": "[SKILL:media:play]",
            "next": "[SKILL:media:next]",
            "next song": "[SKILL:media:next]",
            "agla": "[SKILL:media:next]",
            "skip": "[SKILL:media:next]",
            "previous": "[SKILL:media:previous]",
            "previous song": "[SKILL:media:previous]",
            "peeche": "[SKILL:media:previous]",
            # Volume
            "volume up": "[SKILL:volume:up]",
            "awaaz badhao": "[SKILL:volume:up]",
            "volume down": "[SKILL:volume:down]",
            "awaaz kam karo": "[SKILL:volume:down]",
            "mute": "[SKILL:volume:mute]",
            "awaaz band karo": "[SKILL:volume:mute]",
            "unmute": "[SKILL:volume:unmute]",
            # Info
            "time": "[SKILL:time_now]",
            "what is the time": "[SKILL:time_now]",
            "time kya hai": "[SKILL:time_now]",
            "date": "[SKILL:date_today]",
            "what is the date": "[SKILL:date_today]",
            "aaj kya date hai": "[SKILL:date_today]",
            # System
            "close max": "[SKILL:quit_max]",
            "quit max": "[SKILL:quit_max]",
            "shutdown max": "[SKILL:quit_max]",
            "exit max": "[SKILL:quit_max]",
            "quit": "[SKILL:quit_max]",
            "quit yourself": "[SKILL:quit_max]",
            "exit": "[SKILL:quit_max]",
            "close": "[SKILL:quit_max]",
            "bye max": "[SKILL:quit_max]",
            "bye": "[SKILL:quit_max]",
            "goodbye": "[SKILL:quit_max]",
            "band ho ja": "[SKILL:quit_max]",
            "band ho jao": "[SKILL:quit_max]",
            "exit yourself": "[SKILL:quit_max]",
            "close yourself": "[SKILL:quit_max]",
        }

        if lower in tier1_map:
            return 1, tier1_map[lower]

        # Tier 2: Open app (Only for simple single-target app/folder requests, NOT compound/multi-action sentences)
        for prefix in ["open ", "kholo "]:
            if lower.startswith(prefix):
                app = lower[len(prefix):].strip()
                # If command contains conjunctions, multi-action verbs or is long, pass to LLM (Tier 3)
                complex_indicators = [" and ", " aur ", " then ", " to ", " take ", " send ", " tell ", " check ", " what ", " screenshot ", " whatsapp "]
                if app and len(app.split()) <= 4 and not any(ind in app for ind in complex_indicators):
                    return 2, f"[SKILL:open_app:{app}]"

        # Tier 2: YouTube search
        if lower.startswith("search youtube for "):
            query = lower[len("search youtube for "):].strip()
            if query:
                return 2, f"[SKILL:youtube_search:{query}]"

        # Tier 3: Send to LLM
        return 3, None


class SafeActionLayer:
    def __init__(self):
        self.awaiting_confirmation = False
        self.pending_action: Optional[str] = None
        self.pending_skill_tag: Optional[str] = None

    def intercept(self, skill_tag: str) -> Optional[str]:
        if not skill_tag:
            return None
        for action in RISKY_ACTIONS:
            if action in skill_tag:
                self.awaiting_confirmation = True
                self.pending_action = action
                self.pending_skill_tag = skill_tag
                return f"You are about to {action.replace('_', ' ')}. Are you sure?"
        return None

    def check_confirmation(self, text: str) -> Optional[str]:
        if not self.awaiting_confirmation:
            return None
        lower = text.lower().strip()
        if any(w in lower for w in ["yes", "confirm", "proceed", "haan", "ha", "yep", "do it"]):
            tag = self.pending_skill_tag
            self.clear()
            return tag
        if any(w in lower for w in ["no", "cancel", "abort", "na", "nahi", "stop"]):
            self.clear()
            return "CANCEL_ACTION"
        return None

    def clear(self):
        self.awaiting_confirmation = False
        self.pending_action = None
        self.pending_skill_tag = None


class ListeningManager:
    def __init__(self):
        self.memory = ConversationMemoryLayer()
        self.safe_layer = SafeActionLayer()
        self.continuous_mode = True

    def process_transcript(self, text: str) -> Dict[str, Any]:
        """
        Main pipeline. Simple and linear:
        Reserved → SafeAction confirmation → Pronoun resolve → FastBrain → LLM
        """
        # 1. Pending confirmation takes absolute priority
        if self.safe_layer.awaiting_confirmation:
            result = self.safe_layer.check_confirmation(text)
            if result == "CANCEL_ACTION":
                return {"action": "reply", "response": "Action cancelled.", "skill_tag": None}
            elif result:
                return {"action": "execute", "response": "Confirmed.", "skill_tag": result, "tier": 1}
            else:
                return {"action": "reply", "response": "Please say yes or no.", "skill_tag": None}

        # 2. Reserved commands (start/stop listening, abort)
        reserved = ReservedCommandLayer.check(text)
        if reserved:
            return {"action": "reserved", "command": reserved}

        # 3. Pronoun resolution (close it → close chrome)
        resolved_text = self.memory.resolve_pronouns(text)

        # 4. Local Fast Brain routing
        tier, skill_tag = LocalFastBrain.route(resolved_text)

        if tier in [1, 2] and skill_tag:
            # Safety check for risky actions
            safe_msg = self.safe_layer.intercept(skill_tag)
            if safe_msg:
                return {"action": "reply", "response": safe_msg, "skill_tag": None}

            # Track entities for pronoun resolution
            if "open_app" in skill_tag:
                app_name = skill_tag.split(":")[-1].replace("]", "")
                self.memory.add_entity(app_name)

            return {"action": "execute", "response": "Executing...", "skill_tag": skill_tag, "tier": tier}

        # 5. Everything else → LLM (the LLM is smart, let it decide)
        return {"action": "reasoning", "resolved_text": resolved_text}
