# Path: backend/modules/learning_engine.py
# Use: Self-learning adaptive intelligence — MAX gets smarter with every interaction.
"""
learning_engine.py — MAX v5.0

Implements implicit RLHF (Reinforcement Learning from Human Feedback):
  - Correction Memory: stores what MAX did wrong vs what was expected
  - Skill Override Learning: permanently learns corrected skill mappings
  - Response Style Tracking: adapts to user's preferred tone/length
  - Usage Patterns: most-used skills, time patterns, frequent queries
  - Positive Reinforcement: reinforces behaviors when user says "nice", "perfect"

All learning is stored in backend/data/learning_journal.json
Injected as compressed context (~30-50 tokens) into every prompt.
"""

import re
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from collections import Counter

logger = logging.getLogger("MAX.LEARNING")

JOURNAL_FILE = Path(__file__).resolve().parent.parent / "data" / "learning_journal.json"

# ── Detection Patterns ────────────────────────────────────

_NEGATIVE_FEEDBACK = re.compile(
    r"\b(wrong|galat|nahi|not that|no no|incorrect|ye nahi|that's wrong|"
    r"mat karo|ruko|stop|aise nahi|idhar nahi|nahi yaar|fix this|fix karo|"
    r"ye sahi nahi|not what i asked|not right)\b",
    re.IGNORECASE
)

_POSITIVE_FEEDBACK = re.compile(
    r"\b(nice|perfect|good|great|correct|sahi hai|theek hai|"
    r"exactly|well done|awesome|amazing|thanks|thank you|"
    r"bahut accha|very good|love it|nailed it)\b",
    re.IGNORECASE
)

_CORRECTION_PATTERNS = re.compile(
    r"\b(i said|i meant|i asked for|maine kaha tha|maine bola tha|"
    r"not .+ but .+|instead of .+|should have .+|chahiye tha)\b",
    re.IGNORECASE
)


# ═══════════════════════════════════════════════════
# LEARNING ENGINE
# ═══════════════════════════════════════════════════

