"""
memory.py - MAX Multi-Tier Memory System

Industry-grade memory with token-efficient context retrieval, atomic writes,
and a vector-backed episodic recall layer. Backwards-compatible with the
public API used by agent_core.py and skills.py.

Memory tiers:
  1. WORKING       - last N turns, in-memory only, fastest
  2. SESSION       - current session summary, persisted (debounced)
  3. FACTUAL       - user facts, preferences, permanent rules
  4. EPISODIC      - past interactions, vector-indexed (ChromaDB if avail,
                     else TF-IDF fallback)
  5. SEMANTIC      - consolidated user profile, periodically regenerated
  6. PROCEDURAL    - successful skill patterns, used for skill selection

Storage:
  - One JSON file per tier, atomic write via .tmp + os.replace.
  - Save is DEBOUNCED: writes happen at most every N seconds OR when the
    pending queue hits M items, whichever first. shutdown()/flush() forces
    a final write.
  - All public methods are async-safe via a single asyncio.Lock.
  - Context builder enforces a TOKEN BUDGET (default 800 tokens) so the
    LLM prompt stays lean.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("MAX.MEMORY")

# Roughly 4 chars per token for English. Used to estimate and cap context size.
CHARS_PER_TOKEN = 4
DEFAULT_TOKEN_BUDGET = 800
# Debounce: save at most once every N seconds, OR after M pending writes.
DEBOUNCE_SECONDS = 5.0
DEBOUNCE_PENDING_MAX = 10
# Episodic: how many past interactions to keep.
EPISODIC_MAX_ENTRIES = 500
# Episodic: drop episodes older than this (days) unless pinned.
EPISODIC_TTL_DAYS = 90


# ---------------------------------------------------------------------------
# Atomic JSON store
# ---------------------------------------------------------------------------

class AtomicJSONStore:
    """Tiny atomic JSON file store. Single-writer, no external deps."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def load(self, default):
        async with self._lock:
            if not self.path.exists():
                return default
            try:
                txt = self.path.read_text(encoding="utf-8").strip()
                if not txt:
                    return default
                return json.loads(txt)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"AtomicJSONStore: failed to load {self.path}: {e}")
                return default

    async def save(self, data) -> bool:
        async with self._lock:
            return self._save_unlocked(data)

    def _save_unlocked(self, data) -> bool:
        """Write atomically: write to .tmp, then os.replace."""
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            # Write to a temp file in the same directory (so os.replace is atomic).
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            logger.error(f"AtomicJSONStore: save failed for {self.path}: {e}")
            return False


# ---------------------------------------------------------------------------
# Episodic recall (vector-light)
# ---------------------------------------------------------------------------

class EpisodicIndex:
    """
    Lightweight episodic recall. Uses TF-IDF cosine similarity by default.
    If ChromaDB is available, falls back to it for true vector search.
    """

    def __init__(self):
        self._use_chroma = False
        self._chroma_collection = None
        self._tfidf_vocab: Dict[str, int] = {}
        self._tfidf_idf: Dict[str, float] = {}
        self._episodes: List[Dict[str, Any]] = []
        self._dirty = True
        # Try chroma once; on failure, stay with TF-IDF.
        try:
            import chromadb  # noqa: F401
            self._use_chroma = True
        except Exception:
            self._use_chroma = False

    def load(self, episodes: List[Dict[str, Any]]):
        self._episodes = list(episodes)
        self._dirty = True

    def add(self, episode: Dict[str, Any]):
        self._episodes.append(episode)
        self._dirty = True

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        if not self._episodes:
            return []
        if not query.strip():
            return self._episodes[-top_k:]
        scored = self._score(query)
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:top_k]]

    def _score(self, query: str) -> List[Tuple[float, Dict[str, Any]]]:
        if self._dirty:
            self._rebuild_tfidf()
        q_vec = self._vectorize(query)
        out = []
        for ep in self._episodes:
            text = (ep.get("user", "") + " " + ep.get("max", "")).lower()
            v = self._vectorize(text, prebuilt=True)
            sim = self._cosine(q_vec, v)
            if sim > 0.0:
                out.append((sim, ep))
        return out

    def _rebuild_tfidf(self):
        from collections import Counter
        import math
        docs = [(ep.get("user", "") + " " + ep.get("max", "")).lower() for ep in self._episodes]
        df: Counter = Counter()
        tokenized_docs = []
        for d in docs:
            tokens = self._tokenize(d)
            tokenized_docs.append(tokens)
            for t in set(tokens):
                df[t] += 1
        n = max(1, len(docs))
        self._tfidf_vocab = {}
        self._tfidf_idf = {}
        for idx, (tok, count) in enumerate(df.items()):
            self._tfidf_vocab[tok] = idx
            self._tfidf_idf[tok] = math.log((n + 1) / (count + 1)) + 1.0
        # Cache per-doc vectors
        self._cached_doc_vecs = [
            self._vectorize_from_tokens(toks) for toks in tokenized_docs
        ]
        self._dirty = False

    def _tokenize(self, text: str) -> List[str]:
        # Keep alphanumerics and a few Hinglish-friendly chars.
        return re.findall(r"\b[\w']+\b", text.lower())

    def _vectorize(self, text: str, prebuilt: bool = False):
        from collections import Counter
        tokens = self._tokenize(text)
        return self._vectorize_from_tokens(tokens)

    def _vectorize_from_tokens(self, tokens: List[str]):
        from collections import Counter
        tf = Counter(tokens)
        vec = {}
        for tok, c in tf.items():
            if tok in self._tfidf_vocab:
                vec[self._tfidf_vocab[tok]] = c * self._tfidf_idf.get(tok, 1.0)
        return vec

    def _cosine(self, a, b) -> float:
        if not a or not b:
            return 0.0
        dot = sum(a.get(k, 0) * b.get(k, 0) for k in a.keys() & b.keys())
        na = sum(v * v for v in a.values()) ** 0.5
        nb = sum(v * v for v in b.values()) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


