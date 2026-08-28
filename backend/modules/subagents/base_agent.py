"""Abstract base class for all subagents."""
from abc import ABC, abstractmethod
import logging
from typing import Dict, Any, List

from .exceptions import SubAgentExecutionError

logger = logging.getLogger("MAX.SubAgents")

class BaseSubAgent(ABC):
    """
    The fundamental blueprint for all MAX subagents.
    Enforces strict modularity, localized context, and standard logging.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the subagent with the global config.
        """
        self.config = config
        self.agent_name = self.__class__.__name__

    @abstractmethod
    async def process(self, query: str, context: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Process the user query given the restricted context.
        
        Args:
            query (str): The specific user request routed to this agent.
            context (List[Dict[str, str]]): The strictly restricted list of past interactions.
            
        Returns:
            Dict[str, Any]: Must contain at least 'response' (str).
            
        Raises:
            SubAgentExecutionError: For any domain-specific failures.
        """
        pass

    def _log_info(self, message: str):
        """Standardized info logging for subagents."""
        logger.info(f"[{self.agent_name}] {message}")

    def _log_error(self, message: str, error: Exception = None):
        """Standardized error logging for subagents."""
        if error:
            logger.error(f"[{self.agent_name}] FATAL: {message} | Exception: {type(error).__name__} - {str(error)}")
        else:
            logger.error(f"[{self.agent_name}] FATAL: {message}")
