"""Exceptions specific to the chat agent."""
from ..exceptions import SubAgentExecutionError

class ChatGenerationError(SubAgentExecutionError):
    """Raised when the LLM fails to generate a chat response."""
    pass

class ChatContextFormattingError(SubAgentExecutionError):
    """Raised when the restricted chat context cannot be formatted properly."""
    pass
