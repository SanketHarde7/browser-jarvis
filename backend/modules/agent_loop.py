# Path: backend/modules/agent_loop.py
# Use: Autonomous Plan → Act → Observe → Reflect loop for multi-step goals.
"""
agent_loop.py — MAX v6.0 (Agentic Core)

Turns MAX from a one-shot command runner into a true agent:

  1. PLAN    — one LLM call breaks a complex goal into ordered steps.
  2. ACT     — each step executes ONE skill via the existing SkillsEngine.
  3. OBSERVE — the output of every step is inspected for failure signals.
  4. REFLECT — on failure, one focused LLM call revises the step (retry/skip),
               then execution continues.

Design notes:
- MAX is a personal LIFE assistant (apps, media, web, research, reminders,
  messages, smart home, files) — NOT a coding tool. The planner prompt is
  tuned for everyday multi-step tasks.
- Rate-limit friendly: exactly ONE planning call per goal, reflection calls
  only happen when a step fails, and the final summary is a single call.
- Steps can reference earlier results with {step_N} placeholders.
- Progress is streamed to the client as additive `plan_update` WebSocket
  events — old frontends simply ignore them.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from config import config
from api_utils import execute_with_retry

logger = logging.getLogger("MAX.AGENT_LOOP")


# ═════════════════════════════════════════════════
# COMPLEX-GOAL DETECTION (0 API calls)
# Conservative on purpose: simple commands keep the fast single-shot path.
# ═════════════════════════════════════════════════

_SEQUENCE_RE = re.compile(
    r"\b(and then|after that|after this|uske baad|us ke baad|phir|fir se nahi|"
    r"baad me|baad mein|ke baad|followed by|once (it'?s|that'?s| ?)done|"
    r"step by step|ek ek karke|sabse pehle|first\b.*\bthen)\b",
    re.IGNORECASE,
)

_ACTION_VERB_RE = re.compile(
    r"\b(research|search|find|check|open|play|send|set|create|save|read|"
    r"summarize|summarise|email|message|whatsapp|schedule|remind|note|"
    r"download|scrape|compare|book|order|likho|bhejo|dhundo|khojo|banao|"
    r"laga(?:o|do)|sunao|dikhao|pata karo)\b",
    re.IGNORECASE,
)


def is_complex_goal(text: str) -> bool:
    """Heuristic gate — decides if a goal needs the full agent loop."""
    t = (text or "").strip()
    if len(t.split()) < 5:
        return False
    if _SEQUENCE_RE.search(t):
        return True
    distinct_verbs = {m.lower() for m in _ACTION_VERB_RE.findall(t)}
    return len(distinct_verbs) >= 3


# ═════════════════════════════════════════════════
# PROMPTS
# ═════════════════════════════════════════════════

_PLANNER_PROMPT = """You are the planning brain of MAX — a personal voice assistant that manages the user's daily digital life: apps, media, web browsing, research, reminders, notes, messages, email, calendar, smart home and files. MAX is NOT a coding tool.

Break the user's goal into a SHORT ordered plan. Use as FEW steps as possible (max {max_steps}).

AVAILABLE SKILLS (use EXACT names):
{skills}

SKILL TAG FORMAT: [SKILL:skill_name:param1:param2]
- For counting: [SKILL:count:<start>:<end>:<reverse_true_or_false>] (e.g. [SKILL:count:1:5:false])

RULES:
- Each step performs exactly ONE action with ONE skill tag, OR is a pure reasoning/summarizing step with skill set to null.
- Later steps may use the result of an earlier step by writing {step_N} inside a parameter. Example: [SKILL:note:{step_1}]
- Order steps by real dependency — a step that needs data must come AFTER the step that fetches it.
- Do NOT invent extra steps, confirmations, or actions the user never asked for.
- Everyday practical actions only.

USER GOAL: "{goal}"

Respond ONLY with valid JSON, no other text:
{"steps": [{"id": 1, "description": "short human description", "skill": "[SKILL:...]" }]}
Use null for the skill of pure reasoning steps."""


_REFLECT_PROMPT = """You are the self-correction brain of MAX, a personal voice assistant.

A plan step just FAILED.
USER GOAL: "{goal}"
FAILED STEP: "{description}"
SKILL TRIED: {skill}
OUTPUT/ERROR: {error}

AVAILABLE SKILLS (exact names): {skills}

