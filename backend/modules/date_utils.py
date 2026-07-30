# Path: backend/modules/date_utils.py
# Use: Flexible natural language date and time parser for MAX scheduling and calendar skills.
"""
date_utils.py — MAX v5.0

Parses human date and time expressions like:
  - "today", "aaj", "tomorrow", "kal", "day after tomorrow", "parso"
  - "3 pm", "3pm", "15:00", "9:30 am", "9am", "in 15 minutes", "in 2 hours"
  - "2026-07-30", "30-07-2026", "30th July"

Returns (date_str: "YYYY-MM-DD", time_str: "HH:MM")
Never fails — defaults gracefully to today / current or target time.
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Tuple

logger = logging.getLogger("MAX.DATE_UTILS")

_TIME_PATTERN = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', re.IGNORECASE)
_RELATIVE_MINUTES = re.compile(r'\bin\s*(\d+)\s*(?:min|mins|minute|minutes)\b', re.IGNORECASE)
_RELATIVE_HOURS = re.compile(r'\bin\s*(\d+)\s*(?:hr|hrs|hour|hours)\b', re.IGNORECASE)


def parse_natural_datetime(date_raw: str = "", time_raw: str = "") -> Tuple[str, str]:
    """
    Parses fuzzy natural date and time strings into strictly formatted ("YYYY-MM-DD", "HH:MM").
    
    Examples:
      ("today", "3 pm") -> ("2026-07-30", "15:00")
      ("tomorrow", "10 am") -> ("2026-07-31", "10:00")
      ("2026-07-30 15:00", "") -> ("2026-07-30", "15:00")
      ("in 15 minutes", "") -> ("2026-07-30", "09:07")
    """
    now = datetime.now()
    target_date = now.date()
    target_time = (now + timedelta(minutes=5)).time()  # default: 5 mins from now

    combined = f"{date_raw} {time_raw}".strip().lower()

    # ── Check for relative offsets ("in X minutes", "in X hours") ──
    m_min = _RELATIVE_MINUTES.search(combined)
    if m_min:
        mins = int(m_min.group(1))
        target_dt = now + timedelta(minutes=mins)
        return target_dt.strftime("%Y-%m-%d"), target_dt.strftime("%H:%M")

    m_hr = _RELATIVE_HOURS.search(combined)
    if m_hr:
        hrs = int(m_hr.group(1))
        target_dt = now + timedelta(hours=hrs)
        return target_dt.strftime("%Y-%m-%d"), target_dt.strftime("%H:%M")

    # ── Date Parsing ──
    d_clean = date_raw.strip().lower()
    if not d_clean or d_clean in ("today", "aaj", "tonight", "this evening"):
        target_date = now.date()
    elif d_clean in ("tomorrow", "kal", "next day"):
        target_date = now.date() + timedelta(days=1)
    elif d_clean in ("day after tomorrow", "parso"):
        target_date = now.date() + timedelta(days=2)
    else:
        # Try YYYY-MM-DD
        m_iso = re.search(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', d_clean)
        if m_iso:
            try:
                target_date = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).date()
            except ValueError:
                pass
        else:
            # Try DD-MM-YYYY or DD/MM/YYYY
            m_dmy = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', d_clean)
            if m_dmy:
                try:
                    target_date = datetime(int(m_dmy.group(3)), int(m_dmy.group(2)), int(m_dmy.group(1))).date()
                except ValueError:
                    pass

    # ── Time Parsing ──
    t_clean = f"{time_raw} {date_raw}".strip().lower()
    
    # Try HH:MM 24h
    m_24h = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', time_raw.strip())
    if m_24h:
        try:
            target_time = datetime.strptime(f"{m_24h.group(1).zfill(2)}:{m_24h.group(2)}", "%H:%M").time()
        except ValueError:
            pass
    else:
        # Try 12h with AM/PM (e.g. "3 pm", "3pm", "10:30 am")
        m_ampm = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', t_clean)
        if m_ampm:
            hr = int(m_ampm.group(1))
            mn = int(m_ampm.group(2)) if m_ampm.group(2) else 0
            ampm = m_ampm.group(3).lower()
            if ampm == "pm" and hr < 12:
                hr += 12
            elif ampm == "am" and hr == 12:
                hr = 0
            try:
                target_time = datetime.strptime(f"{str(hr).zfill(2)}:{str(mn).zfill(2)}", "%H:%M").time()
            except ValueError:
                pass
        else:
            # Standalone hour number (e.g. "at 3")
            m_num = re.search(r'\b(?:at|by)\s*(\d{1,2})\b', t_clean)
            if m_num:
                hr = int(m_num.group(1))
                if 1 <= hr <= 7:  # Assume PM for 1..7 unless specified
                    hr += 12
                try:
                    target_time = datetime.strptime(f"{str(hr).zfill(2)}:00", "%H:%M").time()
                except ValueError:
                    pass

    date_out = target_date.strftime("%Y-%m-%d")
    time_out = target_time.strftime("%H:%M")
    logger.debug(f"Parsed natural date/time '{date_raw}' '{time_raw}' → {date_out} {time_out}")
    return date_out, time_out
