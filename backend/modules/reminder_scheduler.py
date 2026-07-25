# Path: backend/modules/reminder_scheduler.py
# Use: Schedules timed callbacks for system reminders.
"""
reminder_scheduler.py — MAX Real-Time Reminder Engine v1.0

WHY THIS EXISTS:
  The existing reminder_set / reminder_list skills only STORE reminders.
  They never actually fire — MAX never proactively speaks at the due time.
  This module fixes that by running a background thread that checks every
  30 seconds and fires TTS when a reminder is due.

HOW IT WORKS:
  1. reminder_set skill writes to data/reminders.json (datetime + text)
  2. ReminderScheduler thread wakes every 30s
  3. If now >= due_time (within 1-min window) → fire TTS → mark fired
  4. Fired reminders are cleaned up after 2 minutes

INTEGRATION:
  Start in agent_core.py at boot:
    from modules.reminder_scheduler import get_scheduler
    get_scheduler(config).start()

  reminder_set skill calls:
    from modules.reminder_scheduler import ReminderStore
    ReminderStore(config).add("1:00pm", "mujhe xyz kaam karna hai")

DATETIME PARSING:
  Handles natural input:
    "1:00pm", "13:00", "1pm", "2:30 PM", "tomorrow 9am"
  All stored as ISO-format strings in reminders.json.
"""

import json
import logging
import re
import threading
import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MAX.REMINDER")


# ══════════════════════════════════════════════════════
# DATETIME PARSER
# Converts natural time strings to datetime objects.
# "1pm" → today 13:00 | "tomorrow 9am" → tomorrow 09:00
# ══════════════════════════════════════════════════════

class TimeParser:

    # Matches: 1pm, 1:30pm, 13:00, 1:30 PM, 9 am
    _TIME_RE = re.compile(
        r'(?P<hour>\d{1,2})'
        r'(?::(?P<minute>\d{2}))?'
        r'\s*(?P<ampm>am|pm)?',
        re.IGNORECASE
    )

    @staticmethod
    def parse(raw: str, reference: Optional[datetime] = None) -> Optional[datetime]:
        """
        Parse a natural time string into a datetime.
        Reference defaults to now (today).
        Returns None if parsing fails.
        """
        ref   = reference or datetime.now()
        lower = raw.strip().lower()

        # Determine base date
        if "tomorrow" in lower:
            base = (ref + timedelta(days=1)).date()
            lower = lower.replace("tomorrow", "").strip()
        else:
            base = ref.date()

        # Match time portion
        m = TimeParser._TIME_RE.search(lower)
        if not m:
            return None

        hour   = int(m.group("hour"))
        minute = int(m.group("minute") or 0)
        ampm   = (m.group("ampm") or "").lower()

        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None

        due = datetime(base.year, base.month, base.day, hour, minute)

        # If time already passed today and no "tomorrow" → push to tomorrow
        if due <= ref and "tomorrow" not in raw.lower():
            due += timedelta(days=1)

        return due


# ══════════════════════════════════════════════════════
# REMINDER STORE
# Handles read/write of data/reminders.json
# Thread-safe via a reentrant lock.
# ══════════════════════════════════════════════════════

