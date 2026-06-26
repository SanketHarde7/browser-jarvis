# personality_engine.py — Dynamic personality context for MAX
# Fully local. Uses datetime only. Zero API calls.

from datetime import datetime
from typing import Optional

# ── Session depth tracker ─────────────────────────────────────
_message_count: int = 0

def increment_message_count() -> None:
    global _message_count
    _message_count += 1

def reset_message_count() -> None:
    global _message_count
    _message_count = 0

def get_message_count() -> int:
    return _message_count


# ── Last topic tracker ────────────────────────────────────────
_last_topic: str = "general"

TOPIC_KEYWORDS = {
    "coding":   ["code", "error", "bug", "function", "class", "api", "debug",
                 "python", "react", "javascript", "llm", "model", "database",
                 "deploy", "server", "backend", "frontend", "script", "file"],
    "learning": ["explain", "what is", "how does", "teach", "understand",
                 "concept", "difference between", "kya hai", "samjhao", "bata"],
    "casual":   ["bhai", "yaar", "haha", "lol", "kya chal", "mood", "bored",
                 "chill", "random", "fun", "joke", "masti", "timepass"],
    "venting":  ["ugh", "frustrated", "nothing works", "hate", "worst",
                 "pareshaan", "irritating", "stress", "pressure", "thak"],
    "planning": ["project", "idea", "plan", "roadmap", "steps", "strategy",
                 "build", "create", "start", "let's make", "banate hain"],
}

def update_topic(text: str) -> None:
    global _last_topic
    text_lower = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            _last_topic = topic
            return
    # Don't reset topic if nothing detected — carry forward last topic

def get_last_topic() -> str:
    return _last_topic

def reset_topic() -> None:
    global _last_topic
    _last_topic = "general"


# ── Time of day ───────────────────────────────────────────────
def get_time_slot() -> str:
    """
    Returns time slot string based on current hour (IST assumed).
    morning     = 05:00 - 11:59
    afternoon   = 12:00 - 16:59
    evening     = 17:00 - 20:59
    night       = 21:00 - 23:59
    late_night  = 00:00 - 04:59
    """
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    elif 21 <= hour < 24:
        return "night"
    else:
        return "late_night"


def get_day_type() -> str:
    """Returns 'weekday' or 'weekend'."""
    return "weekend" if datetime.now().weekday() >= 5 else "weekday"


# ── Personality injection builder ─────────────────────────────
TIME_SLOT_BEHAVIORS = {
    "morning":    "It's morning. Sanket is starting his day. Be energetic, motivating, and clear.",
    "afternoon":  "It's afternoon. Sanket is likely in work/study mode. Be efficient and focused.",
    "evening":    "It's evening. Sanket is entering his highly active and productive phase. Match his high energy and focus.",
    "night":      "It's night. Sanket is in his peak productive zone right now. Be sharp, efficient, and assist him in his flow state.",
    "late_night": "It's late night. Sanket usually sleeps between 12-1 AM. If it's past 12 AM, gently remind him to sleep. Keep responses calm and concise.",
}

TOPIC_BEHAVIORS = {
    "coding":   "Sanket is working on code. Be technical and precise. Skip social filler. Get to the point.",
    "learning": "Sanket wants to understand something. Be a good teacher — clear, simple, use examples.",
    "casual":   "Sanket is in casual mode. Be friendly, conversational, slightly playful.",
    "venting":  "Sanket is venting or frustrated. Acknowledge briefly, then solve. Don't lecture.",
    "planning": "Sanket is planning or ideating. Be collaborative and constructive. Build on his ideas.",
    "general":  "",  # No extra injection for general topic
}

SESSION_DEPTH_BEHAVIORS = {
    "new":      "This is early in the conversation. Introduce yourself naturally if asked. Don't assume context.",
    "mid":      "You've been talking for a while. You have context. Reference it naturally when relevant.",
    "deep":     "This is a long session. Sanket trusts you. Be more direct, skip formalities entirely.",
}

def get_session_depth_label() -> str:
    count = get_message_count()
    if count <= 3:
        return "new"
    elif count <= 15:
        return "mid"
    else:
        return "deep"


def get_personality_injection() -> str:
    """
    Builds the full personality context string to inject into system prompt.
    Returns empty string if all components are neutral/general.
    """
    parts = []

    time_slot = get_time_slot()
    time_behavior = TIME_SLOT_BEHAVIORS.get(time_slot, "")
    if time_behavior:
        parts.append(time_behavior)

    topic = get_last_topic()
    topic_behavior = TOPIC_BEHAVIORS.get(topic, "")
    if topic_behavior:
        parts.append(topic_behavior)

    depth_label = get_session_depth_label()
    depth_behavior = SESSION_DEPTH_BEHAVIORS.get(depth_label, "")
    if depth_behavior:
        parts.append(depth_behavior)

    day_type = get_day_type()
    if day_type == "weekend":
        parts.append("It's the weekend — Sanket might be more relaxed or exploring side projects.")

    return " | ".join(parts) if parts else ""
