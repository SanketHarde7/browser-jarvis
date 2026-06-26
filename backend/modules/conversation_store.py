# conversation_store.py — In-memory multi-turn history for MAX
# Stores last N message pairs per session.

from collections import deque
from typing import List, Dict

MAX_HISTORY_TURNS = 6          # Keep last 6 messages (3 user + 3 assistant)
_history: deque = deque(maxlen=MAX_HISTORY_TURNS)

def add_user_message(text: str) -> None:
    """Call this BEFORE sending to LLM."""
    _history.append({"role": "user", "content": text.strip()[:2000]})

def add_assistant_message(text: str) -> None:
    """Call this AFTER receiving LLM response."""
    if text and text.strip():
        _history.append({"role": "assistant", "content": text.strip()[:1000]})

def get_history() -> List[Dict]:
    """Returns list of dicts ready to inject into Groq messages[]."""
    return list(_history)

def clear_history() -> None:
    """Call this on session reset or quit_max skill."""
    _history.clear()
