# Path: backend/modules/resource_manager.py
# Use: Dynamic capacity gate for Orchestrator sub-agent dispatch and Groq budget.
import asyncio
from typing import Any, Dict, List
import psutil
from api_utils import key_pool

class ResourceManager:
    def __init__(self):
        self._running = 0
        self._lock = asyncio.Lock()

    def api_stats(self) -> Dict[str, Dict[str, Any]]:
        return key_pool.stats()

    def concurrency_ceiling(self) -> int:
        stats = self.api_stats()
        available = sum(1 for s in stats.values() if s.get("cooling_for_s", 0) <= 0 and s.get("used_last_60s", 0) < s.get("rpm_limit", 1)) or 1
        cpu = psutil.cpu_percent(interval=0.05)
        ram = psutil.virtual_memory().percent
        local = 2 if cpu > 85 or ram > 85 else 4 if cpu > 70 or ram > 75 else 8
        return max(1, min(local, available * 2))

    async def filter_by_capacity(self, ready_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        async with self._lock:
            slots = max(0, self.concurrency_ceiling() - self._running)
            chosen = ready_steps[:slots]
            self._running += len(chosen)
            return chosen

    async def release(self, count: int = 1):
        async with self._lock:
            self._running = max(0, self._running - count)

resource_manager = ResourceManager()
