# Path: backend/modules/agents/master_orchestrator_agent.py
# Use: The "Master Brain" that runs in a persistent browser tab (Gemini/Claude).
#      It dynamically decides every move: search, read, ask another AI, or finish.
#      The orchestrator loop simply executes whatever ACTION this agent commands.

import logging
import re
from typing import Dict, Any, Optional

from modules.agents.browser_manager import browser_manager
from modules.ai_orchestrator.platform_config import PLATFORMS

logger = logging.getLogger("MAX.MASTER_ORCHESTRATOR")

DEFAULT_PLATFORM = "gemini"

MASTER_PERSONA = (
    "You are MAX's Master Research Orchestrator — a fully autonomous research coordinator.\n"
    "Your job is to deeply research the given topic by issuing ONE action per turn.\n\n"

    "═══ AVAILABLE ACTIONS ═══\n\n"

    "ACTION: SEARCH | <search query>\n"
    "  → I will search the web and return top URLs found.\n"
    "  → Use specific, targeted queries for best results.\n\n"

    "ACTION: READ_URL | <full url>\n"
    "  → I will fetch the text content of that page and send it to you.\n"
    "  → Use this to read promising URLs from search results.\n\n"

    "ACTION: ASK_AI | <platform> | <question or prompt>\n"
    "  → I will open a separate AI tab and ask your question. Platforms: perplexity, chatgpt, claude\n"
    "  → Use this for expert analysis, when web search fails, or to get another AI's perspective.\n"
    "  → Perplexity is best for web-grounded research with citations.\n\n"

    "ACTION: FINISH\n"
    "  → You have gathered enough evidence. I will compile your final research report.\n"
    "  → Only call this when you have high-quality evidence from 3+ diverse sources.\n\n"

    "═══ RULES ═══\n"
    "1. Respond with EXACTLY this format every turn — nothing else:\n"
    "   THINKING: <your 1-line reasoning about what to do next>\n"
    "   ACTION: <TYPE> | <args>\n"
    "2. Start by issuing 2-3 SEARCH actions (one per turn) to find sources.\n"
    "3. Then READ_URL the most promising pages.\n"
    "4. If a search fails or returns bad results, adapt: try different queries or ASK_AI.\n"
    "5. If READ_URL fails (blocked site, etc.), skip it and try another URL.\n"
    "6. Gather evidence from at least 3-5 diverse, high-quality sources before FINISH.\n"
    "7. Do NOT hallucinate URLs — only READ_URL pages that came from SEARCH results or that you know exist.\n"
    "8. After each action, I will send you the result. Then decide your next move.\n"
)