class LearningEngine:
    """
    Self-learning adaptive engine for MAX.
    Tracks corrections, preferences, and usage to make MAX progressively smarter.
    """

    def __init__(self):
        self._journal = self._load_journal()
        self._last_interaction: Optional[Dict] = None  # Track last MAX action for feedback detection
        self._interaction_count_since_save = 0

    def _load_journal(self) -> Dict:
        """Load learning journal from disk or create fresh."""
        try:
            if JOURNAL_FILE.exists():
                data = json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
                logger.info(f"📚 Loaded learning journal: {len(data.get('corrections', []))} corrections, "
                            f"{data.get('usage_stats', {}).get('total_interactions', 0)} total interactions")
                return data
        except Exception as e:
            logger.warning(f"Could not load learning journal: {e}")
        return self._fresh_journal()

    def _fresh_journal(self) -> Dict:
        """Create empty learning journal structure."""
        return {
            "corrections": [],
            "skill_overrides": {},
            "response_preferences": {
                "avg_preferred_length": 60,
                "prefers_casual_tone": True,
                "dislikes_words": ["certainly", "absolutely", "of course"],
                "likes_directness": True
            },
            "usage_stats": {
                "top_skills": {},
                "peak_hours": {},
                "total_interactions": 0,
                "total_corrections": 0,
                "total_positive": 0
            },
            "positive_reinforcements": [],
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat()
        }

    def _save_journal(self):
        """Persist learning journal to disk."""
        try:
            JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._journal["last_updated"] = datetime.now().isoformat()
            JOURNAL_FILE.write_text(
                json.dumps(self._journal, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Failed to save learning journal: {e}")

    # ── Interaction Recording ─────────────────────────────────

    def record_interaction(self, user_query: str, max_response: str, 
                           skill_used: Optional[str] = None):
        """
        Record every interaction for pattern learning.
        Called after MAX responds to a query.
        """
        # Store last interaction for feedback detection
        self._last_interaction = {
            "timestamp": datetime.now().isoformat(),
            "user_query": user_query[:200],
            "max_response": max_response[:200],
            "skill_used": skill_used
        }

        # Update usage stats
        stats = self._journal.setdefault("usage_stats", {})
        stats["total_interactions"] = stats.get("total_interactions", 0) + 1

        # Track skill usage
        if skill_used:
            # Extract base skill name from tag
            skill_name = skill_used
            if "[SKILL:" in skill_name:
                parts = skill_name.replace("[SKILL:", "").replace("]", "").split(":")
                skill_name = parts[0].strip() if parts else skill_used

            top_skills = stats.setdefault("top_skills", {})
            top_skills[skill_name] = top_skills.get(skill_name, 0) + 1

        # Track peak hours
        hour = str(datetime.now().hour)
        peak_hours = stats.setdefault("peak_hours", {})
        peak_hours[hour] = peak_hours.get(hour, 0) + 1

        # Track response length preferences
        prefs = self._journal.setdefault("response_preferences", {})
        # Moving average of response lengths user engages with
        current_avg = prefs.get("avg_preferred_length", 60)
        prefs["avg_preferred_length"] = int(0.9 * current_avg + 0.1 * len(max_response))

        # Auto-save periodically (every 5 interactions to reduce I/O)
        self._interaction_count_since_save += 1
        if self._interaction_count_since_save >= 5:
            self._save_journal()
            self._interaction_count_since_save = 0

    # ── Feedback Detection ────────────────────────────────────

    def detect_feedback(self, user_text: str) -> Optional[str]:
        """
        Analyze user's message for feedback signals about MAX's previous response.
        Returns: "positive", "negative", "correction", or None.
        """
        if _CORRECTION_PATTERNS.search(user_text):
            return "correction"
        if _NEGATIVE_FEEDBACK.search(user_text):
            return "negative"
        if _POSITIVE_FEEDBACK.search(user_text):
            return "positive"
        return None

    def process_feedback(self, user_text: str, feedback_type: str):
        """
        Process detected feedback and update learning journal.
        """
        if not self._last_interaction:
            return

        last = self._last_interaction
        stats = self._journal.setdefault("usage_stats", {})

        if feedback_type in ("negative", "correction"):
            # Store correction
            correction = {
                "timestamp": datetime.now().isoformat(),
                "user_query": last.get("user_query", ""),
                "max_did": last.get("max_response", "")[:100],
                "user_feedback": user_text[:200],
                "skill_used": last.get("skill_used", ""),
                "lesson": f"When user said '{last.get('user_query', '')[:80]}', MAX responded incorrectly. User correction: '{user_text[:100]}'"
            }

            corrections = self._journal.setdefault("corrections", [])
            corrections.append(correction)
            # Keep last 50 corrections (prevent unbounded growth)
            if len(corrections) > 50:
                self._journal["corrections"] = corrections[-50:]

            stats["total_corrections"] = stats.get("total_corrections", 0) + 1
            logger.info(f"📝 Learning: Stored correction #{stats['total_corrections']}")

            # If skill was used, try to learn skill override
            if last.get("skill_used"):
                query_key = last["user_query"].lower().strip()[:60]
                # Don't auto-override, just flag for next similar query
                # The override will be refined when the user explicitly corrects the skill

        elif feedback_type == "positive":
            reinforcement = {
                "timestamp": datetime.now().isoformat(),
                "query_pattern": last.get("user_query", "")[:80],
                "response_style": "brief" if len(last.get("max_response", "")) < 100 else "detailed",
                "skill_used": last.get("skill_used", "")
            }

            positives = self._journal.setdefault("positive_reinforcements", [])
            positives.append(reinforcement)
            # Keep last 30 positive signals
            if len(positives) > 30:
                self._journal["positive_reinforcements"] = positives[-30:]

            stats["total_positive"] = stats.get("total_positive", 0) + 1
            logger.info(f"✅ Learning: Positive reinforcement #{stats['total_positive']}")

        self._save_journal()

    def learn_skill_override(self, query_pattern: str, correct_skill: str):
        """
        Permanently learn that a specific query pattern maps to a specific skill.
        Called when user explicitly corrects MAX's skill choice.
        """
        overrides = self._journal.setdefault("skill_overrides", {})
        overrides[query_pattern.lower().strip()[:60]] = correct_skill
        logger.info(f"🧠 Learning: Skill override '{query_pattern[:40]}' → {correct_skill}")
        self._save_journal()

    # ── Learning Context Injection ────────────────────────────

    def get_learning_context(self) -> str:
        """
        Generate compressed learning context for LLM prompt injection.
        Target: ~30-50 tokens.
        """
        parts = []

        # Response preferences
        prefs = self._journal.get("response_preferences", {})
        avg_len = prefs.get("avg_preferred_length", 60)
        if avg_len < 80:
            parts.append("User prefers SHORT direct responses.")
        elif avg_len > 150:
            parts.append("User prefers detailed responses.")

        # Recent corrections (last 3, compressed)
        corrections = self._journal.get("corrections", [])
        if corrections:
            recent = corrections[-3:]
            correction_lines = []
            for c in recent:
                query = c.get("user_query", "")[:40]
                feedback = c.get("user_feedback", "")[:40]
                correction_lines.append(f'"{query}" → {feedback}')
            if correction_lines:
                parts.append("PAST CORRECTIONS: " + "; ".join(correction_lines))

        # Top skills (for context about what user commonly does)
        top_skills = self._journal.get("usage_stats", {}).get("top_skills", {})
        if top_skills:
            sorted_skills = sorted(top_skills.items(), key=lambda x: x[1], reverse=True)[:3]
            skill_list = ", ".join([s[0] for s in sorted_skills])
            parts.append(f"Most used: {skill_list}.")

        return "\n".join(parts) if parts else ""

    # ── Stats & Diagnostics ───────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return learning stats summary."""
        stats = self._journal.get("usage_stats", {})
        return {
            "total_interactions": stats.get("total_interactions", 0),
            "total_corrections": stats.get("total_corrections", 0),
            "total_positive": stats.get("total_positive", 0),
            "skill_overrides": len(self._journal.get("skill_overrides", {})),
            "top_skills": dict(sorted(
                stats.get("top_skills", {}).items(),
                key=lambda x: x[1], reverse=True
            )[:5]),
            "accuracy_rate": self._calculate_accuracy()
        }

    def _calculate_accuracy(self) -> str:
        """Calculate approximate accuracy based on correction ratio."""
        stats = self._journal.get("usage_stats", {})
        total = stats.get("total_interactions", 0)
        corrections = stats.get("total_corrections", 0)
        if total == 0:
            return "N/A"
        accuracy = ((total - corrections) / total) * 100
        return f"{accuracy:.1f}%"


# ═══════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════

_instance: Optional[LearningEngine] = None

def get_learning_engine() -> LearningEngine:
    """Get or create the singleton LearningEngine."""
    global _instance
    if _instance is None:
        _instance = LearningEngine()
    return _instance
