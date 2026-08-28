# Path: backend/modules/Intent_engine.py
# Use: Classifies user voice inputs into actionable intents.
"""
intent_engine.py — MAX v4.2
LLM-based intent classifier — runs BEFORE main response generation.

WHY THIS EXISTS:
  The main LLM handles both conversation AND skill routing in one call.
  When its system prompt fails (truncated, ambiguous), it fires skills incorrectly.
  Example: "Can you play YouTube?" → triggered youtube_play instead of answering.

  This module runs a FAST, FOCUSED classification call first.
  The main LLM then receives a clear directive: allow_skills=True/False.

INTENT TAXONOMY:
  COMMAND              — User explicitly wants action done NOW
  CAPABILITY_QUESTION  — User asks if MAX CAN do something
  INFORMATION_QUESTION — User asks about facts/data (may use search skill)
  NEGATIVE_COMMAND     — User says NOT to do something
  CONVERSATION         — Casual chat, greetings, thanks

FLOW:
  agent_core.py
      → IntentEngine.classify(text) → Intent
      → get_response(text, context, allow_skills=intent.should_execute_skill)
"""

import json
import asyncio
import logging
import re
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass, field
from api_utils import execute_with_retry, key_pool

logger = logging.getLogger("MAX.INTENT")


# ═══════════════════════════════════════════════════
# INTENT TYPES
# ═══════════════════════════════════════════════════

class IntentType(str, Enum):
    COMMAND              = "COMMAND"
    CAPABILITY_QUESTION  = "CAPABILITY_QUESTION"
    INFORMATION_QUESTION = "INFORMATION_QUESTION"
    NEGATIVE_COMMAND     = "NEGATIVE_COMMAND"
    CONVERSATION         = "CONVERSATION"


# These intent types must NEVER trigger skill execution
_NO_SKILL_INTENTS = frozenset({
    IntentType.CAPABILITY_QUESTION,
    IntentType.NEGATIVE_COMMAND,
    IntentType.CONVERSATION,
})


@dataclass
class Intent:
    type: IntentType
    should_execute_skill: bool
    confidence: float        # 0.0 – 1.0
    reason: str = ""

    def __post_init__(self):
        # Enforce: certain intent types can NEVER execute skills regardless of LLM output
        if self.type in _NO_SKILL_INTENTS:
            object.__setattr__(self, "should_execute_skill", False)


# ═══════════════════════════════════════════════════
# CLASSIFICATION PROMPT
# Extremely focused — only classifies, never responds.
# max_tokens=80, temperature=0.0 for determinism.
# ═══════════════════════════════════════════════════