# ---------------------------------------------------------------------------
# Main MemoryManager
# ---------------------------------------------------------------------------

class MemoryManager:
    """
    Multi-tier memory for MAX.

    Public API (kept compatible with the previous module):
      - add_message(role, content)
      - get_context() -> str  (legacy; calls build_context("legacy"))
      - get_context_for_query(query, intent_type) -> str
      - clear_memory() -> bool
      - get_recent_messages(limit) -> List[Dict]
      - get_user_fact(key, default)
      - update_user_fact(key, value) -> bool
      - extract_and_store_facts(text) -> List[str]
      - update_personality(response_length, skill_used) -> bool
      - store_episode(user_text, max_text, skill_used)
      - get_recent_history(limit) -> str
    """

    def __init__(
        self,
        memory_file: str,
        max_messages: int = 6,
        summarize_threshold: int = 50,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ):
        self.memory_file = Path(memory_file)
        self.data_dir = self.memory_file.parent
        self.max_messages = max_messages
        self.summarize_threshold = summarize_threshold
        self.token_budget = token_budget
        self._lock = asyncio.Lock()

        # Per-tier stores
        self._store = AtomicJSONStore(self.memory_file)
        self._episodic_store = AtomicJSONStore(self.data_dir / "episodic_memory.json")
        self._semantic_store = AtomicJSONStore(self.data_dir / "semantic_profile.json")
        self._procedural_store = AtomicJSONStore(self.data_dir / "procedural_memory.json")

        # Episodic index (in-process; rebuilt on load)
        self._episodic = EpisodicIndex()

        # Pending write bookkeeping for debouncing
        self._pending = 0
        self._last_save = 0.0
        self._save_task: Optional[asyncio.Task] = None

        # Loaded state
        self.memory: Dict[str, Any] = {}
        self.semantic: Dict[str, Any] = {}
        self.procedural: Dict[str, Any] = {}
        self.episodes: List[Dict[str, Any]] = []

    # ── lifecycle ────────────────────────────────────────────────────────

    async def initialize(self):
        """Load all tiers from disk. Call once at startup."""
        async with self._lock:
            self.memory = await self._store.load(self._fresh_memory())
            self.semantic = await self._semantic_store.load(self._fresh_semantic())
            self.procedural = await self._procedural_store.load(self._fresh_procedural())
            self.episodes = await self._episodic_store.load([])
            # Drop expired episodes
            self.episodes = self._filter_expired(self.episodes)
            self._episodic.load(self.episodes)
            self._pending = 0
            self._last_save = time.time()
            logger.info(
                f"Memory loaded: {len(self.memory.get('messages', []))} msgs, "
                f"{len(self.episodes)} episodes, "
                f"{len(self.semantic.get('traits', []))} semantic traits"
            )

    async def flush(self):
        """Force a save of all dirty tiers. Call on shutdown."""
        async with self._lock:
            await self._flush_unlocked()

    async def _flush_unlocked(self):
        ok = True
        if self._pending > 0:
            ok &= self._store._save_unlocked(self.memory)
            self._pending = 0
        if self._episodic_dirty():
            ok &= self._episodic_store._save_unlocked(self.episodes)
        if self._semantic_dirty():
            ok &= self._semantic_store._save_unlocked(self.semantic)
        if self._procedural_dirty():
            ok &= self._procedural_store._save_unlocked(self.procedural)
        self._last_save = time.time()
        return ok

    # ── working + session ────────────────────────────────────────────────

    async def add_message(self, role: str, content: str) -> bool:
        """Add a message to working memory. Async-safe, debounced save."""
        if not content or not content.strip():
            return False
        content = content.strip()
        async with self._lock:
            # Dedup: skip if previous message has same role and near-identical content
            msgs = self.memory.setdefault("messages", [])
            if msgs and msgs[-1].get("role") == role:
                prev = msgs[-1].get("content", "")
                if self._similar(prev, content) > 0.85:
                    logger.debug("Skipped near-duplicate message")
                    return False

            msgs.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            })
            # Bump interaction counter
            profile = self.memory.setdefault("personality_profile", {})
            profile["total_interactions"] = profile.get("total_interactions", 0) + 1

            # Compact when working set grows
            if len(msgs) > self.summarize_threshold:
                self._compact_working_unlocked()
            # Hard cap working set
            if len(msgs) > 100:
                self.memory["messages"] = msgs[-100:]

            self._pending += 1
            await self._maybe_debounce_save_unlocked()
            return True

    def _compact_working_unlocked(self):
        """Summarize older messages into the session summary, keep recent ones."""
        msgs = self.memory["messages"]
        if len(msgs) <= self.summarize_threshold:
            return
        keep_head = 2
        keep_tail = 30
        head = msgs[:keep_head]
        middle = msgs[keep_head:-keep_tail]
        tail = msgs[-keep_tail:]

        if middle:
            parts = [f"[{m['role']}] {m['content'][:80]}" for m in middle[:6]]
            new_chunk = " | ".join(parts)
            existing = self.memory.get("summary", "")
            self.memory["summary"] = (existing + " | " + new_chunk).strip(" |")
            # Cap summary length
            if len(self.memory["summary"]) > 2000:
                self.memory["summary"] = self.memory["summary"][-2000:]
        self.memory["messages"] = head + tail

    async def get_recent_messages(self, limit: int = 5) -> List[Dict[str, Any]]:
        async with self._lock:
            return list(self.memory.get("messages", [])[-limit:])

    # ── factual ──────────────────────────────────────────────────────────

    def get_user_fact(self, key: str, default=None):
        return self.memory.get("user_facts", {}).get(key, default)

    async def update_user_fact(self, key: str, value) -> bool:
        async with self._lock:
            self.memory.setdefault("user_facts", {})[key] = value
            self._pending += 1
            await self._maybe_debounce_save_unlocked()
            return True

    async def update_personality(self, response_length: int, skill_used: str = "") -> bool:
        async with self._lock:
            profile = self.memory.setdefault("personality_profile", {})
            interactions = profile.get("total_interactions", 0)
            if interactions > 10:
                recent_assistant = [
                    m for m in self.memory.get("messages", [])[-20:]
                    if m.get("role") == "assistant"
                ]
                if recent_assistant:
                    avg_len = sum(len(m.get("content", "")) for m in recent_assistant) / len(recent_assistant)
                    profile["prefers_short_answers"] = avg_len < 150
            if skill_used:
                code_skills = {"write_code", "run_code", "code_review", "fix_code", "project_scaffold"}
                if skill_used in code_skills:
                    profile["main_domain"] = "coding"
                elif skill_used in {"search", "weather", "youtube_search"}:
                    profile["main_domain"] = "information"
                elif skill_used in {"open_app", "web_open", "volume", "brightness", "lock_pc"}:
                    profile["main_domain"] = "pc_control"
            self._pending += 1
            await self._maybe_debounce_save_unlocked()
            return True

    # ── episodic ─────────────────────────────────────────────────────────

    async def store_episode(self, user_text: str, max_text: str, skill_used: str = ""):
        if not user_text:
            return
        ep = {
            "id": hashlib.sha1(
                f"{user_text[:50]}|{max_text[:50]}|{time.time()}".encode()
            ).hexdigest()[:16],
            "timestamp": datetime.now().isoformat(),
            "user": user_text[:200],
            "max": max_text[:200],
            "skill": skill_used or "",
            "pinned": False,
        }
        async with self._lock:
            self.episodes.append(ep)
            self.episodes = self._filter_expired(self.episodes)
            if len(self.episodes) > EPISODIC_MAX_ENTRIES:
                # Drop oldest unpinned first
                unpinned = [e for e in self.episodes if not e.get("pinned")]
                if len(unpinned) > EPISODIC_MAX_ENTRIES // 2:
                    keep = EPISODIC_MAX_ENTRIES // 2
                    self.episodes = [e for e in self.episodes if e.get("pinned")] + unpinned[-keep:]
            self._episodic.load(self.episodes)
            await self._episodic_store.save(self.episodes)

    def _episodic_dirty(self) -> bool:
        return False  # Episodes are saved immediately on add.

    # ── procedural ───────────────────────────────────────────────────────

    async def record_skill_success(self, skill: str, query: str):
        if not skill:
            return
        async with self._lock:
            successes = self.procedural.setdefault("skill_successes", {})
            entry = successes.setdefault(skill, {"count": 0, "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < 5:
                entry["examples"].append(query[:120])
            await self._procedural_store.save(self.procedural)

    def _procedural_dirty(self) -> bool:
        return False

    # ── semantic ─────────────────────────────────────────────────────────

    async def regenerate_semantic_profile(self):
        """Regenerate the long-term semantic profile from raw memory.

        Run periodically (e.g. once a day) or when total_interactions jumps.
        For now, derives simple traits from messages. Future: LLM-based
        consolidation.
        """
        async with self._lock:
            msgs = self.memory.get("messages", [])
            if len(msgs) < 20:
                return
            traits = []
            # Word frequency over recent messages (simple topic hint)
            from collections import Counter
            counter: Counter = Counter()
            for m in msgs[-200:]:
                if m.get("role") == "user":
                    for tok in re.findall(r"\b[a-zA-Z]{4,}\b", m.get("content", "").lower()):
                        counter[tok] += 1
            top = [w for w, _ in counter.most_common(8)]
            if top:
                traits.append("frequent_topics: " + ", ".join(top))
            profile = self.memory.get("personality_profile", {})
            if profile.get("main_domain"):
                traits.append(f"main_domain: {profile['main_domain']}")
            if profile.get("prefers_short_answers"):
                traits.append("prefers_short_answers: true")
            self.semantic = {"traits": traits, "updated_at": datetime.now().isoformat()}
            await self._semantic_store.save(self.semantic)

    def _semantic_dirty(self) -> bool:
        return False

    # ── context building (the public surface the LLM sees) ──────────────

    def get_context(self) -> str:
        """Legacy: build a single full context string. Prefer build_context()."""
        return self._build_context_sync(intent_type="legacy", query="")

    def get_context_for_query(self, query: str, intent_type: str = "COMMAND") -> str:
        return self._build_context_sync(intent_type=intent_type, query=query)

    def _build_context_sync(self, intent_type: str, query: str) -> str:
        """
        Assemble a token-budgeted context block for the LLM.

        Layout (in order, each section dropped if it would exceed the budget):
          RULES        - permanent_rules.json (always first if present)
          RECALL       - episodic hits (only if intent == MEMORY_RECALL or query signals recall)
          USER         - user_facts (compact)
          PROFILE      - personality_profile (compact)
          SEMANTIC     - semantic traits (one line)
          WORKING      - last N turns (within budget)
        """
        sections: List[str] = []
        used = 0
        cap = self.token_budget * CHARS_PER_TOKEN

        def add(label: str, body: str):
            nonlocal used
            if not body:
                return
            block = f"{label}:\n{body}".strip()
            cost = len(block) + 1
            if used + cost > cap:
                # Try a truncated version
                remaining = cap - used - len(label) - 8
                if remaining < 40:
                    return
                block = f"{label}:\n{body[:remaining]}"
                cost = len(block) + 1
            sections.append(block)
            used += cost

        # 1. Rules (always include)
        add("RULES", self._get_rules_text())

        # 2. Recall (episodic) - only on recall queries
        if self._is_recall_query(query) or intent_type in ("MEMORY_RECALL", "CONVERSATION"):
            rec = self._format_episodes_for_context(self._episodic.search(query or "", top_k=3))
            add("PAST", rec)

        # 3. Factual
        add("USER", self._get_factual_text())

        # 4. Personality
        add("PROFILE", self._get_personality_text())

        # 5. Semantic (one-liner)
        sem = self.semantic.get("traits", [])
        if sem:
            add("TRAITS", "; ".join(sem[:3]))

        # 6. Working memory (last N)
        add("RECENT", self._get_working_text(intent_type))

        return "\n\n".join(sections) if sections else ""

    # ── episodic recall helpers ──────────────────────────────────────────

    def _is_recall_query(self, query: str) -> bool:
        if not query:
            return False
        q = query.lower()
        signals = [
            "remember", "pehle", "yesterday", "earlier", "last time",
            "what did we", "kya baat ki", "yaad", "recall", "history",
            "discussed", "past conversation", "you know",
        ]
        return any(s in q for s in signals)

    def _format_episodes_for_context(self, episodes: List[Dict[str, Any]]) -> str:
        if not episodes:
            return ""
        lines = []
        for ep in episodes:
            ts = (ep.get("timestamp") or "")[:16]
            user = (ep.get("user") or "")[:80]
            mx = (ep.get("max") or "")[:80]
            sk = ep.get("skill") or ""
            tag = f" [{sk}]" if sk else ""
            lines.append(f"[{ts}] You: {user} -> Max: {mx}{tag}")
        return "\n".join(lines)

    # ── misc public helpers ──────────────────────────────────────────────

    async def clear_memory(self) -> bool:
        """Reset conversation history. Keep facts/profile/semantic."""
        async with self._lock:
            user_facts = self.memory.get("user_facts", {})
            profile = self.memory.get("personality_profile", {})
            self.memory = self._fresh_memory()
            self.memory["user_facts"] = user_facts
            self.memory["personality_profile"] = profile
            ok = self._store._save_unlocked(self.memory)
            self._pending = 0
            return ok

    def get_recent_history(self, limit: int = 20) -> str:
        msgs = self.memory.get("messages", [])
        if not msgs:
            return "No previous conversation history found."
        recent = msgs[-limit:]
        lines = [f"=== RECENT CONVERSATION HISTORY (Last {len(recent)} messages) ==="]
        for idx, m in enumerate(recent, 1):
            role = "Sanket" if m.get("role") == "user" else "MAX"
            ts = m.get("timestamp", "")
            time_str = f" [{ts[11:16]}]" if len(ts) >= 16 else ""
            lines.append(f"{idx}. {role}{time_str}: {m.get('content','')}")
        return "\n".join(lines)

    async def extract_and_store_facts(self, user_text: str) -> List[str]:
        """Pattern + (rate-limited) Gemini fact extraction."""
        import re
        facts_found: List[str] = []
        text_lower = user_text.lower()

        name_patterns = [
            r"mera naam (\w+) hai", r"my name is (\w+)",
            r"main (\w+) hoon", r"call me (\w+)",
        ]
        for p in name_patterns:
            m = re.search(p, text_lower)
            if m:
                name = m.group(1).title()
                await self.update_user_fact("name", name)
                facts_found.append(f"name={name}")
                break

        loc_patterns = [
            r"main (\w+) mein rehta hoon", r"main (\w+) mein rehti hoon",
            r"i live in (\w+)", r"i am from (\w+)",
        ]
        for p in loc_patterns:
            m = re.search(p, text_lower)
            if m:
                loc = m.group(1).title()
                await self.update_user_fact("location", loc)
                facts_found.append(f"location={loc}")
                break

        pref_patterns = [
            (r"mujhe (\w+) pasand hai", "likes"),
            (r"i love (\w+)", "likes"),
            (r"i hate (\w+)", "dislikes"),
            (r"mujhe (\w+) nahi pasand", "dislikes"),
        ]
        for p, cat in pref_patterns:
            m = re.search(p, text_lower)
            if m:
                item = m.group(1)
                prefs = self.memory.get("user_facts", {}).get("preferences", {})
                prefs.setdefault(cat, []).append(item)
                await self.update_user_fact("preferences", prefs)
                facts_found.append(f"{cat}={item}")

        # Rate-limited Gemini extraction
        if not facts_found and len(user_text.strip()) > 20:
            now = time.time()
            last_gemini = self.memory.get("_last_gemini_fact_ts", 0)
            if now - last_gemini > 60:  # at most once per minute
                try:
                    from modules.gemini_router import get_gemini_router
                    self.memory["_last_gemini_fact_ts"] = now
                    self._pending += 1
                    gemini_facts = await get_gemini_router().extract_facts(user_text)
                    for k, v in (gemini_facts or {}).items():
                        if k and v and isinstance(v, str):
                            await self.update_user_fact(k, v)
                            facts_found.append(f"{k}={v}")
                except Exception as e:
                    logger.debug(f"Gemini fact extraction skipped: {e}")

        return facts_found

    # ── internal: defaults + helpers ─────────────────────────────────────

    def _fresh_memory(self) -> Dict[str, Any]:
        return {
            "session_id": datetime.now().isoformat(),
            "messages": [],
            "summary": "",
            "user_facts": {
                "name": "the user",
                "location": "Maharashtra",
                "preferences": {},
            },
            "personality_profile": {
                "prefers_short_answers": False,
                "main_domain": "coding",
                "humor_level": "medium",
                "total_interactions": 0,
                "last_greeting": "",
            },
            "created_at": datetime.now().isoformat(),
        }

    def _fresh_semantic(self) -> Dict[str, Any]:
        return {"traits": [], "updated_at": ""}

    def _fresh_procedural(self) -> Dict[str, Any]:
        return {"skill_successes": {}}

    def _get_rules_text(self) -> str:
        rules_file = self.data_dir / "permanent_rules.json"
        if not rules_file.exists():
            return ""
        try:
            rules = json.loads(rules_file.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if not rules:
            return ""
        return "; ".join(r.get("rule", "")[:60] for r in rules[:5])

    def _get_factual_text(self) -> str:
        facts = self.memory.get("user_facts", {})
        if not facts:
            return ""
        parts = []
        for k, v in facts.items():
            if k == "preferences":
                continue
            if v:
                parts.append(f"{k}: {v}")
        if not parts:
            return ""
        return ", ".join(parts)

    def _get_personality_text(self) -> str:
        p = self.memory.get("personality_profile", {})
        if not p:
            return ""
        parts = []
        if p.get("prefers_short_answers"):
            parts.append("prefers short answers")
        if p.get("main_domain"):
            parts.append(f"domain: {p['main_domain']}")
        return ", ".join(parts)

    def _get_working_text(self, intent_type: str) -> str:
        msgs = self.memory.get("messages", [])
        if not msgs:
            return ""
        # Commands need only enough for pronoun resolution; chat needs more continuity.
        if intent_type in ("COMMAND", "INFORMATION_QUESTION", "NEGATIVE_COMMAND"):
            n = 3
        elif intent_type == "CONVERSATION":
            n = 5
        else:
            n = self.max_messages
        recent = msgs[-n:]
        lines = []
        for m in recent:
            role = "You" if m.get("role") == "user" else "Max"
            content = (m.get("content") or "")[:160]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _filter_expired(self, episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not episodes:
            return episodes
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=EPISODIC_TTL_DAYS)
        out = []
        for ep in episodes:
            if ep.get("pinned"):
                out.append(ep)
                continue
            ts = ep.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                out.append(ep)
                continue
            if dt >= cutoff:
                out.append(ep)
        return out

    def _similar(self, a: str, b: str) -> float:
        """Quick Jaccard similarity over word tokens."""
        ta = set(re.findall(r"\b\w+\b", a.lower()))
        tb = set(re.findall(r"\b\w+\b", b.lower()))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    async def _maybe_debounce_save_unlocked(self):
        """Coalesce frequent saves."""
        now = time.time()
        if self._pending >= DEBOUNCE_PENDING_MAX or (now - self._last_save) >= DEBOUNCE_SECONDS:
            await self._flush_unlocked()
            return
        # Schedule a trailing save if not already scheduled
        if self._save_task is None or self._save_task.done():
            async def _delayed():
                await asyncio.sleep(DEBOUNCE_SECONDS)
                async with self._lock:
                    if self._pending > 0:
                        await self._flush_unlocked()
            self._save_task = asyncio.create_task(_delayed())


# Module-level singleton (legacy entry point).
_memory_instance: Optional[MemoryManager] = None


def get_memory_manager(config) -> MemoryManager:
    """Return the singleton MemoryManager. Initializes on first call."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = MemoryManager(
            memory_file=config.MEMORY_FILE,
            max_messages=getattr(config, "MEMORY_MAX_MESSAGES", 6),
            summarize_threshold=getattr(config, "MEMORY_SUMMARIZE_THRESHOLD", 50),
            token_budget=getattr(config, "MEMORY_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET),
        )
        # Best-effort eager init; if no running loop, will lazy-init on first use.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_memory_instance.initialize())
            else:
                loop.run_until_complete(_memory_instance.initialize())
        except Exception as e:
            logger.debug(f"get_memory_manager: eager init skipped: {e}")
    return _memory_instance
