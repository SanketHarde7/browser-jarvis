# Path: backend/modules/agents/ai_delegate_agent.py
# Use: Opens a temporary browser tab on another AI platform (ChatGPT, Perplexity, Claude)
#      to ask a question, harvests the response, closes the tab, and returns the answer.
#      Used by the Master Orchestrator when it issues ACTION: ASK_AI.

import logging
import uuid
from typing import Dict, Any

from modules.agents.browser_manager import browser_manager
from modules.ai_orchestrator.platform_config import PLATFORMS

logger = logging.getLogger("MAX.AI_DELEGATE")


async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Open a temporary tab on a specified AI platform, ask a question,
    harvest the response, close the tab, return the answer.

    input_data keys:
      - platform (str): "perplexity", "chatgpt", "claude"
      - prompt (str): The question to ask
    """
    platform_key = (input_data.get("platform") or "perplexity").lower().strip()
    prompt = input_data.get("prompt") or ""

    if not prompt:
        return {"success": False, "answer": "", "error": "No prompt provided to AI delegate."}

    platform_info = PLATFORMS.get(platform_key)
    if not platform_info:
        # Fallback to perplexity if unknown platform
        logger.warning(f"Unknown platform '{platform_key}'. Falling back to perplexity.")
        platform_key = "perplexity"
        platform_info = PLATFORMS["perplexity"]

    # Unique tab ID so it doesn't conflict with other tabs
    tab_id = f"delegate_{platform_key}_{uuid.uuid4().hex[:8]}"

    try:
        logger.info(f"🤖 AI Delegate: Opening {platform_info.name} tab to ask: {prompt[:100]}...")

        # Open a new tab on the target platform
        await browser_manager.get_tab(tab_id, url=platform_info.url_new_chat)

        # Send the prompt and wait for the response
        pre_count = await browser_manager.inject_and_submit(tab_id, prompt, platform_info)
        answer = await browser_manager.smart_harvester(
            tab_id, platform_info, pre_submit_count=pre_count, timeout=120
        )

        logger.info(f"🤖 AI Delegate: Got {len(answer)} chars from {platform_info.name}")

        # Close the temporary tab — we're done with it
        await browser_manager.close_tab(tab_id)

        if answer and len(answer.strip()) > 10:
            return {
                "success": True,
                "answer": answer,
                "platform": platform_key,
                "error": None,
            }
        else:
            return {
                "success": False,
                "answer": "",
                "platform": platform_key,
                "error": f"{platform_info.name} returned an empty or too-short response.",
            }

    except Exception as e:
        logger.error(f"🔴 AI Delegate failed on {platform_key}: {e}", exc_info=True)

        # Try to close the tab even if something went wrong
        try:
            await browser_manager.close_tab(tab_id)
        except Exception:
            pass

        return {
            "success": False,
            "answer": "",
            "platform": platform_key,
            "error": str(e),
        }
