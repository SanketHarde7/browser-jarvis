# Path: backend/modules/memory.py
# Use: Manages session history and persistent user context.
"""
memory.py — MAX v4.0
Added: Personality evolution tracking, auto fact extraction, buddy tone.
"""
import json
import os
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

class MemoryManager:
    """
    Manages conversation memory with:
    - Context window (last N messages)
    - Auto-summarization when threshold exceeded
    - Persistent JSON storage
    - User fact extraction & Permanent Rules
    - Personality evolution profile
    """
    
    def __init__(self, memory_file: str, max_messages: int = 5, summarize_threshold: int = 50):
        self.memory_file = Path(memory_file)
        self.max_messages = max_messages
        self.max_history_retention = 50
        self.summarize_threshold = summarize_threshold
        self._lock = asyncio.Lock()
        
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.memory = self._load_memory()
    
    def _load_memory(self) -> Dict:
        """Load memory from JSON file or return fresh structure."""
        try:
            if self.memory_file.exists():
                content = self.memory_file.read_text(encoding='utf-8').strip()
                if not content:
                    logger.warning(f"⚠️ Memory file empty, resetting: {self.memory_file}")
                    return self._fresh_memory()
                    
                data = json.loads(content)
                logger.info(f"📦 Loaded memory with {len(data.get('messages', []))} messages")
                return data
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Memory file corrupted, resetting: {e}")
        except Exception as e:
            logger.error(f"❌ Failed to load memory: {e}")
        
        return self._fresh_memory()
    
    def _fresh_memory(self) -> Dict:
        """Create fresh memory structure."""
        return {
            "session_id": datetime.now().isoformat(),
            "messages": [],
            "summary": "",
            "user_facts": {
                "name": "the user",
                "location": "Maharashtra",
                "preferences": {}
            },
            "personality_profile": {
                "prefers_short_answers": False,
                "main_domain": "coding",
                "humor_level": "medium",
                "total_interactions": 0,
                "last_greeting": ""
            },
            "created_at": datetime.now().isoformat()
        }
    
    def _save_to_disk(self) -> bool:
        """Write memory to disk (call inside lock only)."""
        try:
            temp_file = self.memory_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, indent=2, ensure_ascii=False)
            try:
                temp_file.replace(self.memory_file)
            except OSError:
                with open(self.memory_file, 'w', encoding='utf-8') as f:
                    json.dump(self.memory, f, indent=2, ensure_ascii=False)
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
            return True
        except Exception as e:
            logger.error(f"❌ Failed to save memory to disk: {e}")
            return False
    
    async def save_memory(self) -> bool:
        """Persist memory to JSON file (thread-safe)."""
        async with self._lock:
            return self._save_to_disk()
    
    async def add_message(self, role: str, content: str) -> bool:
        """Add a message to conversation history."""
        try:
            async with self._lock:
                self.memory["messages"].append({
                    "role": role,
                    "content": content,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Update interaction count
                self.memory.setdefault("personality_profile", {})["total_interactions"] = \
                    self.memory["personality_profile"].get("total_interactions", 0) + 1
                
                # Check if summarization needed
                if len(self.memory["messages"]) >= self.summarize_threshold:
                    self._auto_summarize_internal()
                
                # Keep extended history retention (up to 50 messages) for on-demand skill recall
                if len(self.memory["messages"]) > self.max_history_retention:
                    self.memory["messages"] = self.memory["messages"][-self.max_history_retention:]
                
                return self._save_to_disk()
                
        except Exception as e:
            logger.error(f"❌ Failed to add message: {e}")
            return False
    
    def _auto_summarize_internal(self) -> bool:
        """Summarize older messages to save tokens. Must be called inside lock."""
        try:
            messages = self.memory["messages"]
            if len(messages) <= self.summarize_threshold:
                return True
            
            kept_messages = messages[:2] + messages[-30:]
            middle_messages = messages[2:-30]
            
            summary_parts = [
                f"[{m['role']}] {m['content'][:100]}..." 
                for m in middle_messages[:5]
            ]
            new_summary = " | ".join(summary_parts)
            
            self.memory["messages"] = kept_messages
            self.memory["summary"] = new_summary
            logger.info(f"📝 Auto-summarized {len(middle_messages)} messages")
            
            return True
        except Exception as e:
            logger.error(f"❌ Summarization failed: {e}")
            return False
    
    def get_context(self) -> str:
        """Build context string for LLM prompt, injecting permanent rules first."""
        context_parts = []

        # --- Inject Permanent Rules ---
        rules_file = self.memory_file.parent / "permanent_rules.json"
        if rules_file.exists():
            try:
                rules = json.loads(rules_file.read_text(encoding='utf-8'))
                if rules:
                    rules_text = "CRITICAL PERMANENT RULES YOU MUST ALWAYS FOLLOW:\n"
                    for r in rules:
                        rules_text += f"- {r['rule']}\n"
                    context_parts.append(rules_text)
            except Exception as e:
                logger.warning(f"Could not load permanent rules: {e}")
        
        # --- Inject Personality Profile ---
        profile = self.memory.get("personality_profile", {})
        if profile:
            parts = []
            if profile.get("prefers_short_answers"):
                parts.append("User prefers SHORT answers.")
            domain = profile.get("main_domain")
            if domain:
                parts.append(f"User mainly asks about: {domain}")

            if parts:
                context_parts.append("PERSONALITY PROFILE:\n" + "\n".join(parts))
        
        # --- User Facts ---
        facts = self.memory.get("user_facts", {})
        if facts:
            fact_lines = [f"USER FACTS:"]
            for k, v in facts.items():
                if k != "preferences" and v:
                    fact_lines.append(f"- {k}: {v}")
            context_parts.append("\n".join(fact_lines))
        
        if self.memory.get("summary"):
            context_parts.append(f"PREVIOUS: {self.memory['summary']}")
        
        for msg in self.memory["messages"][-self.max_messages:]:
            role = "You" if msg["role"] == "user" else "Max"
            context_parts.append(f"{role}: {msg['content']}")
        
        return "\n".join(context_parts)
    
    async def clear_memory(self) -> bool:
        """Reset conversation history (keep user facts and profile)."""
        try:
            async with self._lock:
                user_facts = self.memory.get("user_facts", {})
                profile = self.memory.get("personality_profile", {})
                self.memory = self._fresh_memory()
                self.memory["user_facts"] = user_facts
                self.memory["personality_profile"] = profile
                return self._save_to_disk()
        except Exception as e:
            logger.error(f"❌ Failed to clear memory: {e}")
            return False
    
    def get_user_fact(self, key: str, default=None):
        return self.memory.get("user_facts", {}).get(key, default)
    
    async def update_user_fact(self, key: str, value) -> bool:
        """Update a user fact (async-safe)."""
        try:
            async with self._lock:
                self.memory.setdefault("user_facts", {})[key] = value
                return self._save_to_disk()
        except Exception as e:
            logger.error(f"❌ Failed to update user fact: {e}")

    def get_recent_history(self, limit: int = 20) -> str:
        """
        On-demand recall of past conversation history.
        Returns formatted transcript of the last `limit` messages.
        """
        messages = self.memory.get("messages", [])
        if not messages:
            return "No previous conversation history found."
        
        recent = messages[-limit:]
        lines = [f"=== RECENT CONVERSATION HISTORY (Last {len(recent)} messages) ==="]
        for idx, m in enumerate(recent, 1):
            role = "Sanket" if m["role"] == "user" else "MAX"
            timestamp = m.get("timestamp", "")
            time_str = f" [{timestamp[11:16]}]" if len(timestamp) >= 16 else ""
            lines.append(f"{idx}. {role}{time_str}: {m['content']}")
        
        return "\n".join(lines)

    async def extract_and_store_facts(self, user_text: str) -> List[str]:
        """
        Simple pattern-based fact extraction.
        e.g. 'Mera naam the user hai' -> name=the user
        """
        import re
        facts_found = []
        text_lower = user_text.lower()
        
        # Name patterns
        name_patterns = [
            r"mera naam (\w+) hai",
            r"my name is (\w+)",
            r"main (\w+) hoon",
            r"call me (\w+)",
        ]
        for p in name_patterns:
            m = re.search(p, text_lower)
            if m:
                name = m.group(1).title()
                await self.update_user_fact("name", name)
                facts_found.append(f"name={name}")
                break
        
        # Location patterns
        loc_patterns = [
            r"main (\w+) mein rehta hoon",
            r"main (\w+) mein rehti hoon",
            r"i live in (\w+)",
            r"i am from (\w+)",
        ]
        for p in loc_patterns:
            m = re.search(p, text_lower)
            if m:
                loc = m.group(1).title()
                await self.update_user_fact("location", loc)
                facts_found.append(f"location={loc}")
                break
        
        # Preference patterns
        pref_patterns = [
            (r"mujhe (\w+) pasand hai", "likes"),
            (r"i love (\w+)", "likes"),
            (r"i hate (\w+)", "dislikes"),
            (r"mujhe (\w+) nahi pasand", "dislikes"),
        ]
        for p, category in pref_patterns:
            m = re.search(p, text_lower)
            if m:
                item = m.group(1)
                prefs = self.memory.get("user_facts", {}).get("preferences", {})
                prefs.setdefault(category, []).append(item)
                await self.update_user_fact("preferences", prefs)
                facts_found.append(f"{category}={item}")
        
        return facts_found
    
    async def update_personality(self, response_length: int, skill_used: str = "") -> bool:
        """Update personality profile based on interaction patterns."""
        try:
            async with self._lock:
                profile = self.memory.setdefault("personality_profile", {})
                interactions = profile.get("total_interactions", 0)
                
                # Track short answer preference
                if interactions > 10:
                    avg_len = sum(len(m.get("content", "")) for m in self.memory["messages"][-20:] if m["role"] == "assistant") / max(1, len([m for m in self.memory["messages"][-20:] if m["role"] == "assistant"]))
                    profile["prefers_short_answers"] = avg_len < 150
                
                # Track domain
                if skill_used:
                    code_skills = {"write_code", "run_code", "code_review", "fix_code", "project_scaffold"}
                    if skill_used in code_skills:
                        profile["main_domain"] = "coding"
                    elif skill_used in {"search", "weather", "youtube_search"}:
                        profile["main_domain"] = "information"
                    elif skill_used in {"open_app", "web_open", "volume", "brightness", "lock_pc"}:
                        profile["main_domain"] = "pc_control"
                
                return self._save_to_disk()
        except Exception as e:
            logger.error(f"Personality update failed: {e}")
            return False


_memory_instance: Optional[MemoryManager] = None

def get_memory_manager(config) -> MemoryManager:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = MemoryManager(
            memory_file=config.MEMORY_FILE,
            max_messages=config.MEMORY_MAX_MESSAGES,
            summarize_threshold=config.MEMORY_SUMMARIZE_THRESHOLD
        )
    return _memory_instance