_PROMPT = """You are a strict intent classifier for an AI assistant. Classify the user message into exactly one intent type.

INTENT DEFINITIONS:

COMMAND — User wants an action performed RIGHT NOW.
  ✓ "play Believer on YouTube"
  ✓ "open Chrome"
  ✓ "set a 5 minute timer"
  ✓ "search for latest news"
  ✓ "YouTube pe Believer play karo"
  ✓ "research about quantum computing"
  ✓ "deep research karo AI pe"
  ✓ "find data about climate change"
  ✓ "investigate blockchain technology"
  ✓ "open the copied link"
  ✓ "clipboard me jo link hai use open karo"
  ✓ "screen pe jo link hai open karo"
  ✓ "open the link inside main.py"
  ✓ "ask chatgpt to write a python script"
  ✓ "use claude to summarize this"
  ✓ "ask gemini"
  ✓ "perplexity se pucho"

CAPABILITY_QUESTION — User asks if the assistant CAN or IS ABLE TO do something. No action should be taken.
  ✓ "can you play YouTube videos?"
  ✓ "are you able to open apps?"
  ✓ "do you support timers?"
  ✓ "tell me if you can play videos"
  ✓ "kya tum YouTube play kar sakte ho?"
  ✓ "bata do kya tum ye kar sakte ho"
  KEY SIGNAL: contains "can you", "are you able", "do you support", "kya tum kar sakte", question about capability

INFORMATION_QUESTION — User asks about facts, news, weather, current data. May need search skill.
  ✓ "what's the weather in Mumbai?"
  ✓ "who is Elon Musk?"
  ✓ "latest IPL scores"

NEGATIVE_COMMAND — User explicitly says NOT to do something.
  ✓ "don't open YouTube"
  ✓ "never play music automatically"
  ✓ "do not open the browser"
  KEY SIGNAL: "don't", "do not", "never", "not", "mat", "nahi karna"

CONVERSATION — Casual chat, greetings, thanks, identity questions, feelings.
  ✓ "hey", "hello", "how are you?"
  ✓ "thanks", "okay", "got it"
  ✓ "what is your name?"
  ✓ "good morning"

CRITICAL DECISION RULES (in order of priority):
1. If the message contains capability-check words ("can you", "are you able", "kya tum kar sakte", "tell me if you can") → CAPABILITY_QUESTION, should_execute_skill=false
2. If the message contains negation ("don't", "do not", "never", "mat") about an action → NEGATIVE_COMMAND, should_execute_skill=false
3. If it's a greeting, casual chat, identity question → CONVERSATION, should_execute_skill=false
4. If the message is a direct response, continuation, or relates to a game/topic in the Recent Context (even if it's random Hindi text or lyrics) → CONVERSATION, should_execute_skill=false
5. If user is directly asking for an action now → COMMAND, should_execute_skill=true
6. Otherwise → INFORMATION_QUESTION, should_execute_skill=true (may need search)

Recent Context:
{context}

User message: "{text}"

Respond ONLY with valid JSON. No other text.
{{"intent": "COMMAND|CAPABILITY_QUESTION|INFORMATION_QUESTION|NEGATIVE_COMMAND|CONVERSATION", "should_execute_skill": true|false, "confidence": 0.0-1.0, "reason": "one line"}}"""


# ═══════════════════════════════════════════════════
# FAST PATTERN PRE-CHECK
# Catches obvious cases without any LLM call.
# Only call LLM when truly ambiguous.
# [PREVIOUS LOGIC PRESERVED]
# _EXPLICIT_ACTION_PATTERNS = re.compile(
#     r'\b(open|khol|kholo|launch|start|play|run|search|record|create|set|delete|send)\b',
#     re.IGNORECASE
# )

_CAPABILITY_PATTERNS = re.compile(
    r'\b(can you|are you able|do you support|are you capable|kya tum kar sakte|'
    r'kya aap kar sakte|tell me if you can|can u|are u able|'
    r'bata do kya|kya ye ho sakta|kya tum)\b',
    re.IGNORECASE
)

_NEGATIVE_PATTERNS = re.compile(
    r"\b(don't|do not|never|please don't|mat karo|nahi karna|"
    r"mujhe nahi chahiye)\b",
    re.IGNORECASE
)

_CONVERSATION_PATTERNS = re.compile(
    r"^(hi|hello|hey|good morning|good night|good evening|how are you|"
    r"how r u|what is your name|who are you|what can you do|thanks|"
    r"thank you|okay|ok|got it|sure|alright|great|nice|cool|"
    r"kaise ho|namaste|sukriya|theek hai)[\s!?.]*$",
    re.IGNORECASE
)

_GREETING_WITH_NAME = re.compile(
    r"^(?:hey|hello|hi|good\s+morning|good\s+night|good\s+evening|"
    r"good\s+afternoon|yo|namaste|howdy|hola)[\s,]+"
    r"(?:max|mex|maps|macs)[\s!?.,]*$",
    re.IGNORECASE
)

_SUGGESTION_PATTERNS = re.compile(
    r'\b(suggest|recommend|advise|best platform|best website|best app|'
    r'which one|which platform|what is the best|kya suggest|konsa best|'
    r'kya use karu|which one should|best way to|best tool)\b',
    re.IGNORECASE
)

_ACTION_VERBS = {"open", "khol", "kholo", "play", "launch", "start", "run", "search", "record", "create", "set", "delete", "send"}

