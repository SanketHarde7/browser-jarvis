# Path: backend/modules/blackboard.py
# Use: SQLite-backed shared state store for Orchestrator tasks.
import json, sqlite3, threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import config

STATUSES_DONE = {"success", "failed", "skipped_redundant", "skipped_by_user"}

class Blackboard:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or (config.DATA_DIR / "blackboard.sqlite3"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        with self._lock, self._connect() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS blackboard (
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                depends_on TEXT,
                input_data TEXT,
                output_data TEXT,
                error_message TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                PRIMARY KEY (task_id, step_id)
            )""")
            con.commit()

    def init_task(self, task_id: str, roadmap: Dict[str, Any]):
        for step in roadmap.get("steps", []):
            self.create_step(task_id, step.get("step_id"), step.get("agent"), step.get("depends_on", []), step)

    def create_step(self, task_id: str, step_id: str, agent_name: str, depends_on=None, input_data=None):
        now = datetime.utcnow().isoformat()
        with self._lock, self._connect() as con:
            con.execute("""INSERT OR REPLACE INTO blackboard
            (task_id, step_id, agent_name, status, depends_on, input_data, output_data, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM blackboard WHERE task_id=? AND step_id=?), ?), ?)""",
            (task_id, step_id, agent_name, "pending", json.dumps(depends_on or []), json.dumps(input_data or {}), "{}", None, task_id, step_id, now, now))
            con.commit()

    def update_step(self, task_id: str, step_id: str, status: str, output_data: Any=None, error_message: str=None):
        with self._lock, self._connect() as con:
            con.execute("UPDATE blackboard SET status=?, output_data=?, error_message=?, updated_at=? WHERE task_id=? AND step_id=?",
                        (status, json.dumps(output_data or {}), error_message, datetime.utcnow().isoformat(), task_id, step_id))
            con.commit()

    def get_task_steps(self, task_id: str) -> List[Dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM blackboard WHERE task_id=? ORDER BY created_at, step_id", (task_id,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r); 
            for k in ("depends_on","input_data","output_data"):
                try: d[k]=json.loads(d.get(k) or "{}")
                except Exception: d[k]=[] if k=="depends_on" else {}
            out.append(d)
        return out

    def get_ready_steps(self, task_id: str) -> List[Dict[str, Any]]:
        steps = self.get_task_steps(task_id); by_id={s["step_id"]:s for s in steps}
        return [s for s in steps if s["status"]=="pending" and all(by_id.get(dep,{}).get("status") in STATUSES_DONE for dep in (s.get("depends_on") or []))]

    def is_task_complete(self, task_id: str) -> bool:
        steps=self.get_task_steps(task_id)
        return bool(steps) and all(s["status"] in STATUSES_DONE for s in steps)

    def summarize_task(self, task_id: str) -> str:
        steps=self.get_task_steps(task_id)
        counts={}
        for s in steps: counts[s["status"]]=counts.get(s["status"],0)+1
        return f"Task {task_id}: " + ", ".join(f"{v} {k}" for k,v in sorted(counts.items()))

    def find_interrupted_tasks(self) -> List[str]:
        with self._connect() as con:
            rows=con.execute("SELECT DISTINCT task_id FROM blackboard WHERE status IN ('running','pending','needs_user_input')").fetchall()
        return [r[0] for r in rows]

blackboard = Blackboard()
