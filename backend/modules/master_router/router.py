import os
import json
import logging
from typing import List, Dict, Any
from groq import AsyncGroq
from api_utils import key_pool

from .prompts import ROUTER_SYSTEM_PROMPT
from .errors import RouterExecutionError

logger = logging.getLogger("MAX.MasterRouter")

class MasterRouter:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
    def _format_context(self, context: List[Dict[str, str]]) -> str:
        if not context:
            return "No previous context."
        formatted = ""
        for msg in context[-5:]:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            formatted += f"{role}: {content}\n"
        return formatted.strip()

    async def route(self, query: str, context: List[Dict[str, str]]) -> str:
        """
        Evaluates the query and context to return the target agent name.
        Returns 'chat_agent' or 'legacy_engine'.
        """
        logger.info(f"[MasterRouter] Evaluating routing for query: '{query[:50]}...'")
        
        try:
            formatted_context = self._format_context(context)
            system_prompt = ROUTER_SYSTEM_PROMPT.replace("{context}", formatted_context)
            
            api_key = await key_pool.lease_key()
            if not api_key:
                raise RouterExecutionError("No API key available for Router.")
                
            client = AsyncGroq(api_key=api_key)
            
            # Dynamically fetch the model from environment/config
            # Defaults to whatever the user has set or the oss model.
            main_model = getattr(self.config, "MAIN_LLM_MODEL", os.getenv("MAIN_LLM_MODEL", "openai/gpt-oss-120b"))
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ]
            
            response = await client.chat.completions.create(
                model=main_model,
                messages=messages,
                temperature=0.0,  # Zero temperature for deterministic JSON output
                max_tokens=150,
                response_format={"type": "json_object"}
            )
            
            output = response.choices[0].message.content.strip()
            parsed = json.loads(output)
            
            assigned_agent = parsed.get("assign_to", "legacy_engine")
            
            # Failsafe validation
            if assigned_agent not in ["chat_agent", "legacy_engine"]:
                assigned_agent = "legacy_engine"
                
            logger.info(f"[MasterRouter] Routed to: {assigned_agent}")
            return assigned_agent
            
        except Exception as e:
            logger.error(f"[MasterRouter] FATAL ERROR: {e}. Falling back to legacy_engine.")
            return "legacy_engine"