def _fast_classify(text: str) -> Optional[Intent]:
    """
    Pattern-based fast path. Returns Intent if obvious, else None (→ use LLM).
    Dynamically differentiates capability questions from explicit action requests.
    """
    t = text.strip()

    # ── Greeting with assistant name (highest priority) ──
    # Catches: "Hello, Max.", "Hey Max!", "Good morning Max", etc.
    if _GREETING_WITH_NAME.match(t):
        return Intent(
            type=IntentType.CONVERSATION,
            should_execute_skill=False,
            confidence=0.99,
            reason="greeting with assistant name"
        )

    # Strip wake word for cleaner pattern matching (handles comma: "Hello, Max, open chrome")
    t_clean = re.sub(r'^(?:hey|hello|hi|ok|okay)[\s,]+max\b[\s,.!?]*', '', t, flags=re.IGNORECASE).strip()
    if t_clean == t.strip():
        # Didn't match greeting+max prefix, try standalone "max" prefix
        t_clean = re.sub(r'^max[\s,]+', '', t, flags=re.IGNORECASE).strip()
    if not t_clean:
        t_clean = t.strip()
    words = set(re.findall(r'\b\w+\b', t_clean.lower()))

    # Recommendation / advice check (no explicit action verbs)
    if _SUGGESTION_PATTERNS.search(t_clean) and not words.intersection(_ACTION_VERBS):
        return Intent(
            type=IntentType.CONVERSATION,
            should_execute_skill=False,
            confidence=0.97,
            reason="pure recommendation/suggestion query"
        )

    # Capability check:
    # "can you play youtube videos?" -> CAPABILITY_QUESTION
    # "can you play Believer on youtube?" -> COMMAND (has specific target 'believer')
    if _CAPABILITY_PATTERNS.search(t_clean):
        # Generic capability questions end with general terms ("videos", "music", "apps", "files", "code", "timers")
        generic_terms = {"videos", "video", "music", "songs", "apps", "app", "files", "timers", "reminders", "code"}
        is_generic = bool(words.intersection(generic_terms)) and len(words) <= 6
        
        if words.intersection(_ACTION_VERBS) and not is_generic and not _NEGATIVE_PATTERNS.search(t_clean):
            return Intent(
                type=IntentType.COMMAND,
                should_execute_skill=True,
                confidence=0.98,
                reason="capability phrasing with specific target action"
            )
        return Intent(
            type=IntentType.CAPABILITY_QUESTION,
            should_execute_skill=False,
            confidence=0.97,
            reason="pure capability question"
        )

    if _NEGATIVE_PATTERNS.search(t_clean):
        # Allow cancel skills for system actions (shutdown/restart/lock)
        # "don't shutdown" / "cancel shutdown" → still needs skill execution for cancel_shutdown
        _CANCELLABLE_ACTION_WORDS = {"shutdown", "shut", "restart", "reboot", "lock", "power"}
        if words.intersection(_CANCELLABLE_ACTION_WORDS):
            return Intent(
                type=IntentType.COMMAND,
                should_execute_skill=True,
                confidence=0.97,
                reason="negation with cancellable system action — route to cancel skill"
            )
        return Intent(
            type=IntentType.NEGATIVE_COMMAND,
            should_execute_skill=False,
            confidence=0.95,
            reason="negation keyword detected"
        )

    if _CONVERSATION_PATTERNS.match(t_clean) or _CONVERSATION_PATTERNS.match(t):
        return Intent(
            type=IntentType.CONVERSATION,
            should_execute_skill=False,
            confidence=0.99,
            reason="pure conversation pattern"
        )

    return None  # Ambiguous — needs LLM


# ═══════════════════════════════════════════════════
# INTENT ENGINE
# ═══════════════════════════════════════════════════

