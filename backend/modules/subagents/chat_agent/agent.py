"""Core execution logic for the chat agent."""
import asyncio
from typing import Dict, Any, List
from groq import AsyncGroq

from ..base_agent import BaseSubAgent
from .prompts import CHAT_SYSTEM_PROMPT
from .errors import ChatGenerationError, ChatContextFormattingError

# Assumes key_pool is available globally in api_utils
from api_utils import key_pool

class ChatAgent(BaseSubAgent):
    """
    Subagent responsible exclusively for general conversation.
    Has 0 skills and no capability to perform actions.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.model = "llama-3.3-70b-versatile"
        
    def _format_context(self, context: List[Dict[str, str]]) -> str:
        """Format the last N messages into a string for the prompt."""
        try:
            if not context:
                return "No previous context."
            
            # Context is expected to be a list of dicts with 'role' and 'content'
            # As per user rules, this agent gets max 5 past interactions.
            formatted = ""
            for msg in context[-5:]:
                role = msg.get("role", "user").capitalize()
                content = msg.get("content", "")
                formatted += f"{role}: {content}\n"
            return formatted.strip()
        except Exception as e:
            raise ChatContextFormattingError(f"Failed to format context: {str(e)}")

    async def process(self, query: str, context: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Generates a chat response based on the query and limited context.
        """
        self._log_info(f"Processing chat query: '{query[:50]}...'")
        
        try:
            formatted_context = self._format_context(context)
            system_prompt = CHAT_SYSTEM_PROMPT.replace("{context}", formatted_context)
            
            # Fetch API key
            api_key = await key_pool.lease_key()
            if not api_key:
                raise ChatGenerationError("No API key available for ChatAgent.")
                
            client = AsyncGroq(api_key=api_key)
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ]
            
            import os
            main_model = getattr(self.config, "MAIN_LLM_MODEL", os.getenv("MAIN_LLM_MODEL", "openai/gpt-oss-120b"))
            
            response = await client.chat.completions.create(
                model=main_model,
                messages=messages,
                temperature=0.7,
                max_tokens=256
            )
            
            reply = response.choices[0].message.content.strip()
            self._log_info("Successfully generated chat response.")
            
            return {
                "response": reply,
                "intent": "general_chat",
                "status": "success"
            }
            
        except ChatContextFormattingError as ce:
            self._log_error("Context formatting failed", ce)
            raise
        except Exception as e:
            self._log_error("LLM generation failed", e)
            raise ChatGenerationError(f"Failed to generate response: {str(e)}")
