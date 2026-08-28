"""
Module self-test - runs at backend startup to detect which critical modules
actually loaded vs fell back to stubs. Logs a clear status table and exposes
a programmatic API for the /health/modules endpoint.

Usage:
    from modules.module_selftest import run_module_selftest, get_module_status
    await run_module_selftest()    # call from main.py startup
    report = get_module_status()   # for the health endpoint
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from core.module_registry import registry

logger = logging.getLogger("MAX.SelfTest")


# Module -> list of function names we expect to be real (not stubs).
CRITICAL_MODULES: Dict[str, List[str]] = {
    "modules.llm": ["get_response", "get_greeting", "get_acknowledgment"],
    "modules.memory": ["get_memory_manager"],
    "modules.skills": ["get_skills_engine"],
    "modules.tts": ["generate_tts"],
    "modules.stt": ["transcribe_audio", "is_valid_transcript"],
    "modules.Intent_engine": ["get_intent_engine"],
    "modules.gatekeeper": ["get_gatekeeper"],
    "modules.gemini_router": ["get_gemini_router"],
    "modules.skill_rag": ["get_skill_rag"],
    "modules.learning_engine": ["get_learning_engine"],
    "modules.knowledge_base": ["get_knowledge_base"],
    "modules.knowledge_indexer": ["get_knowledge_indexer"],
    "modules.email_agent": ["get_email_agent"],
    "modules.calendar_agent": ["get_calendar_agent"],
    "modules.browser_agent": ["get_browser_agent"],
    "modules.smarthome_agent": ["get_smarthome_agent"],
    "modules.reminder_agent": ["start_reminder_daemon"],
    "modules.device_security": ["get_security_manager"],
    "modules.agent_loop": ["get_agent_loop", "is_complex_goal"],
    "modules.listening_manager": ["ListeningManager"],
    "modules.plugin_loader": ["get_plugin_loader"],
}

OPTIONAL_MODULES: Dict[str, List[str]] = {
    "modules.health_buddy": ["HealthBuddy"],
    "modules.code_engine": ["get_code_engine"],
    "modules.web_autopilot": ["LAST_BOT_BYPASS_URL"],
}


# Status: {module_name: {"ok": bool, "missing": [...], "loaded_via": "real|stub|partial"}}
_status: Dict[str, Dict[str, Any]] = {}
_last_run: float = 0.0


def _probe_module(name: str, funcs: List[str]) -> Dict[str, Any]:
    """
    Try to load each function from a module. Returns a status dict.

    A function is considered 'real' if it's not the registry's stub identity.
    Stubs are usually the _noop / _noop_none / _async_noop fallbacks defined
    in agent_core.py / main.py.
    """
    info: Dict[str, Any] = {"ok": True, "missing": [], "loaded_via": "real", "funcs": {}}
    for fn in funcs:
        try:
            mod = registry.get_module(name)
            if mod is None:
                # Module not in registry - try direct get_function which has fallback
                fn_obj = registry.get_function(name, fn, fallback=None)
            else:
                fn_obj = getattr(mod, fn, None)
            if fn_obj is None:
                info["missing"].append(fn)
                info["ok"] = False
                info["funcs"][fn] = "missing"
            else:
                info["funcs"][fn] = "real"
        except Exception as e:
            info["missing"].append(fn)
            info["ok"] = False
            info["funcs"][fn] = f"error: {type(e).__name__}"
    if info["missing"]:
        info["loaded_via"] = "partial" if len(info["missing"]) < len(funcs) else "stub"
    return info


async def run_module_selftest(force: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Probe every critical module. Idempotent within a 30s window unless force=True.
    """
    global _last_run
    now = time.time()
    if not force and _status and (now - _last_run) < 30:
        return _status

    _status.clear()
    for mod_name, funcs in CRITICAL_MODULES.items():
        try:
            info = await asyncio.to_thread(_probe_module, mod_name, funcs)
        except Exception as e:
            info = {"ok": False, "missing": funcs[:], "loaded_via": "error", "error": str(e)}
        _status[mod_name] = info

    for mod_name, funcs in OPTIONAL_MODULES.items():
        try:
            info = await asyncio.to_thread(_probe_module, mod_name, funcs)
            info["optional"] = True
        except Exception as e:
            info = {"ok": False, "missing": funcs[:], "loaded_via": "error", "optional": True, "error": str(e)}
        _status[mod_name] = info

    _last_run = now

    # Log a compact summary
    crit = [(k, v) for k, v in _status.items() if not v.get("optional")]
    ok = sum(1 for _, v in crit if v["ok"])
    bad = [(k, v) for k, v in crit if not v["ok"]]
    logger.info(f"Module self-test: {ok}/{len(crit)} critical modules OK")
    for name, info in bad:
        logger.warning(f"  - {name}: missing={info.get('missing', [])}")
    return _status


def get_module_status() -> Dict[str, Any]:
    """Public API for /health/modules endpoint."""
    crit = [(k, v) for k, v in _status.items() if not v.get("optional")]
    ok = sum(1 for _, v in crit if v["ok"])
    return {
        "ran_at": _last_run,
        "ok": ok,
        "total": len(crit),
        "all_ok": ok == len(crit) and len(crit) > 0,
        "modules": _status,
    }
