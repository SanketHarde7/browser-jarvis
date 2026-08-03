# Path: backend/modules/gatekeeper.py
# Use: Ensures security policies and authorization checks.
# gatekeeper.py — MAX v4.3
# Post-processes LLM output before UI + TTS.
# - Removes banned words that still leak through
# - Gender correction: MAX is female (he/him → she/her) - context-aware
# - Strips leaked skill tags, markdown artifacts
# - TTS-specific cleanup (emojis, length trim)
import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("MAX.GATEKEEPER")

_URL_PATTERN = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_DOMAIN_PATTERN = re.compile(
    r"\b[a-zA-Z0-9-]+\.(?:com|net|org|io|ai|app|dev|co|in|edu|gov|me|tv|xyz)(?:/\S*)?\b",
    re.IGNORECASE,
)
_LOCALHOST_PATTERN = re.compile(r"\blocalhost(?::\d+)?\b", re.IGNORECASE)


def clean_response_text(text: str) -> str:
    """Remove artifact brackets while preserving ACTION tags."""
    # Remove SKILL tags that leaked through
    cleaned_text = re.sub(r"\[SKILL:[^\]]*\]", "", text)
    # Remove other bracket artifacts except ACTION:
    cleaned_text = re.sub(r"\[(?!ACTION:)[A-Z_]+:[^\]]*\]", "", cleaned_text)
    # Clean up extra whitespace
    cleaned_text = re.sub(r" {2,}", " ", cleaned_text).strip()
    return cleaned_text


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


def _strip_urls_for_tts(text: str) -> str:
    def _replace(match: re.Match) -> str:
        return _url_to_label(match.group(0))

    text = _LOCALHOST_PATTERN.sub("Localhost", text)
    text = _URL_PATTERN.sub(_replace, text)
    text = _DOMAIN_PATTERN.sub(_replace, text)
    return text


class _Rule:
    __slots__ = ("regex", "replacement")

    def __init__(self, pattern: str, replacement: str, flags: int = re.IGNORECASE):
        self.regex = re.compile(pattern, flags)
        self.replacement = replacement

    def apply(self, text: str) -> str:
        return self.regex.sub(self.replacement, text)


