# emotion_tracker.py — Lightweight session emotion state for MAX
# 100% local. No API calls. Keyword-based detection only.

import time
from typing import Optional

# ── Emotion definitions ──────────────────────────────────────
EMOTION_KEYWORDS = {
    "frustrated": [
        "ugh", "wtf", "fuck", "shit", "not working", "broken", "stupid",
        "why isn't", "why is it", "nothing works", "again", "still not",
        "wrong", "mistake", "incorrect", "error again", "pareshaan",
        "irritating", "annoying", "frustrating", "dimag kharab", "bakwas",
        "nahi ho raha", "kya hai ye", "pakk gaya", "gussa", "irritate"
    ],
    "stressed": [
        "deadline", "urgent", "asap", "quickly", "fast", "hurry",
        "running out of time", "jaldi", "time nahi", "submission",
        "presentation tomorrow", "exam", "need this now", "tension",
        "stress", "fati", "panic", "late ho gaya", "bohot kaam hai"
    ],
    "tired": [
        "tired", "exhausted", "sleepy", "thak", "neend", "rest",
        "can't focus", "brain dead", "low energy", "tak gaya",
        "so jaunga", "bas yaar", "energy nahi", "break chahiye", "burnout"
    ],
    "happy": [
        "nice", "great", "awesome", "working", "finally", "fixed",
        "it works", "perfect", "ho gaya", "done", "yay", "love it",
        "thank you", "thanks", "amazing", "badhiya", "mast", "maza aa gaya",
        "superb", "khatarnak", "bawaal", "set hai"
    ],
    "excited": [
        "let's go", "let's build", "idea", "new project", "want to make",
        "thinking of", "what if", "can we", "i want to", "mind blowing",
        "excited", "craze", "aag laga denge", "fodu", "plan", "start karein"
    ],
    "bored": [
        "bored", "boring", "nothing to do", "kuch nahi", "bakwaas",
        "suggest something", "entertain", "random", "timepass",
        "kya karu", "bore ho raha", "kuch naya", "pakao"
    ],
    "focused": [
        "let's continue", "next step", "moving on", "what's next",
        "keep going", "proceed", "continue", "focus", "concentrate",
        "zone", "dhyan", "chal aage", "next", "serious", "flow"
    ],
}

# Emotion → prompt injection text (what MAX sees in system prompt)
EMOTION_PROMPTS = {
    "frustrated": "Sanket seems FRUSTRATED right now. Be calm, skip pleasantries, get straight to the solution. No filler words.",
    "stressed":   "Sanket seems STRESSED or under time pressure. Be fast and efficient. Give the answer directly. No padding.",
    "tired":      "Sanket seems TIRED. Keep responses SHORT and gentle. Don't overload with information.",
    "happy":      "Sanket is in a GOOD MOOD. Match his energy lightly. Brief celebration is okay, then move on.",
    "excited":    "Sanket is EXCITED about something. Match his enthusiasm briefly, then be helpful and practical.",
    "bored":      "Sanket seems BORED. Be conversational and playful. Suggest something or ask what interests him.",
    "focused":    "Sanket is IN THE ZONE. Be sharp and minimal. Just the answer, no small talk.",
    "neutral":    "",   # Empty string → nothing injected into prompt
}

# ── State storage ─────────────────────────────────────────────
_current_emotion: str = "neutral"
_emotion_set_at: float = 0.0
EMOTION_FADE_SECONDS: int = 300   # Emotion fades to neutral after 5 minutes of inactivity


def detect_emotion(text: str) -> Optional[str]:
    """
    Scan user message for emotion keywords.
    Returns detected emotion string, or None if nothing detected.
    Priority order: frustrated > stressed > tired > happy > excited > bored > focused
    """
    if not text:
        return None
    text_lower = text.lower()
    priority_order = ["frustrated", "stressed", "tired", "happy", "excited", "bored", "focused"]

    for emotion in priority_order:
        keywords = EMOTION_KEYWORDS[emotion]
        if any(kw in text_lower for kw in keywords):
            return emotion
    return None


def update_emotion(text: str) -> None:
    """
    Call this on every user message.
    Updates global emotion state if a new emotion is detected.
    If nothing detected, keeps existing state (don't reset to neutral immediately).
    """
    global _current_emotion, _emotion_set_at
    detected = detect_emotion(text)
    if detected:
        _current_emotion = detected
        _emotion_set_at = time.time()


def get_current_emotion() -> str:
    """
    Returns current emotion. Auto-fades to 'neutral' after EMOTION_FADE_SECONDS.
    """
    global _current_emotion, _emotion_set_at
    if _current_emotion != "neutral":
        elapsed = time.time() - _emotion_set_at
        if elapsed > EMOTION_FADE_SECONDS:
            _current_emotion = "neutral"
    return _current_emotion


def get_emotion_prompt_injection() -> str:
    """
    Returns the string to inject into system prompt.
    Returns empty string if emotion is neutral (no injection needed).
    """
    emotion = get_current_emotion()
    return EMOTION_PROMPTS.get(emotion, "")


def reset_emotion() -> None:
    """Call on session reset / clear_memory."""
    global _current_emotion, _emotion_set_at
    _current_emotion = "neutral"
    _emotion_set_at = 0.0
