import importlib
import logging
from typing import Any, Dict, Optional, Callable

logger = logging.getLogger("MAX.Registry")

# Sentinel value for modules that failed to load
_FAILED = object()

class ModuleRegistry:
    """
    A dynamic registry that safely loads and isolates modules.
    If a module crashes during import, it gracefully catches the error
    and prevents the main orchestrator from crashing.
    Failed modules are cached so we don't spam repeated import attempts.
    """
    _instance = None
    _modules: Dict[str, Any] = {}
    _failures: Dict[str, str] = {}  # module_path -> error message

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ModuleRegistry, cls).__new__(cls)
            cls._modules = {}
            cls._failures = {}
        return cls._instance

    @classmethod
    def get_module(cls, module_path: str, fallback: Any = None) -> Any:
        """
        Dynamically load a Python module safely.
        Caches both successful and failed loads.
        """
        if module_path in cls._modules:
            cached = cls._modules[module_path]
            return fallback if cached is _FAILED else cached
        try:
            module = importlib.import_module(module_path)
            cls._modules[module_path] = module
            logger.info(f"✅ Loaded module: {module_path}")
            return module
        except Exception as e:
            cls._modules[module_path] = _FAILED
            cls._failures[module_path] = str(e)
            logger.error(f"❌ Failed to load module {module_path}: {e}")
            return fallback

    @classmethod
    def get_function(cls, module_path: str, function_name: str, fallback: Any = None) -> Callable:
        """
        Dynamically load a module and return a specific function/class.
        Returns `fallback` if module or function is missing/crashed.
        """
        module = cls.get_module(module_path)
        if module and hasattr(module, function_name):
            return getattr(module, function_name)
        
        if module_path not in cls._failures:
            logger.warning(f"⚠️ Could not resolve {function_name} in {module_path}. Using fallback.")
        return fallback

    @classmethod
    def get_health_report(cls) -> Dict[str, str]:
        """
        Returns a dict of module statuses for diagnostics.
        """
        report = {}
        for path, mod in cls._modules.items():
            if mod is _FAILED:
                report[path] = f"FAILED: {cls._failures.get(path, 'unknown')}"
            else:
                report[path] = "OK"
        return report

    @classmethod
    def register_skill(cls, skill_instance: Any):
        """Register a BaseSkill instance."""
        if not hasattr(cls, '_skills'):
            cls._skills = {}
        cls._skills[skill_instance.name] = skill_instance
        logger.info(f"✅ Registered skill: {skill_instance.name}")

    @classmethod
    def get_skill(cls, skill_name: str) -> Optional[Any]:
        """Retrieve a registered skill by name."""
        if not hasattr(cls, '_skills'):
            return None
        return cls._skills.get(skill_name)

    @classmethod
    def list_skills(cls) -> Dict[str, Any]:
        """List all registered skills."""
        if not hasattr(cls, '_skills'):
            return {}
        return dict(cls._skills)

    @classmethod
    def discover_skills(cls, module_path: str):
        """Discover and register all BaseSkill subclasses from a module."""
        module = cls.get_module(module_path)
        if not module or module is _FAILED:
            return

        import inspect
        from skills.base_skill import BaseSkill
        
        for name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and issubclass(obj, BaseSkill) and obj is not BaseSkill:
                try:
                    skill_instance = obj()
                    cls.register_skill(skill_instance)
                except Exception as e:
                    logger.error(f"❌ Failed to instantiate skill {name} from {module_path}: {e}")

registry = ModuleRegistry()

# Auto-discover core skills
registry.discover_skills("skills.core_skills")

