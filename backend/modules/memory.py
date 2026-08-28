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
    
    def get_recent_messages(self, limit: int = 5) -> List[Dict]:
        """Return raw messages for subagents."""
        return self.memory.get("messages", [])[-limit:]
    
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
        Pattern-based + Gemini Flash Lite intelligent fact extraction.
        e.g. 'Mera naam Sanket hai' -> name=Sanket
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
        
        # If pattern matching found nothing and text has enough substance, try Gemini Router
        if not facts_found and len(user_text.strip()) > 15:
            try:
                from modules.gemini_router import get_gemini_router
                gemini_facts = await get_gemini_router().extract_facts(user_text)
                for k, v in gemini_facts.items():
                    if k and v and isinstance(v, str):
                        await self.update_user_fact(k, v)
                        facts_found.append(f"{k}={v}")
            except Exception as e:
                logger.debug(f"Gemini fact extraction skipped: {e}")

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

    # ═══════════════════════════════════════════════════
    # MULTI-TIER MEMORY — Smart Context Retrieval
    # ═══════════════════════════════════════════════════

    def get_context_for_query(self, query: str, intent_type: str = "COMMAND") -> str:
        """
        Smart memory retriever — returns ONLY the memory relevant for this query type.
        
        Token-efficient context injection:
          CONVERSATION → short-term turns + user facts + personality (~50 tokens)
          COMMAND      → short-term turns only (~30 tokens)  
          MEMORY_RECALL → episodic search through past conversations (~100 tokens)
        """
        context_parts = []
        query_lower = query.lower()

        # ── Check if this is a memory recall query ──
        memory_recall_signals = [
            "remember", "pehle", "yesterday", "earlier", "last time",
            "what did we", "kya baat ki", "yaad", "recall", "history",
            "discussed", "past conversation"
        ]
        is_recall = any(s in query_lower for s in memory_recall_signals)

        if is_recall:
            # Episodic recall — search past conversations
            episodes = self._search_episodes(query, top_k=3)
            if episodes:
                context_parts.append("PAST CONVERSATIONS:\n" + episodes)
            # Also include factual context for continuity
            facts_ctx = self._get_factual_context()
            if facts_ctx:
                context_parts.append(facts_ctx)
            return "\n".join(context_parts)

        # ── Permanent Rules (always inject) ──
        rules_ctx = self._get_rules_context()
        if rules_ctx:
            context_parts.append(rules_ctx)

        if intent_type in ("CONVERSATION", "CAPABILITY_QUESTION"):
            # Chat: user facts + personality + short-term
            facts_ctx = self._get_factual_context()
            if facts_ctx:
                context_parts.append(facts_ctx)
            personality_ctx = self._get_personality_context()
            if personality_ctx:
                context_parts.append(personality_ctx)
            # Short-term (last 4 messages for chat continuity)
            short_term = self._get_short_term(limit=4)
            if short_term:
                context_parts.append(short_term)

        elif intent_type in ("COMMAND", "INFORMATION_QUESTION"):
            # Action: just short-term for pronoun resolution
            short_term = self._get_short_term(limit=3)
            if short_term:
                context_parts.append(short_term)

        else:
            # Fallback: standard context (original behavior)
            return self.get_context()

        return "\n".join(context_parts) if context_parts else "None"

    def _get_rules_context(self) -> str:
        """Get permanent rules (compact)."""
        rules_file = self.memory_file.parent / "permanent_rules.json"
        if not rules_file.exists():
            return ""
        try:
            rules = json.loads(rules_file.read_text(encoding='utf-8'))
            if rules:
                return "RULES: " + "; ".join(r['rule'][:60] for r in rules[:5])
        except Exception:
            pass
        return ""

    def _get_factual_context(self) -> str:
        """Get user facts (compact)."""
        facts = self.memory.get("user_facts", {})
        if not facts:
            return ""
        parts = []
        for k, v in facts.items():
            if k != "preferences" and v:
                parts.append(f"{k}: {v}")
        return "USER: " + ", ".join(parts) if parts else ""

    def _get_personality_context(self) -> str:
        """Get personality profile (compact)."""
        profile = self.memory.get("personality_profile", {})
        if not profile:
            return ""
        parts = []
        if profile.get("prefers_short_answers"):
            parts.append("prefers short answers")
        domain = profile.get("main_domain")
        if domain:
            parts.append(f"domain: {domain}")
        return "PROFILE: " + ", ".join(parts) if parts else ""

    def _get_short_term(self, limit: int = 4) -> str:
        """Get last N messages (compact)."""
        messages = self.memory.get("messages", [])
        if not messages:
            return ""
        recent = messages[-limit:]
        lines = []
        for m in recent:
            role = "You" if m["role"] == "user" else "Max"
            content = m["content"][:120]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # ── Episodic Memory ──────────────────────────────────

    def _get_episodes_file(self) -> Path:
        """Path to episodic memory JSON file."""
        return self.memory_file.parent / "episodic_memory.json"

    async def store_episode(self, user_text: str, max_response: str, skill_used: str = ""):
        """
        Store an interaction as an episodic memory entry.
        Called after every meaningful interaction.
        """
        try:
            episodes_file = self._get_episodes_file()
            episodes = []
            if episodes_file.exists():
                try:
                    episodes = json.loads(episodes_file.read_text(encoding='utf-8'))
                except Exception:
                    episodes = []

            episode = {
                "timestamp": datetime.now().isoformat(),
                "user": user_text[:200],
                "max": max_response[:200],
                "skill": skill_used or "",
                "hour": datetime.now().hour
            }
            episodes.append(episode)

            # Keep last 200 episodes (prevent unbounded growth)
            if len(episodes) > 200:
                episodes = episodes[-200:]

            episodes_file.parent.mkdir(parents=True, exist_ok=True)
            episodes_file.write_text(
                json.dumps(episodes, indent=1, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception as e:
            logger.warning(f"Failed to store episode: {e}")

    def _search_episodes(self, query: str, top_k: int = 3) -> str:
        """
        Search episodic memory for relevant past interactions.
        Uses simple keyword overlap scoring (fast, no ML dependency).
        """
        episodes_file = self._get_episodes_file()
        if not episodes_file.exists():
            return ""

        try:
            episodes = json.loads(episodes_file.read_text(encoding='utf-8'))
        except Exception:
            return ""

        if not episodes:
            return ""

        import re
        query_words = set(re.findall(r'\b\w+\b', query.lower()))
        # Remove common stop words
        stop_words = {"the", "a", "is", "was", "i", "you", "me", "my", "we", "did",
                      "what", "kya", "hai", "ki", "ka", "se", "ko", "ne", "thi", "tha"}
        query_words -= stop_words

        if not query_words:
            # No meaningful words — return last few episodes
            recent = episodes[-top_k:]
            return self._format_episodes(recent)

        scored = []
        for ep in episodes:
            text = (ep.get("user", "") + " " + ep.get("max", "")).lower()
            ep_words = set(re.findall(r'\b\w+\b', text))
            overlap = len(query_words & ep_words)
            if overlap > 0:
                scored.append((overlap, ep))

        if not scored:
            # No keyword matches — return most recent episodes
            return self._format_episodes(episodes[-top_k:])

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [ep for _, ep in scored[:top_k]]
        return self._format_episodes(top)

    def _format_episodes(self, episodes: list) -> str:
        """Format episodes for LLM context injection."""
        if not episodes:
            return ""
        lines = []
        for ep in episodes:
            ts = ep.get("timestamp", "")[:16]  # YYYY-MM-DDTHH:MM
            user_text = ep.get("user", "")[:80]
            max_text = ep.get("max", "")[:80]
            skill = ep.get("skill", "")
            skill_tag = f" [{skill}]" if skill else ""
            lines.append(f"[{ts}] You: {user_text} → Max: {max_text}{skill_tag}")
        return "\n".join(lines)


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

