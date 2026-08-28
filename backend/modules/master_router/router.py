"""
Master router - decides whether a query is conversational or actionable.

Performance notes:
- Deterministic patterns handle the easy 80% of inputs in <1ms with no LLM call.
- Only ambiguous inputs are sent to the LLM.
- Token usage drops by ~80% on typical sessions.

Returns one of: "chat_agent" | "legacy_engine"
"""

import os
import re
import json
import logging
from typing import List, Dict, Any

from groq import AsyncGroq

from api_utils import key_pool
from .prompts import ROUTER_SYSTEM_PROMPT
from .errors import RouterExecutionError

logger = logging.getLogger("MAX.MasterRouter")


# Words / patterns that strongly suggest an action is requested.
_ACTION_TOKENS = {
    "open", "kholo", "khol", "launch", "start", "stop", "band", "close",
    "play", "pause", "set", "create", "make", "write", "run", "execute",
    "send", "schedule", "remind", "reminder", "search", "find", "look up",
    "show", "screenshot", "record", "type", "click", "press", "scroll",
    "delete", "remove", "rename", "move", "copy", "save", "download",
    "upload", "install", "uninstall", "lock", "unlock", "shutdown", "restart",
    "shutdown", "volume", "brightness", "mute", "unmute", "increase", "decrease",
    "call", "message", "email", "whatsapp", "tweet", "post", "book",
    "increase", "decrease", "up", "down", "bada", "kam", "ghata",
    "timer", "alarm", "weather", "temperature", "news", "youtube",
    "google", "search karo", "dhundho", "batao", "bata", "dekho",
    "screenshot lo", "screen record", "read screen", "screen read",
    "code likho", "code banao", "fix karo", "review karo", "run karo",
}

# Words / patterns that strongly suggest conversation.
_CHAT_TOKENS = {
    "hi", "hello", "hey", "namaste", "namaskar", "kaise", "kaisa", "kaisi",
    "thanks", "thank", "shukriya", "dhanyavaad", "sorry", "maaf",
    "how are you", "kya haal", "what's up", "sup", "kya kar rahe",
    "good morning", "good night", "subah", "raat", "shubh",
    "i love you", "love you", "miss you", "yaad aaya",
    "bored", "tired", "happy", "sad", "angry", "frustrated", "lonely",
    "joke", "funny", "meme", "story", "kahani",
    "who are you", "what is your name", "your name", "tum kaun", "tumhara naam",
    "what can you do", "kya kar sakte", "capabilities",
    "really", "seriously", "wow", "amazing", "great", "nice", "cool", "awesome",
    "haha", "lol", "hehe", "lmao",
    "i think", "i feel", "i believe", "mujhe lagta", "mujhe laga",
    "tell me about", "baat karo", "chat karo", "baatein", "gup",
}


class MasterRouter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def _format_context(self, context: List[Dict[str, str]]) -> str:
        if not context:
            return "No previous context."
        lines = []
        for msg in context[-3:]:
            role = msg.get("role", "user").capitalize()
            content = (msg.get("content", "") or "")[:100]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def route(self, query: str, context: List[Dict[str, str]] = None) -> str:
        """
        Returns 'chat_agent' or 'legacy_engine'.

        Cheap deterministic rules first; LLM only for ambiguous inputs.
        """
        q = (query or "").strip()
        q_lower = q.lower()
        ctx = context or []

        # 1. Very short, pure chat inputs - never action.
        words = re.findall(r"\b[\w']+\b", q_lower)
        if not words:
            return "chat_agent"

        # 2. If ANY strong action token is present, it's an action.
        #    Order matters: check action first because compound sentences
        #    like "tell me a joke but also open YouTube" are still actions.
        for tok in words:
            if tok in _ACTION_TOKENS or any(a in tok for a in ("kar", "khol", "bana")):
                # But filter out questions about actions ("can you open chrome?")
                if "?" in q and not re.search(r"\b(karo|kholo|do|de|na)\b", q_lower):
                    continue
                return "legacy_engine"

        # 3. Pure chat signals
        chat_score = 0
        action_score = 0
        for tok in words:
            if tok in _CHAT_TOKENS:
                chat_score += 1
            if any(a in tok for a in ("kar", "khol", "bana", "set", "open", "send")):
                action_score += 1
        # 4. Conversational opener pattern: greeting + name/identity
        if q_lower.startswith(("hi ", "hello ", "hey ", "namaste", "namaskar", "good ", "kaise", "kya haal")):
            chat_score += 2
        # 5. Question with no imperative verb -> chat
        if q.endswith("?") and action_score == 0:
            chat_score += 1

        if chat_score >= 2 and chat_score > action_score:
            return "chat_agent"
        if action_score >= 2 and action_score > chat_score:
            return "legacy_engine"

        # 6. Capability / identity questions are always chat
        if re.search(r"\b(who are you|what can you do|what is your name|tum kaun|kya kar sakte|capabilities|your name)\b", q_lower):
            return "chat_agent"

        # 7. Follow-up pattern after a recent action: still chat (no new action words)
        if ctx and action_score == 0:
            # If the last assistant message just executed a skill, the next
            # utterance is often conversational ("thanks", "great", "now what").
            last = ctx[-1] if ctx else {}
            if last.get("role") in ("assistant", "Max") and chat_score > 0:
                return "chat_agent"

        # 8. Ambiguous -> ask the LLM (low temperature, small output, JSON).
        return await self._llm_route(q, ctx)

    async def _llm_route(self, query: str, context: List[Dict[str, str]]) -> str:
        try:
            formatted_context = self._format_context(context)
            system_prompt = ROUTER_SYSTEM_PROMPT.replace("{context}", formatted_context)

            api_key = await key_pool.lease_key()
            if not api_key:
                return "legacy_engine"
            client = AsyncGroq(api_key=api_key)
            main_model = getattr(
                self.config, "MAIN_LLM_MODEL",
                os.getenv("MAIN_LLM_MODEL", "openai/gpt-oss-120b"),
            )
            response = await client.chat.completions.create(
                model=main_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=50,
                response_format={"type": "json_object"},
            )
            output = (response.choices[0].message.content or "").strip()
            try:
                parsed = json.loads(output)
            except Exception:
                return "legacy_engine"
            assigned = parsed.get("assign_to", "legacy_engine")
            if assigned not in ("chat_agent", "legacy_engine"):
                return "legacy_engine"
            return assigned
        except Exception as e:
            logger.error(f"[MasterRouter] LLM route failed: {e}; defaulting legacy")
            return "legacy_engine"
