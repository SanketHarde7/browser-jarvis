# Path: backend/modules/gemini_router.py
# Use: Smart API router — lightweight tasks to Gemini Flash Lite, heavy tasks to Groq.
"""
gemini_router.py — MAX v5.0

Routes lightweight classification tasks to Gemini Flash Lite (free tier: 1000-1500 req/day)
while keeping main LLM responses on Groq (unlimited).

Budget Protection:
  - Daily counter stored in backend/data/gemini_usage.json
  - Auto-resets at midnight
  - If budget exhausted → silently falls back to local/Groq
  - Conservative budget: 800 req/day (leaves 200-700 buffer)
"""

import os
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, Any, List

logger = logging.getLogger("MAX.GEMINI_ROUTER")


# ═══════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════

DAILY_BUDGET = 800  # Conservative (actual free tier: 1000-1500)
USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "gemini_usage.json"
GEMINI_MODEL = "gemini-2.0-flash-lite"  # Free tier model


# ═══════════════════════════════════════════════════
# GEMINI ROUTER
# ═══════════════════════════════════════════════════

class GeminiRouter:
    """
    Smart API router that sends lightweight tasks to Gemini Flash Lite
    and falls back gracefully when budget is exhausted.
    """

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._today_count = 0
        self._today_date = ""
        self._load_usage()

    # ── Budget Tracking ───────────────────────────────────────

    def _load_usage(self):
        """Load today's usage count from disk."""
        try:
            if USAGE_FILE.exists():
                data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
                stored_date = data.get("date", "")
                if stored_date == str(date.today()):
                    self._today_count = data.get("count", 0)
                    self._today_date = stored_date
                else:
                    # New day — reset counter
                    self._today_count = 0
                    self._today_date = str(date.today())
                    self._save_usage()
            else:
                self._today_count = 0
                self._today_date = str(date.today())
        except Exception as e:
            logger.warning(f"Could not load Gemini usage: {e}")
            self._today_count = 0
            self._today_date = str(date.today())

    def _save_usage(self):
        """Persist usage counter to disk."""
        try:
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "date": self._today_date,
                "count": self._today_count,
                "budget": DAILY_BUDGET,
                "remaining": max(0, DAILY_BUDGET - self._today_count),
                "last_updated": datetime.now().isoformat()
            }
            USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save Gemini usage: {e}")

    def _check_budget(self) -> bool:
        """Check if we have remaining Gemini budget for today."""
        # Auto-reset on new day
        today = str(date.today())
        if self._today_date != today:
            self._today_count = 0
            self._today_date = today
            self._save_usage()

        if not self._api_key:
            return False

        return self._today_count < DAILY_BUDGET

    def _increment_usage(self):
        """Increment daily usage counter."""
        self._today_count += 1
        # Save every 10 requests to reduce disk I/O
        if self._today_count % 10 == 0:
            self._save_usage()

    def get_usage_stats(self) -> Dict[str, Any]:
        """Return current usage stats."""
        return {
            "date": self._today_date,
            "used": self._today_count,
            "budget": DAILY_BUDGET,
            "remaining": max(0, DAILY_BUDGET - self._today_count),
            "available": self._check_budget()
        }

    # ── Gemini API Call ───────────────────────────────────────

    async def _call_gemini(self, prompt: str, max_tokens: int = 100, temperature: float = 0.0) -> Optional[str]:
        """
        Make a lightweight Gemini Flash Lite API call.
        Returns response text or None if failed/budget exhausted.
        """
        if not self._check_budget():
            logger.debug("Gemini budget exhausted — skipping.")
            return None

        try:
            import httpx

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={self._api_key}"

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature
                }
            }

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            self._increment_usage()
                            return parts[0]["text"].strip()
                else:
                    logger.warning(f"Gemini API error {r.status_code}: {r.text[:150]}")
                    return None
        except Exception as e:
            logger.warning(f"Gemini call failed: {e}")
            return None

    # ── Lightweight Task: Fact Extraction ─────────────────────

    async def extract_facts(self, conversation_text: str) -> Dict[str, str]:
        """
        Use Gemini Flash Lite to intelligently extract user facts from conversation.
        Called after every Nth interaction (not every message).
        
        Returns dict of extracted facts or empty dict on failure.
        """
        prompt = f"""Extract personal facts from this conversation text. Return ONLY a JSON object.
Keys: name, location, job, preferences, likes, dislikes. Only include keys with actual values found.
If no facts found, return {{}}.

Text: "{conversation_text[:500]}"

JSON:"""

        result = await self._call_gemini(prompt, max_tokens=80, temperature=0.0)
        if not result:
            return {}

        try:
            # Strip markdown code blocks if present
            clean = result.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(clean)
        except (json.JSONDecodeError, Exception):
            return {}

    # ── Lightweight Task: Skill Validation ────────────────────

    async def validate_skill_match(self, query: str, candidates: List[str]) -> Optional[str]:
        """
        Use Gemini Flash Lite to validate/pick the best skill for ambiguous queries.
        Only called when SkillRAG confidence is low.
        
        Returns the best skill name or None.
        """
        candidates_str = ", ".join(candidates[:8])
        prompt = f"""Given user query and available skills, pick the SINGLE best skill.
Reply with ONLY the skill name, nothing else.

Query: "{query}"
Available skills: {candidates_str}

Best skill:"""

        result = await self._call_gemini(prompt, max_tokens=20, temperature=0.0)
        if result and result.strip().lower() in [c.lower() for c in candidates]:
            return result.strip().lower()
        return None


# ═══════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════

_instance: Optional[GeminiRouter] = None

def get_gemini_router(api_key: str = "") -> GeminiRouter:
    """Get or create the singleton GeminiRouter."""
    global _instance
    if _instance is None:
        _instance = GeminiRouter(api_key)
    return _instance