class IntentEngine:
    """
    Two-layer intent classifier:
      Layer 1: Fast pattern matching (0ms, no API call)
      Layer 2: LLM classification for ambiguous inputs (~300ms)

    Results are cached to prevent duplicate API calls for repeated queries.
    """

    def __init__(self, config):
        self.config = config
        self._cache: Dict[str, Intent] = {}
        self._max_cache_size = 500

    async def classify(self, text: str, context: str = "") -> Intent:
        """
        Main entry point. Returns Intent for the given user text.
        Never raises — falls back to safe defaults on failure.
        """
        cache_key = text.strip().lower()

        # Cache hit
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            logger.debug(f"Intent cache hit: {cached.type.value} for '{text[:50]}'")
            return cached

        # Layer 1: Fast pattern check
        fast = _fast_classify(text)
        if fast is not None:
            logger.info(f"Intent (fast): {fast.type.value} [{fast.confidence:.2f}] — '{text[:60]}'")
            self._cache_put(cache_key, fast)
            return fast

        # Default to Single-Pass Main Agent Execution (0ms Overhead, 0 Extra LLM Calls)
        # Main Agent handles skill selection and chat dynamically in a single pass.
        intent = Intent(
            type=IntentType.COMMAND,
            should_execute_skill=True,
            confidence=0.90,
            reason="single-pass main agent delegation"
        )
        logger.info(f"Intent (Single-Pass): {intent.type.value} [{intent.confidence:.2f}] — '{text[:60]}'")
        self._cache_put(cache_key, intent)
        return intent

        # [PREVIOUS LOGIC PRESERVED]
        # try:
        #     intent = await asyncio.wait_for(self._llm_classify(text, context), timeout=10.0)
        # except asyncio.TimeoutError:
        #     intent = self._default_intent("timeout")
        # except Exception as e:
        #     intent = self._default_intent(str(e))
        # self._cache_put(cache_key, intent)
        # return intent

    async def _llm_classify(self, text: str, context: str) -> Intent:
        from groq import AsyncGroq
        
        # Limit context size to avoid massive token overhead per intent call
        short_context = context[-1500:] if len(context) > 1500 else context
        prompt = _PROMPT.replace("{text}", text.strip()).replace("{context}", short_context or "No recent context.")

        async def call():
            key = await key_pool.lease_key()
            if not key:
                raise ValueError("No API key available")
            client = AsyncGroq(api_key=key)
            return await client.chat.completions.create(
                model=self.config.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,    # Must be deterministic
                max_tokens=80,      # We only need the JSON
            )

        resp = await execute_with_retry(call)
        raw = resp.choices[0].message.content.strip()
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> Intent:
        """Parse LLM JSON response into Intent. Handles malformed output gracefully."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # LLM may have wrapped JSON in backticks or added preamble — extract it
            m = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse intent JSON: {raw[:100]}")
                    return self._default_intent("json parse failed")
            else:
                return self._default_intent("no json in response")

        # Map intent string to enum
        intent_str = data.get("intent", "COMMAND").upper().strip()
        try:
            intent_type = IntentType(intent_str)
        except ValueError:
            logger.warning(f"Unknown intent type: '{intent_str}'. Defaulting to COMMAND.")
            intent_type = IntentType.COMMAND

        should_execute = bool(data.get("should_execute_skill", True))
        confidence = float(data.get("confidence", 0.8))
        reason = str(data.get("reason", ""))

        return Intent(
            type=intent_type,
            should_execute_skill=should_execute,
            confidence=confidence,
            reason=reason,
        )

    def _default_intent(self, reason: str) -> Intent:
        """
        Safe fallback — allows skill execution (preserves original behavior).
        We only suppress skills when we're confident it's wrong to do so.
        """
        return Intent(
            type=IntentType.COMMAND,
            should_execute_skill=True,
            confidence=0.5,
            reason=f"fallback: {reason}"
        )

    def _cache_put(self, key: str, intent: Intent):
        """Add to cache, evict oldest entries if over limit."""
        if len(self._cache) >= self._max_cache_size:
            # Remove oldest 20% of entries
            evict_count = self._max_cache_size // 5
            for k in list(self._cache.keys())[:evict_count]:
                del self._cache[k]
        self._cache[key] = intent


# ═══════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════

_intent_engine: Optional[IntentEngine] = None


def get_intent_engine(config) -> IntentEngine:
    global _intent_engine
    if _intent_engine is None:
        _intent_engine = IntentEngine(config)
        logger.info("IntentEngine initialized")
    return _intent_engine