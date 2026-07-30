# Path: backend/modules/calendar_agent.py
# Use: Manages calendars, events, and scheduling tasks.
"""
calendar_agent.py — MAX v4.0
Local .ics calendar (zero setup, offline). No Google API needed.
Skills: calendar_today, calendar_add, calendar_week
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from config import config

logger = logging.getLogger("MAX.CALENDAR")

CALENDAR_FILE = Path(config.DATA_DIR) / "calendar.json"


def _load_events() -> List[Dict]:
    if CALENDAR_FILE.exists():
        try:
            return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_events(events: List[Dict]):
    CALENDAR_FILE.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")


class CalendarAgent:
    """Local JSON-based calendar. No API keys, no cloud."""

    def today(self) -> str:
        events = _load_events()
        today_str = datetime.now().strftime("%Y-%m-%d")
        todays = [e for e in events if e.get("date", "").startswith(today_str)]
        todays.sort(key=lambda x: x.get("time", ""))
        if not todays:
            return f"Aaj {today_str} — koi schedule nahi hai boss. Free ho!"
        lines = [f"📅 Aaj ka schedule ({today_str}):"]
        for e in todays:
            lines.append(f"  {e.get('time', '?')} — {e.get('title', 'No title')}")
        return "\n".join(lines)

    def week(self) -> str:
        events = _load_events()
        now = datetime.now()
        week_dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        week_events = [e for e in events if any(e.get("date", "").startswith(d) for d in week_dates)]
        week_events.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
        if not week_events:
            return "Is hafte koi schedule nahi hai boss. Chill mode on!"
        lines = ["📅 Is hafte ka schedule:"]
        for e in week_events:
            lines.append(f"  {e.get('date', '?')} {e.get('time', '?')} — {e.get('title', 'No title')}")
        return "\n".join(lines)

    def add_event(self, title: str, date_str: str = "", time_str: str = "") -> str:
        try:
            from modules.date_utils import parse_natural_datetime
            parsed_date, parsed_time = parse_natural_datetime(date_str, time_str)
        except Exception:
            parsed_date = datetime.now().strftime("%Y-%m-%d")
            parsed_time = "09:00"

        events = _load_events()
        events.append({
            "title": title or "Event",
            "date": parsed_date,
            "time": parsed_time,
            "created": datetime.now().isoformat(),
        })
        _save_events(events)
        return f"Event added: '{title or 'Event'}' on {parsed_date} at {parsed_time}."


# Singleton
_calendar_agent: Optional[CalendarAgent] = None


def get_calendar_agent() -> CalendarAgent:
    global _calendar_agent
    if _calendar_agent is None:
        _calendar_agent = CalendarAgent()
    return _calendar_agent
