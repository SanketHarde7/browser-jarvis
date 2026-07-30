# Path: backend/modules/action_scheduler.py
import time
import json
import asyncio
import threading
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("MAX.SCHEDULER")

class ActionScheduler:
    def __init__(self, config, skills_engine):
        self.config = config
        self.skills_engine = skills_engine
        self.tasks_file = Path(config.DATA_DIR) / "scheduled_tasks.json"
        self._ensure_file()
        self.running = False

    def _ensure_file(self):
        self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.tasks_file.exists():
            self.tasks_file.write_text("[]", encoding="utf-8")

    def add_task(self, execute_at: str, skill_name: str, params: list) -> str:
        """Saves a new task to the JSON queue, auto-parsing natural dates."""
        try:
            from modules.date_utils import parse_natural_datetime
            date_part, time_part = parse_natural_datetime(execute_at, "")
            execute_at_clean = f"{date_part} {time_part}"
        except Exception:
            execute_at_clean = datetime.now().strftime("%Y-%m-%d %H:%M")

        tasks = json.loads(self.tasks_file.read_text(encoding="utf-8"))
        task_id = f"task_{int(time.time())}"
        
        tasks.append({
            "id": task_id,
            "execute_at": execute_at_clean,
            "skill_name": skill_name,
            "params": params,
            "status": "pending"
        })
        
        self.tasks_file.write_text(json.dumps(tasks, indent=4), encoding="utf-8")
        logger.info(f"Task Scheduled 📅: [{skill_name}] for {execute_at_clean}")
        return f"Done! Scheduled {skill_name} task for {execute_at_clean}."

    def start(self):
        """Starts the background loop if not already running."""
        if not self.running:
            self.running = True
            threading.Thread(target=self._worker_loop, daemon=True).start()
            logger.info("⏰ Action Scheduler Background Daemon Started.")

    def _worker_loop(self):
        """Checks the JSON file every 30 seconds for pending tasks."""
        while self.running:
            try:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                tasks = json.loads(self.tasks_file.read_text(encoding="utf-8"))
                updated = False

                for task in tasks:
                    if task["status"] == "pending" and task["execute_at"] <= now_str:
                        logger.info(f"⏳ Executing Scheduled Task: {task['skill_name']}")
                        
                        # Background execution logic
                        self._trigger_skill(task["skill_name"], task["params"])
                        
                        task["status"] = "completed"
                        updated = True

                if updated:
                    self.tasks_file.write_text(json.dumps(tasks, indent=4), encoding="utf-8")

            except Exception as e:
                logger.error(f"Scheduler Loop Error: {e}")

            time.sleep(30) # Rest for 30 seconds before checking again

    def _trigger_skill(self, skill_name, params):
        try:
            func = self.skills_engine.skills_registry.get(skill_name)
            if not func:
                logger.error(f"Scheduled skill '{skill_name}' not found.")
                return

            if asyncio.iscoroutinefunction(func):
                # Run Async skills (like deep_research might be in future) safely
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(func(*params))
                loop.close()
            else:
                # Run Sync skills (like whatsapp_message)
                func(*params)
        except Exception as e:
            logger.error(f"Scheduled Execution Failed: {e}")