Decide ONE of:
- "retry" with a corrected skill tag (fix the skill name, parameters, or pick a better skill)
- "skip" if the step is impossible or not essential to the goal

Respond ONLY with valid JSON:
{"action": "retry" | "skip", "skill": "[SKILL:...]" | null, "reason": "one line"}"""


_SUMMARY_PROMPT = """You are MAX, a personal voice assistant. The user asked: "{goal}"

You just executed this plan:
{step_lines}

Reply with a short spoken summary (1-3 sentences, plain speech, no markdown, no lists).
Lead with the most useful result/information. Mention briefly if something failed.
Don't start with "I". Speak naturally, like a friend reporting back."""


_FAIL_INDICATORS = (
    "could not find", "failed", "error", "not found", "not installed",
    "needed:", "missing", "unable to", "cannot", "does not exist", "no such",
)

_STEP_REF_RE = re.compile(r"\{step_(\d+)\}")


@dataclass
class PlanStep:
    id: int
    description: str
    skill: Optional[str]  # full [SKILL:...] tag, or None for reasoning-only
    status: str = "pending"  # pending | running | done | failed | skipped
    result: str = ""
    attempts: int = 0

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
        }


class AgentLoop:
    """Plan → Act → Observe → Reflect executor on top of SkillsEngine."""

    def __init__(self, cfg, skills_engine):
        self.config = cfg
        self.skills = skills_engine
        self.max_steps = min(int(getattr(cfg, "AGENT_MAX_STEPS", 10)), 6)

    # ── LLM helper ──────────────────────────────────────

    async def _call_llm(self, prompt: str, max_tokens: int = 600,
                        temperature: float = 0.2) -> str:
        from modules.llm import get_client

        async def call():
            client = await get_client()
            return await client.chat.completions.create(
                model=self.config.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )

        resp = await asyncio.wait_for(execute_with_retry(call), timeout=30.0)
        return resp.choices[0].message.content.strip()

    @staticmethod
    def _extract_json(raw: str) -> Optional[dict]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    def _skill_names(self) -> str:
        try:
            names = sorted(getattr(self.skills, "skills_registry", {}).keys())
            if names:
                return ", ".join(names)
        except Exception:
            pass
        return "search, weather, youtube_play, web_open, open_app, timer, note, reminder_set, media, email_send, whatsapp_message, calendar_add, research"

    # ── 1. PLAN ───────────────────────────────────────────

    async def _plan(self, goal: str) -> List[PlanStep]:
        prompt = (
            _PLANNER_PROMPT
            .replace("{max_steps}", str(self.max_steps))
            .replace("{skills}", self._skill_names())
            .replace("{goal}", goal.strip()[:1000])
        )
        raw = await self._call_llm(prompt, max_tokens=700, temperature=0.1)
        data = self._extract_json(raw)
        if not data or not isinstance(data.get("steps"), list) or not data["steps"]:
            raise ValueError(f"Planner returned no usable plan: {raw[:200]}")

        steps: List[PlanStep] = []
        for i, s in enumerate(data["steps"][: self.max_steps], start=1):
            skill = s.get("skill")
            if isinstance(skill, str) and not skill.strip().startswith("[SKILL:"):
                skill = None  # malformed tag → treat as reasoning step
            steps.append(
                PlanStep(
                    id=i,
                    description=str(s.get("description", f"Step {i}"))[:200],
                    skill=skill.strip() if isinstance(skill, str) else None,
                )
            )
        return steps

    # ── 2-3. ACT + OBSERVE ─────────────────────────────────

    @staticmethod
    def _looks_failed(output: str) -> bool:
        low = (output or "").lower()
        return any(ind in low for ind in _FAIL_INDICATORS)

    def _resolve_refs(self, skill_tag: str, steps: List[PlanStep]) -> str:
        """Replace {step_N} placeholders with earlier step results."""
        def sub(m: re.Match) -> str:
            idx = int(m.group(1))
            for st in steps:
                if st.id == idx:
                    one_line = " ".join((st.result or "").split())
                    return one_line[:300]
            return ""
        return _STEP_REF_RE.sub(sub, skill_tag)

    async def _act(self, step: PlanStep, steps: List[PlanStep],
                   context: str, goal: str) -> None:
        step.status = "running"
        step.attempts += 1
        if not step.skill:
            step.status = "done"
            step.result = "(reasoning step — folded into final summary)"
            return
        try:
            tag = self._resolve_refs(step.skill, steps)
            result = await self.skills.parse_and_execute(tag, context, goal)
            output = (result.get("result") or "").strip()
            if result.get("executed") and not self._looks_failed(output):
                step.status = "done"
                step.result = output or "Done."
            else:
                step.status = "failed"
                step.result = output or str(result.get("error", "Skill failed"))
        except Exception as e:  # noqa: BLE001
            step.status = "failed"
            step.result = f"Execution error: {e}"

    # ── 4. REFLECT ─────────────────────────────────────────

    async def _reflect(self, step: PlanStep, goal: str) -> bool:
        """One focused LLM call to fix a failed step. Returns True to retry."""
        prompt = (
            _REFLECT_PROMPT
            .replace("{goal}", goal[:500])
            .replace("{description}", step.description)
            .replace("{skill}", step.skill or "null")
            .replace("{error}", (step.result or "")[:400])
            .replace("{skills}", self._skill_names())
        )
        try:
            raw = await self._call_llm(prompt, max_tokens=200, temperature=0.0)
            data = self._extract_json(raw) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Reflection call failed: {e}")
            return False
        if data.get("action") == "retry":
            new_skill = data.get("skill")
            if isinstance(new_skill, str) and new_skill.strip().startswith("[SKILL:"):
                logger.info(
                    f"🧠 Reflect: retrying step {step.id} with {new_skill} "
                    f"({data.get('reason', '')})"
                )
                step.skill = new_skill.strip()
                return True
        step.status = "skipped"
        logger.info(f"🧠 Reflect: skipping step {step.id} ({data.get('reason', '')})")
        return False

    # ── FINAL SUMMARY ───────────────────────────────────────

    async def _summarize(self, goal: str, steps: List[PlanStep]) -> str:
        step_lines = "\n".join(
            f"{s.id}. [{s.status.upper()}] {s.description} → "
            f"{' '.join((s.result or '').split())[:250]}"
            for s in steps
        )
        prompt = (
            _SUMMARY_PROMPT
            .replace("{goal}", goal[:500])
            .replace("{step_lines}", step_lines)
        )
        try:
            return await self._call_llm(prompt, max_tokens=250, temperature=0.6)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Summary call failed: {e}")
            done = [s for s in steps if s.status == "done"]
            failed = [s for s in steps if s.status in ("failed", "skipped")]
            parts = []
            if done:
                parts.append(f"Finished {len(done)} of {len(steps)} steps.")
            if failed:
                parts.append(f"Couldn't complete: {failed[0].description}.")
            return " ".join(parts) or "Task finished."

    # ── PUBLIC ENTRY ────────────────────────────────────────

    async def run(
        self,
        goal: str,
        context: str,
        send_event: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Execute a multi-step goal autonomously. Never raises."""

        async def emit(phase: str, steps: List[PlanStep]) -> None:
            if send_event is None:
                return
            try:
                await send_event({
                    "event": "plan_update",
                    "phase": phase,
                    "goal": goal[:200],
                    "steps": [s.public() for s in steps],
                })
            except Exception:  # noqa: BLE001
                pass

        # 1. PLAN
        steps = await self._plan(goal)
        logger.info(f"🗺️ Plan ({len(steps)} steps): " +
                    " | ".join(s.description for s in steps))
        await emit("planned", steps)

        # 2-4. ACT → OBSERVE → REFLECT per step
        for step in steps:
            await self._act(step, steps, context, goal)
            if step.status == "failed" and step.attempts == 1:
                if await self._reflect(step, goal):
                    await self._act(step, steps, context, goal)
            logger.info(f"▸ Step {step.id} [{step.status}] {step.description}")
            await emit("progress", steps)

        # 5. SUMMARIZE
        response = await self._summarize(goal, steps)
        await emit("finished", steps)

        skills_used = " ".join(s.skill for s in steps if s.skill) or None
        return {
            "response": response,
            "skills_used": skills_used,
            "steps": [
                {**s.public(), "result": s.result[:500]} for s in steps
            ],
        }


# ── Singleton ──
_agent_loop: Optional[AgentLoop] = None


def get_agent_loop(cfg, skills_engine) -> AgentLoop:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = AgentLoop(cfg, skills_engine)
        logger.info("AgentLoop initialized (Plan→Act→Observe→Reflect)")
    return _agent_loop