class ReminderStore:

    def __init__(self, config):
        self.path = Path(getattr(config, "BASE_DIR", ".")) / "data" / "reminders.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def add(self, time_str: str, text: str) -> str:
        """
        Parse time_str, create reminder, save to file.
        Returns confirmation string for TTS.
        """
        due = TimeParser.parse(time_str)
        if not due:
            return f"Samajh nahi aaya '{time_str}' — try karo '1:00pm' ya '14:30' format mein."

        entry = {
            "id":       f"{int(due.timestamp())}_{hash(text) & 0xFFFF:04x}",
            "text":     text.strip(),
            "datetime": due.isoformat(),
            "fired":    False,
        }

        with self._lock:
            reminders = self._read()
            reminders.append(entry)
            self._write(reminders)

        formatted = due.strftime("%d %b %I:%M %p")
        return f"Done boss. {formatted} ko remind karunga: {text}"

    def list_pending(self) -> list[dict]:
        with self._lock:
            all_r = self._read()
        now = datetime.now()
        return [
            r for r in all_r
            if not r["fired"]
            and datetime.fromisoformat(r["datetime"]) > now
        ]

    def get_due(self) -> list[dict]:
        """Returns reminders due within the next 30s window."""
        with self._lock:
            reminders = self._read()

        now   = datetime.now()
        due   = []
        # Fire if: not yet fired AND due time is within ±30 seconds of now
        for r in reminders:
            if r["fired"]:
                continue
            dt   = datetime.fromisoformat(r["datetime"])
            diff = (now - dt).total_seconds()
            # Fire window: past due up to 30s late (catches scheduler sleep offset)
            if -5 <= diff <= 30:
                due.append(r)

        return due

    def mark_fired(self, reminder_id: str):
        with self._lock:
            reminders = self._read()
            for r in reminders:
                if r["id"] == reminder_id:
                    r["fired"] = True
            self._write(reminders)

    def cleanup(self):
        """Remove reminders that fired more than 5 minutes ago."""
        with self._lock:
            reminders = self._read()
            now = datetime.now()
            fresh = [
                r for r in reminders
                if not r["fired"]
                or (datetime.fromisoformat(r["datetime"]) > now - timedelta(minutes=5))
            ]
            self._write(fresh)

    def _read(self) -> list[dict]:
        try:
            if self.path.exists():
                return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"ReminderStore: read error — {e}")
        return []

    def _write(self, reminders: list[dict]):
        try:
            self.path.write_text(
                json.dumps(reminders, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except OSError as e:
            logger.error(f"ReminderStore: write error — {e}")


# ══════════════════════════════════════════════════════
# REMINDER SCHEDULER
# Background daemon thread. Checks every 30 seconds.
# Fires TTS via asyncio.run() — creates its own event
# loop inside the thread (safe, reminders are rare).
# ══════════════════════════════════════════════════════

class ReminderScheduler:

    CHECK_INTERVAL  = 30   # seconds between checks
    CLEANUP_EVERY   = 20   # cleanup every N checks (~10 minutes)

    def __init__(self, config):
        self.config  = config
        self.store   = ReminderStore(config)
        self._thread : Optional[threading.Thread] = None
        self._running = False
        self._check_count = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("ReminderScheduler: already running")
            return

        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            name="ReminderScheduler",
            daemon=True   # dies when main process exits — no cleanup needed
        )
        self._thread.start()
        logger.info("⏰ ReminderScheduler started")

    def stop(self):
        self._running = False
        logger.info("ReminderScheduler: stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Background loop ────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"ReminderScheduler: tick error — {e}", exc_info=True)

            time.sleep(self.CHECK_INTERVAL)

    def _tick(self):
        self._check_count += 1

        # Periodic cleanup
        if self._check_count % self.CLEANUP_EVERY == 0:
            self.store.cleanup()

        due = self.store.get_due()
        if not due:
            return

        for reminder in due:
            logger.info(f"⏰ Reminder firing: '{reminder['text']}'")
            self.store.mark_fired(reminder["id"])
            # Fire TTS in this thread's own event loop
            asyncio.run(self._fire(reminder["text"]))

    async def _fire(self, text: str):
        """
        Speaks the reminder. Called from background thread via asyncio.run().
        Format: "Boss, reminder — xyz kaam karna hai"
        """
        message = f"Boss, reminder — {text}"
        logger.info(f"ReminderScheduler TTS: {message}")
        try:
            from tts_engine import speak
            await speak(message)
        except Exception as e:
            logger.error(f"ReminderScheduler: TTS failed — {e}")



# ══════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════

_scheduler_instance: Optional[ReminderScheduler] = None


def get_scheduler(config) -> ReminderScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = ReminderScheduler(config)
    return _scheduler_instance
