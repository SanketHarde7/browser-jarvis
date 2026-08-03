from typing import Any, Dict

class BaseSkill:
    """
    Base class for all dynamically loaded MAX skills.
    Every skill must inherit from this class and implement the execute() method.
    """
    
    @property
    def name(self) -> str:
        """Internal identifier for the skill (e.g., 'sysinfo', 'time_now')."""
        raise NotImplementedError("Skill must define a name property.")

    @property
    def description(self) -> str:
        """Brief description of what the skill does."""
        return "No description provided."

    def execute(self, *args, **kwargs) -> str:
        """
        Executes the skill logic.
        Returns a string response that will be spoken/shown to the user.
        Raises exceptions on failure, which will be caught by the SkillsEngine.
        """
        raise NotImplementedError("Skill must implement the execute method.")