class ResponseGatekeeper:
    """
    Applied to every LLM response before it reaches the UI and TTS.

    Rule order:
      1. Banned casual words
      2. Gender correction (MAX is female) — context-aware
      3. Leaked skill tags / markdown
      4. Whitespace / punctuation cleanup
    """

    # ── 1. Banned words ──────────────────────────────────
    _BANNED: List[Tuple[str, str]] = [
        (r"\barre\s+bhai\b", ""),
        (r"\barre\s+yaar\b", ""),
        (r"\barre\s+boss\b", ""),
        (r"\barre\b", ""),
        (r"\byaar\b", ""),
        (r"(?<![a-zA-Z])bhai(?![a-zA-Z])", ""),
        (r"\bsir\b", ""),
        # Over-polite openers
        (r"^(of course|certainly|absolutely|sure thing)[!,.]?\s*", ""),
        # Robotic phrases
        (r"\bAs an AI (language model|assistant)\b", "I"),
        (r"\bAs a(n AI)? (language model|assistant)\b", "I"),
        (r"\bI am an AI (language model|assistant)\b", "I'm MAX"),
        (r"\bI am a large language model\b", "I'm MAX"),
        (r"\bI do not have (personal|feelings|emotions|a body)\b", ""),
        (r"\bI don't have (personal|feelings|emotions|a body)\b", ""),
    ]

    # ── 2. Gender correction ─────────────────────────────
    # MAX is female — fix male pronouns used in self-reference.
    # These are conservative to avoid flipping user-facing pronouns.
    _GENDER: List[Tuple[str, str]] = [
        # "I am he" / "I'm he" type constructs
        (r"\bI(?:'m| am) he\b", "I am MAX"),
        (r"\bI(?:'m| am) him\b", "I am MAX"),
        # "he said" / "he can" when referring to MAX in 3rd person
        (r"\bhe(?= (is|was|can|will|has|said|told|knows|thinks|does|helped|handled|did|created|made|found))\b", "she"),
        # Possessive "his" used for MAX
        (r"\bhis(?= (response|reply|answer|voice|name|output|code|suggestion|recommendation|opinion|take|role|job))\b", "her"),
        # "him" referring to MAX
        (r"\b(ask|tell|give|show|send|hand|help)\s+him\b", r"\1 her"),
        # "himself" -> "herself" when clearly about MAX
        (r"\bhe himself\b", "she herself"),
        (r"\bby himself\b", "by herself"),
    ]

    # ── 3. MAX name correction ────────────────────────────
    # Sometimes LLM refers to itself in 3rd person
    _SELF_REF: List[Tuple[str, str]] = [
        (r"\bthe assistant\b", "I"),
        (r"\bthis assistant\b", "I"),
        (r"\bMAX said\b", "I said"),
        (r"\bMAX thinks\b", "I think"),
        (r"\bMAX will\b", "I'll"),
        (r"\bMAX can\b", "I can"),
        (r"\bMAX is\b", "I'm"),
    ]

    # ── 4. Artifact cleanup ───────────────────────────────
    _ARTIFACTS: List[Tuple[str, str]] = [
        (r"\[SKILL:[^\]]*\]", ""),     # Leaked skill tags
        (r"\[[A-Z_]+:[^\]]*\]", ""),     # Other bracket patterns (except ACTION:)
        (r"\[ACTION:HIBERNATE\]", "[ACTION:HIBERNATE]"),  # Preserve HIBERNATE
        (r"`{1,2}([^`]+)`{1,2}", r"\1"),  # Inline code backticks
        (r"\*{1,2}([^*]+)\*{1,2}", r"\1"),  # Bold/italic markers
    ]

    # ── 5. Whitespace / punctuation ──────────────────────
    _CLEANUP: List[Tuple[str, str]] = [
        (r"[ \t]{2,}", " "),
        (r"^\s*[,;!]\s*", ""),
        (r"\s*[,;]\s*$", ""),
        (r"\n{3,}", "\n\n"),
        (r"^\s*[-—]\s*", ""),  # Leading dashes
    ]

    # ── TTS extra: remove visuals ─────────────────────────
    _TTS_EXTRA: List[Tuple[str, str]] = [
        (r"\*+", ""),
        (r"#+\s*", ""),
        (r"`+", ""),
        (r"_{2,}", ""),
        # Emoji ranges
        (r"[\U0001F300-\U0001FAFF]", ""),
        (r"[\U00002600-\U000027BF]", ""),
        (r"[\U0001F900-\U0001F9FF]", ""),
        (r"[\U0001F100-\U0001F1FF]", ""),  # Regional indicators
    ]

    def __init__(self, extra_banned: Optional[List[str]] = None):
        build = lambda rules, flags=re.IGNORECASE: [_Rule(p, r, flags) for p, r in rules]

        self._banned_rules = build(self._BANNED)
        self._gender_rules = build(self._GENDER)
        self._self_ref_rules = build(self._SELF_REF)
        self._artifact_rules = build(self._ARTIFACTS, 0)
        self._cleanup_rules = build(self._CLEANUP, 0)
        self._tts_rules = build(self._TTS_EXTRA, 0)

        if extra_banned:
            for word in extra_banned:
                escaped = re.escape(word.lower())
                self._banned_rules.append(_Rule(rf"(?<![a-zA-Z]){escaped}(?![a-zA-Z])", ""))

    def filter(self, text: str) -> str:
        """Standard filter for UI display."""
        if not text or not text.strip():
            return text

        original = text
        result = text

        # Apply banned words filter
        for rule in self._banned_rules:
            result = rule.apply(result)

        # Apply gender correction
        for rule in self._gender_rules:
            result = rule.apply(result)

        # Apply self-reference correction
        for rule in self._self_ref_rules:
            result = rule.apply(result)

        # Apply artifact cleanup
        result = clean_response_text(result)
        for rule in self._artifact_rules:
            result = rule.apply(result)

        # Apply whitespace cleanup
        for rule in self._cleanup_rules:
            result = rule.apply(result)

        result = result.strip()
        
        # Capitalize first letter if needed
        if result and result[0].islower():
            result = result[0].upper() + result[1:]

        if result != original:
            logger.debug(f"Gatekeeper: '{original[:70]}' → '{result[:70]}'")
        
        return result

    def filter_for_tts(self, text: str, max_chars: int = 350) -> str:
        """Legacy filter for TTS — truncates text."""
        chunks = self.chunk_for_tts(text, max_chars)
        return chunks[0] if chunks else ""

    def chunk_for_tts(self, text: str, max_chars: int = 250) -> List[str]:
        """Aggressive filter for TTS — strips emojis/markdown and smartly chunks text into sentences."""
        if not text:
            return []

        # Strip code blocks and markdown fences for spoken audio
        result = re.sub(r"```[\s\S]*?```", "", text)
        result = re.sub(r"`[^`]*`", "", result)
        result = re.sub(r"#+\s*", "", result)
        
        result = self.filter(result)
        result = _strip_urls_for_tts(result)
        
        for rule in self._tts_rules:
            result = rule.apply(result)
        
        result = re.sub(r" {2,}", " ", result).strip()
        if not result:
            return ["Here is what you requested."] if text.strip() else []

        # Smart chunking by sentence boundaries
        chunks = []
        # split by punctuation keeping the punctuation
        sentences = re.split(r'(?<=[.!?\n])\s+', result)
        
        current_chunk = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # If a single sentence is incredibly long (rare), hard split it
            if len(sentence) > max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                
                # Hard split by commas or just brute force
                sub_parts = re.split(r'(?<=,)\s+', sentence)
                temp_sub = ""
                for sub in sub_parts:
                    if len(temp_sub) + len(sub) <= max_chars:
                        temp_sub += " " + sub
                    else:
                        if temp_sub: chunks.append(temp_sub.strip())
                        temp_sub = sub
                if temp_sub:
                    current_chunk = temp_sub.strip()
                continue
                
            if len(current_chunk) + len(sentence) <= max_chars:
                current_chunk += (" " if current_chunk else "") + sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
                
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks


# Singleton
_gatekeeper: Optional[ResponseGatekeeper] = None

def get_gatekeeper() -> ResponseGatekeeper:
    global _gatekeeper
    if _gatekeeper is None:
        _gatekeeper = ResponseGatekeeper()
    return _gatekeeper

