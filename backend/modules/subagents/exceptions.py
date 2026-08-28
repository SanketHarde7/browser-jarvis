"""Base exception hierarchy for the subagent orchestration system."""

class SubAgentExecutionError(Exception):
    """Base exception for all subagent failures. Allows catching any agent error gracefully."""
    pass

class InvalidContextError(SubAgentExecutionError):
    """Raised when the context provided to a subagent is invalid or malformed."""
    pass

class SubAgentTimeoutError(SubAgentExecutionError):
    """Raised when a subagent takes too long to execute its task."""
    pass

class SubAgentInitializationError(SubAgentExecutionError):
    """Raised when a subagent fails to initialize (e.g. missing API keys or dependencies)."""
    pass

class ToolExecutionError(SubAgentExecutionError):
    """Raised when a tool/skill inside a subagent fails to execute."""
    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool '{tool_name}' failed: {reason}")
