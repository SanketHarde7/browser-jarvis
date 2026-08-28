"""Core execution logic for the chat agent.

Uses the unified MAX system prompt (modules.prompts) so the chat agent's
persona stays in lockstep with the legacy engine. Persona never drifts.
"""
import os
import logging
from typing import Dict, Any, List

from groq import AsyncGroq

from ..base_agent import BaseSubAgent
from .prompts import CHAT_ROLE_OVERLAY
from .errors import ChatGenerationError, ChatContextFormattingError
from modules.prompts import build_system_prompt

from api_utils import key_pool

logger = logging.getLogger("MAX.ChatAgent")


class ChatAgent(BaseSubAgent):
    """
    Subagent for general conversation. Has no skills; the orchestrator
    decides whether a follow-up action is needed. Uses the unified MAX
    persona so it never drifts from the legacy engine.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.model = "llama-3.3-70b-versatile"

    def _format_capabilities(self, skill_rag=None) -> str:
        """Return a short, human-readable list of what MAX can do right now."""
        try:
            from core.module_registry import registry
            skills = registry.list_skills() if hasattr(registry, "list_skills") else []
            if skills:
                return "Available skills: " + ", ".join(skills[:25])
        except Exception:
            pass
        return (
            "Available skills: coding, web search, opening apps and websites, "
            "system control (volume, brightness, lock, shutdown), timers and "
            "reminders, weather, notes, file search, vision (screen/image), "
            "email, calendar, browser automation, smart-home control."
        )

    def _format_context(self, context: List[Dict[str, str]]) -> str:
        """Format the last N messages into a compact string for the prompt."""
        try:
            if not context:
                return "No previous context."
            lines = []
            for msg in context[-5:]:
                role = msg.get("role", "user").capitalize()
                content = (msg.get("content", "") or "")[:160]
                lines.append(f"{role}: {content}")
            return "\n".join(lines)
        except Exception as e:
            raise ChatContextFormattingError(f"Failed to format context: {str(e)}")

    async def process(self, query: str, context: List[Dict[str, str]]) -> Dict[str, Any]:
        """Generate a chat reply using the unified MAX persona."""
        self._log_info(f"Processing chat query: '{query[:50]}...'")

        try:
            formatted_context = self._format_context(context)
            capabilities = self._format_capabilities()

            # Unified system prompt - same persona as the legacy engine.
            system_prompt = build_system_prompt(
                role="chat",
                capabilities_block=capabilities,
                context_block=f"Recent turns:\n{formatted_context}\n\n{CHAT_ROLE_OVERLAY}",
            )

            api_key = await key_pool.lease_key()
            if not api_key:
                raise ChatGenerationError("No API key available for ChatAgent.")

            client = AsyncGroq(api_key=api_key)
            main_model = getattr(
                self.config, "MAIN_LLM_MODEL",
                os.getenv("MAIN_LLM_MODEL", "openai/gpt-oss-120b"),
            )

            response = await client.chat.completions.create(
                model=main_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                temperature=0.7,
                max_tokens=256,
            )
            reply = (response.choices[0].message.content or "").strip()
            self._log_info("Successfully generated chat response.")

            return {
                "response": reply,
                "intent": "general_chat",
                "status": "success",
            }

        except ChatContextFormattingError as ce:
            self._log_error("Context formatting failed", ce)
            raise
        except Exception as e:
            self._log_error("LLM generation failed", e)
            raise ChatGenerationError(f"Failed to generate response: {str(e)}")
