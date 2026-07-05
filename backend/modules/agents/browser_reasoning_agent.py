# Path: backend/modules/agents/browser_reasoning_agent.py
# Use: Real reasoning agent that uses a persistent browser tab to interact with
#      an AI platform (Gemini/ChatGPT/Claude) for coverage assessment and gap analysis.

import logging
from typing import Dict, Any, List

from modules.agents.browser_manager import browser_manager
from modules.ai_orchestrator.platform_config import PLATFORMS

logger = logging.getLogger("MAX.BROWSER_REASONING_AGENT")

# Default platform — can be overridden via input_data["platform"]
DEFAULT_PLATFORM = "gemini"

SYSTEM_PERSONA = (
    "You are MAX's Deep Research Reasoning Engine. Your job:\n"
    "1. Analyze gathered evidence for a research question.\n"
    "2. Identify coverage gaps — what important angles are still missing?\n"
    "3. Flag single-source claims that need corroboration.\n"
    "4. Decide: is there ENOUGH high-quality evidence to write a comprehensive report, "
    "   or do we need more searching?\n\n"
    "RESPOND IN THIS EXACT FORMAT (no markdown fences, just plain text):\n"
    "SUFFICIENT: YES or NO\n"
    "COVERAGE_ASSESSMENT: <1-2 sentence summary of current evidence quality>\n"
    "GAPS: <comma-separated list of missing topics, or 'none'>\n"
    "NEXT_QUERIES: <comma-separated search queries to fill gaps, or 'none'>\n"
)


async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Called by orchestrator each iteration.
    - iteration 1: Opens a new tab, sends persona + all facts
    - iteration 2+: Sends only new facts to the existing tab (same conversation)
    """
    facts: List[str] = input_data.get("facts") or []
    iteration = int(input_data.get("iteration", 1))
    goal = input_data.get("goal") or (context or {}).get("goal", "")
    task_id = (context or {}).get("task_id", "default")
    platform_key = input_data.get("platform", DEFAULT_PLATFORM)

    platform_info = PLATFORMS.get(platform_key)
    if not platform_info:
        logger.error(f"Unknown platform: {platform_key}. Falling back to {DEFAULT_PLATFORM}.")
        platform_info = PLATFORMS[DEFAULT_PLATFORM]

    tab_id = f"reasoning_{task_id}"
    # Accept facts with at least 20 chars (lowered from 40 to not drop short but useful facts)
    useful_facts = [f for f in facts if f and len(f.strip()) > 20]

    try:
        if iteration == 1:
            # First round: open a new tab and set up the conversation
            logger.info(f"🧠 Reasoning Agent: Opening tab '{tab_id}' on {platform_info.name}")
            await browser_manager.get_tab(tab_id, url=platform_info.url_new_chat)

            facts_block = "\n---\n".join(useful_facts[:15]) if useful_facts else "No evidence gathered yet."
            prompt = (
                f"{SYSTEM_PERSONA}\n\n"
                f"RESEARCH QUESTION: {goal}\n\n"
                f"EVIDENCE GATHERED SO FAR ({len(useful_facts)} sources):\n"
                f"{facts_block}\n\n"
                f"Analyze the above and respond in the required format."
            )
        else:
            # Subsequent rounds: reuse the existing tab, send only new facts
            logger.info(f"🧠 Reasoning Agent: Round {iteration} on tab '{tab_id}'")
            await browser_manager.get_tab(tab_id)  # Just switch to existing tab

            facts_block = "\n---\n".join(useful_facts[:10]) if useful_facts else "No new evidence this round."
            prompt = (
                f"ROUND {iteration} UPDATE — NEW EVIDENCE:\n"
                f"{facts_block}\n\n"
                f"Re-assess coverage. Are we now sufficient to write a comprehensive report? "
                f"Respond in the same format as before (SUFFICIENT / COVERAGE_ASSESSMENT / GAPS / NEXT_QUERIES)."
            )

        # inject_and_submit now returns the pre-submit response count
        pre_submit_count = await browser_manager.inject_and_submit(tab_id, prompt, platform_info)

        # smart_harvester uses the count to wait for the NEW response only
        raw_response = await browser_manager.smart_harvester(
            tab_id, platform_info, pre_submit_count=pre_submit_count, timeout=90
        )

        logger.info(f"🧠 Reasoning response (round {iteration}): {raw_response[:200]}...")
        return _parse_response(raw_response, iteration, len(useful_facts), goal)

    except Exception as e:
        logger.error(f"🔴 Reasoning Agent failed (round {iteration}): {e}", exc_info=True)

        # Graceful fallback so pipeline continues
        return {
            "coverage_assessment": f"Reasoning agent error: {e}",
            "next_search_queries": [f"{goal} detailed analysis", f"{goal} expert review"] if iteration < 3 else [],
            "sufficient": iteration >= 3,
        }


def _parse_response(raw: str, iteration: int, fact_count: int, goal: str) -> Dict[str, Any]:
    """Parse the structured response from the AI platform."""
    lines = raw.strip().split("\n")
    result = {
        "sufficient": False,
        "coverage_assessment": "",
        "next_search_queries": [],
        "raw_reasoning": raw,
    }

    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("SUFFICIENT:"):
            val = line.split(":", 1)[1].strip().upper()
            result["sufficient"] = val.startswith("YES")
        elif upper.startswith("COVERAGE_ASSESSMENT:"):
            result["coverage_assessment"] = line.split(":", 1)[1].strip()
        elif upper.startswith("GAPS:"):
            gaps = line.split(":", 1)[1].strip()
            if gaps.lower() != "none":
                result["gaps"] = gaps
        elif upper.startswith("NEXT_QUERIES:"):
            queries_raw = line.split(":", 1)[1].strip()
            if queries_raw.lower() != "none":
                result["next_search_queries"] = [q.strip() for q in queries_raw.split(",") if q.strip()]

    # Safety fallback: if parsing failed entirely, use heuristics
    if not result["coverage_assessment"]:
        result["coverage_assessment"] = f"Round {iteration}: {fact_count} useful facts analyzed."
        # If we have enough facts and enough rounds, declare sufficient
        if fact_count >= 5 and iteration >= 2:
            result["sufficient"] = True
        elif not result["next_search_queries"]:
            result["next_search_queries"] = [
                f"{goal} latest research",
                f"{goal} criticism limitations",
            ]

    return result