async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Called by the orchestrator each iteration.

    input_data keys:
      - goal (str): The research question (always present)
      - iteration (int): Current turn number
      - action_result (str, optional): Result from the previous action (present from iteration 2+)
      - action_error (str, optional): Error from the previous action if it failed
      - action_type (str, optional): What action was executed ("SEARCH", "READ_URL", "ASK_AI")

    Returns:
      - action (str): "SEARCH", "READ_URL", "ASK_AI", or "FINISH"
      - query (str, optional): For SEARCH
      - url (str, optional): For READ_URL
      - platform (str, optional): For ASK_AI
      - prompt (str, optional): For ASK_AI
      - thinking (str): The AI's reasoning
      - raw_response (str): Full raw AI response
    """
    goal = input_data.get("goal") or (context or {}).get("goal", "")
    iteration = int(input_data.get("iteration", 1))
    task_id = (context or {}).get("task_id", "default")
    platform_key = input_data.get("master_platform", DEFAULT_PLATFORM)

    platform_info = PLATFORMS.get(platform_key)
    if not platform_info:
        logger.error(f"Unknown platform: {platform_key}. Falling back to {DEFAULT_PLATFORM}.")
        platform_info = PLATFORMS[DEFAULT_PLATFORM]

    tab_id = f"master_{task_id}"

    try:
        if iteration == 1:
            # ── First turn: Open tab, send persona + goal ──
            logger.info(f"🧠 Master Agent: Opening tab '{tab_id}' on {platform_info.name}")
            await browser_manager.get_tab(tab_id, url=platform_info.url_new_chat)

            prompt = (
                f"{MASTER_PERSONA}\n\n"
                f"═══ RESEARCH GOAL ═══\n"
                f"{goal}\n\n"
                f"Begin your research. Issue your first ACTION."
            )
        else:
            # ── Subsequent turns: Send action result, get next action ──
            logger.info(f"🧠 Master Agent: Turn {iteration}")
            await browser_manager.get_tab(tab_id)  # Switch to existing tab

            action_type = input_data.get("action_type", "UNKNOWN")
            action_error = input_data.get("action_error")
            action_result = input_data.get("action_result", "")

            if action_error:
                prompt = (
                    f"═══ TURN {iteration} — ACTION RESULT ═══\n"
                    f"Your previous action ({action_type}) FAILED.\n"
                    f"Error: {action_error}\n\n"
                    f"Adapt your strategy. Issue your next ACTION."
                )
            else:
                # Truncate long results to avoid overflowing the AI's context
                if len(action_result) > 15000:
                    action_result = action_result[:15000] + "\n\n[... truncated for length ...]"

                prompt = (
                    f"═══ TURN {iteration} — ACTION RESULT ═══\n"
                    f"Your previous action ({action_type}) succeeded.\n"
                    f"Result:\n{action_result}\n\n"
                    f"Analyze this and issue your next ACTION."
                )

        # Send to browser and harvest response
        pre_count = await browser_manager.inject_and_submit(tab_id, prompt, platform_info)
        raw_response = await browser_manager.smart_harvester(
            tab_id, platform_info, pre_submit_count=pre_count, timeout=90
        )

        logger.info(f"🧠 Master response (turn {iteration}): {raw_response[:300]}...")
        return _parse_action(raw_response, iteration, goal)

    except Exception as e:
        logger.error(f"🔴 Master Agent failed (turn {iteration}): {e}", exc_info=True)

        # Fallback: if the Master AI itself crashes, use a sensible default
        if iteration <= 2:
            return {
                "action": "SEARCH",
                "query": goal,
                "thinking": f"Master agent error ({e}). Falling back to direct search.",
                "raw_response": str(e),
            }
        else:
            return {
                "action": "FINISH",
                "thinking": f"Master agent error ({e}). Finishing with whatever evidence we have.",
                "raw_response": str(e),
            }


def _parse_action(raw: str, iteration: int, goal: str) -> Dict[str, Any]:
    """
    Parse the AI's response to extract THINKING and ACTION.

    Expected format:
      THINKING: <reasoning>
      ACTION: SEARCH | <query>
      ACTION: READ_URL | <url>
      ACTION: ASK_AI | <platform> | <prompt>
      ACTION: FINISH
    """
    result = {
        "action": None,
        "thinking": "",
        "raw_response": raw,
    }

    # Extract THINKING line
    thinking_match = re.search(r"THINKING:\s*(.+)", raw, re.IGNORECASE)
    if thinking_match:
        result["thinking"] = thinking_match.group(1).strip()

    # Extract ACTION line
    action_match = re.search(r"ACTION:\s*(.+)", raw, re.IGNORECASE)
    if not action_match:
        # No action found — try to infer from content
        logger.warning(f"No ACTION found in response. Inferring from content...")
        if iteration <= 2:
            result["action"] = "SEARCH"
            result["query"] = goal
            result["thinking"] = "No action parsed. Defaulting to search."
        else:
            result["action"] = "FINISH"
            result["thinking"] = "No action parsed. Defaulting to finish."
        return result

    action_line = action_match.group(1).strip()
    parts = [p.strip() for p in action_line.split("|")]
    action_type = parts[0].upper()

    if action_type == "SEARCH" and len(parts) >= 2:
        result["action"] = "SEARCH"
        result["query"] = parts[1]

    elif action_type == "READ_URL" and len(parts) >= 2:
        result["action"] = "READ_URL"
        result["url"] = parts[1]

    elif action_type == "ASK_AI" and len(parts) >= 3:
        result["action"] = "ASK_AI"
        result["platform"] = parts[1].lower().strip()
        result["prompt"] = parts[2]

    elif action_type == "ASK_AI" and len(parts) >= 2:
        # Platform not specified — default to perplexity (best for research)
        result["action"] = "ASK_AI"
        result["platform"] = "perplexity"
        result["prompt"] = parts[1]

    elif action_type == "FINISH":
        result["action"] = "FINISH"

    else:
        logger.warning(f"Could not parse action: '{action_line}'. Falling back.")
        if iteration <= 3:
            result["action"] = "SEARCH"
            result["query"] = goal + " research"
        else:
            result["action"] = "FINISH"

    logger.info(f"📋 Parsed action: {result['action']} | Thinking: {result.get('thinking', 'N/A')}")
    return result
