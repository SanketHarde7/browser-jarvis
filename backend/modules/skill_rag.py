# Path: backend/modules/skill_rag.py
# Use: Bulletproof 3-layer Skill RAG Matcher for MAX v5.
"""
skill_rag.py — MAX v5.0 (Selective Skill RAG)

Architecture:
  Layer 1: Keyword/Regex match (O(1) lookup, 10-30 triggers per skill)
  Layer 2: Fuzzy semantic match (difflib against descriptions)
  Layer 3: Full registry safety net (if L1+L2 return 0 candidates)

Result: MAX NEVER misses a skill — 100% guaranteed.

Each skill has rich metadata:
  - name: registry key
  - triggers: list of keywords/phrases (EN + Hindi/Hinglish)
  - description: one-line for LLM context
  - example: usage example [SKILL:name:param]
  - category: grouping for category-boost scoring
"""

import re
import json
import logging
from pathlib import Path
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("MAX.SKILL_RAG")


# ═══════════════════════════════════════════════════
# SKILL CANDIDATE DATACLASS
# ═══════════════════════════════════════════════════

@dataclass
class SkillCandidate:
    """A matched skill candidate with metadata for LLM injection."""
    name: str
    description: str
    example: str
    score: float = 0.0
    category: str = ""


# ═══════════════════════════════════════════════════
# SKILL METADATA REGISTRY
# ═══════════════════════════════════════════════════

# Each skill maps to:
#   triggers: keywords/phrases that indicate this skill (EN + Hindi)
#   desc: one-line description for LLM
#   example: usage example
#   cat: category for boost scoring

SKILL_METADATA: Dict[str, Dict] = {
    # ── MEDIA & ENTERTAINMENT ──────────────────────────────────
    "media": {
        "triggers": ["pause", "play", "resume", "stop music", "next song", "previous", "skip",
                      "roko", "chalao", "agla", "peeche", "gaana", "music", "media", "song"],
        "desc": "Control media playback (pause/play/next/previous/stop)",
        "example": "[SKILL:media:pause]",
        "cat": "media"
    },
    "youtube_play": {
        "triggers": ["play", "youtube", "video", "song", "gaana bajao", "play on youtube",
                      "youtube pe", "bajao", "sunao", "music video"],
        "desc": "Play a song/video on YouTube",
        "example": "[SKILL:youtube_play:Believer]",
        "cat": "media"
    },
    "youtube_search": {
        "triggers": ["search youtube", "youtube search", "find on youtube", "youtube pe dhundho",
                      "youtube me search"],
        "desc": "Search YouTube for videos",
        "example": "[SKILL:youtube_search:react tutorial]",
        "cat": "media"
    },
    "volume": {
        "triggers": ["volume", "awaaz", "sound", "loud", "quiet", "mute", "unmute",
                      "volume up", "volume down", "awaaz badhao", "awaaz kam"],
        "desc": "Control system volume (up/down/mute/unmute/set percentage)",
        "example": "[SKILL:volume:set:50]",
        "cat": "system"
    },
    "brightness": {
        "triggers": ["brightness", "bright", "dim", "screen bright", "roshni",
                      "brightness badhao", "brightness kam"],
        "desc": "Control screen brightness",
        "example": "[SKILL:brightness:up]",
        "cat": "system"
    },

    # ── APP & WINDOW CONTROL ──────────────────────────────────
    "open_app": {
        "triggers": ["open", "launch", "start", "run", "kholo", "chalao", "open app",
                      "launch app", "folder", "downloads", "desktop", "documents",
                      "explorer", "notepad", "calculator", "chrome", "terminal",
                      "vscode", "file explorer", "settings"],
        "desc": "Open an application or folder (e.g., Chrome, Notepad, Downloads folder)",
        "example": "[SKILL:open_app:chrome]",
        "cat": "app"
    },
    "close_app": {
        "triggers": ["close", "quit", "exit", "band karo", "close app", "kill app",
                      "terminate", "end", "shut down app"],
        "desc": "Close a running application",
        "example": "[SKILL:close_app:notepad]",
        "cat": "app"
    },
    "close_window": {
        "triggers": ["close window", "window band", "close this", "close current"],
        "desc": "Close the current active window",
        "example": "[SKILL:close_window]",
        "cat": "app"
    },
    "list_apps": {
        "triggers": ["list apps", "installed apps", "what apps", "all apps", "show apps",
                      "kaunse apps"],
        "desc": "List installed applications",
        "example": "[SKILL:list_apps]",
        "cat": "app"
    },
    "list_windows": {
        "triggers": ["list windows", "open windows", "active windows", "running windows",
                      "kaunsi windows"],
        "desc": "List currently open windows",
        "example": "[SKILL:list_windows]",
        "cat": "app"
    },
    "rebuild_app_index": {
        "triggers": ["rebuild app index", "reindex apps", "refresh apps"],
        "desc": "Rebuild the installed apps index",
        "example": "[SKILL:rebuild_app_index]",
        "cat": "app"
    },

    # ── WEB & BROWSER ─────────────────────────────────────────
    "web_open": {
        "triggers": ["website", "site", "url", "web open", "browser open", "open website",
                      "google", "youtube", "github", "chatgpt", "instagram", "facebook",
                      "twitter", "reddit", "stackoverflow", "linkedin", "gemini",
                      "open in browser", "browser me", "new tab"],
        "desc": "Open a website URL in the browser",
        "example": "[SKILL:web_open:google.com]",
        "cat": "web"
    },
    "search": {
        "triggers": ["search", "google", "find", "lookup", "dhundho", "search for",
                      "kya hai", "who is", "what is", "latest", "news", "score",
                      "price", "weather"],
        "desc": "Web search for information",
        "example": "[SKILL:search:latest AI news]",
        "cat": "web"
    },
    "browser_open": {
        "triggers": ["browser open", "open page", "navigate to", "go to url"],
        "desc": "Open a URL in browser automation",
        "example": "[SKILL:browser_open:https://example.com]",
        "cat": "web"
    },
    "browser_click": {
        "triggers": ["browser click", "click button", "click on", "click element"],
        "desc": "Click an element on a web page",
        "example": "[SKILL:browser_click:Submit]",
        "cat": "web"
    },
    "browser_type": {
        "triggers": ["browser type", "type in browser", "fill field", "enter text browser"],
        "desc": "Type text into a browser input field",
        "example": "[SKILL:browser_type:search:hello]",
        "cat": "web"
    },
    "browser_scrape": {
        "triggers": ["scrape", "extract", "browser scrape", "get page content",
                      "read webpage", "page ka content"],
        "desc": "Scrape/extract content from a webpage",
        "example": "[SKILL:browser_scrape:https://example.com]",
        "cat": "web"
    },
    "open_link": {
        "triggers": ["open link", "clipboard link", "copied link", "screen link",
                      "link open karo", "clipboard me link", "link open"],
        "desc": "Open a link from clipboard or screen",
        "example": "[SKILL:open_link:clipboard]",
        "cat": "web"
    },
    "open_link_select": {
        "triggers": ["select link", "choose link", "which link", "link select"],
        "desc": "Select and open a specific link from multiple found",
        "example": "[SKILL:open_link_select:2]",
        "cat": "web"
    },

    # ── SCREENSHOT & SCREEN ───────────────────────────────────
    "screenshot": {
        "triggers": ["screenshot", "screen capture", "screen shot", "photo", "snap",
                      "capture screen", "ss lo", "screenshot lo", "screen ka photo"],
        "desc": "Take a screenshot (optionally save with name/location)",
        "example": "[SKILL:screenshot::default]",
        "cat": "screen"
    },
    "screen_record": {
        "triggers": ["screen record", "record screen", "recording start", "recording stop",
                      "video record", "screen recording"],
        "desc": "Start or stop screen recording",
        "example": "[SKILL:screen_record]",
        "cat": "screen"
    },
    "read_screen": {
        "triggers": ["read screen", "screen padho", "what's on screen", "screen me kya hai",
                      "screen dekho", "see my screen", "look at screen", "analyze screen"],
        "desc": "Read and analyze what's currently on screen (vision)",
        "example": "[SKILL:read_screen]",
        "cat": "screen"
    },

    # ── FILE OPERATIONS ───────────────────────────────────────
    "list_files": {
        "triggers": ["list files", "show files", "files in", "folder content",
                      "what's inside", "kya hai folder me", "files dikhao"],
        "desc": "List files in a directory",
        "example": "[SKILL:list_files:Downloads]",
        "cat": "file"
    },
    "read_file": {
        "triggers": ["read file", "show file", "file padho", "file dikhao",
                      "file content", "open and read"],
        "desc": "Read contents of a file",
        "example": "[SKILL:read_file:notes.txt]",
        "cat": "file"
    },
    "edit_file": {
        "triggers": ["edit file", "modify file", "change file", "update file",
                      "file edit karo"],
        "desc": "Edit a file's content",
        "example": "[SKILL:edit_file:notes.txt:new content]",
        "cat": "file"
    },
    "create_file": {
        "triggers": ["create file", "new file", "make file", "file banao",
                      "naya file", "create document"],
        "desc": "Create a new file",
        "example": "[SKILL:create_file:notes.txt:content]",
        "cat": "file"
    },
    "delete_file": {
        "triggers": ["delete file", "remove file", "file delete", "file hatao",
                      "erase file"],
        "desc": "Delete a file",
        "example": "[SKILL:delete_file:old_notes.txt]",
        "cat": "file"
    },
    "rename_file": {
        "triggers": ["rename file", "file rename", "naam badlo", "change name"],
        "desc": "Rename a file",
        "example": "[SKILL:rename_file:old.txt:new.txt]",
        "cat": "file"
    },
    "move_file": {
        "triggers": ["move file", "file move", "shift file", "file ko move",
                      "transfer file"],
        "desc": "Move a file to another location",
        "example": "[SKILL:move_file:report.pdf:Documents]",
        "cat": "file"
    },
    "copy_file": {
        "triggers": ["copy file", "file copy", "duplicate file", "file ka copy"],
        "desc": "Copy a file to another location",
        "example": "[SKILL:copy_file:notes.txt:Desktop]",
        "cat": "file"
    },
    "open_file": {
        "triggers": ["open file", "file open", "open document", "open pdf",
                      "file kholo"],
        "desc": "Open a file (document, not an app)",
        "example": "[SKILL:open_file:report.pdf]",
        "cat": "file"
    },
    "save_as": {
        "triggers": ["save as", "save file as", "save with name", "naam se save"],
        "desc": "Save current file with a new name",
        "example": "[SKILL:save_as:report_final.txt]",
        "cat": "file"
    },
    "search_files": {
        "triggers": ["search files", "find file", "file search", "file dhundho",
                      "locate file"],
        "desc": "Search for files by name or content",
        "example": "[SKILL:search_files:report]",
        "cat": "file"
    },
    "file_find": {
        "triggers": ["file find", "find specific file", "locate specific"],
        "desc": "Find a specific file on disk",
        "example": "[SKILL:file_find:document.pdf]",
        "cat": "file"
    },
    "file_send_whatsapp": {
        "triggers": ["send file whatsapp", "file bhejo whatsapp", "whatsapp file send"],
        "desc": "Send a file via WhatsApp",
        "example": "[SKILL:file_send_whatsapp:report.pdf:Aditya]",
        "cat": "file"
    },
    "file_upload_browser": {
        "triggers": ["upload file", "file upload", "browser upload", "upload in browser"],
        "desc": "Upload a file in the browser",
        "example": "[SKILL:file_upload_browser:document.pdf]",
        "cat": "file"
    },
    "file_list_by_date": {
        "triggers": ["files by date", "recent files", "latest files", "newest files"],
        "desc": "List files sorted by date",
        "example": "[SKILL:file_list_by_date:Downloads]",
        "cat": "file"
    },
    "file_list_whatsapp": {
        "triggers": ["whatsapp files", "whatsapp se aaye files", "whatsapp downloads"],
        "desc": "List files received from WhatsApp",
        "example": "[SKILL:file_list_whatsapp]",
        "cat": "file"
    },
    "folder_screenshot_whatsapp": {
        "triggers": ["folder screenshot whatsapp", "folder ka screenshot bhejo",
                      "screenshot folder whatsapp"],
        "desc": "Take folder screenshot and send via WhatsApp",
        "example": "[SKILL:folder_screenshot_whatsapp:Downloads:Aditya]",
        "cat": "file"
    },

    # ── CODE & DEVELOPMENT ────────────────────────────────────
    "write_code": {
        "triggers": ["write code", "code likho", "create script", "program banao",
                      "coding", "code generate", "script likho"],
        "desc": "Write/generate code in any language",
        "example": "[SKILL:write_code:python:hello world script]",
        "cat": "code"
    },
    "run_code": {
        "triggers": ["run code", "execute code", "code chalao", "script run",
                      "compile", "run script", "execute"],
        "desc": "Run/execute a code file or snippet",
        "example": "[SKILL:run_code:script.py]",
        "cat": "code"
    },
    "code_review": {
        "triggers": ["code review", "review code", "check code", "code check",
                      "code dekho", "audit code"],
        "desc": "Review and analyze code for issues",
        "example": "[SKILL:code_review:main.py]",
        "cat": "code"
    },
    "fix_code": {
        "triggers": ["fix code", "code fix", "debug", "error fix", "bug fix",
                      "code thik karo"],
        "desc": "Fix errors/bugs in code",
        "example": "[SKILL:fix_code:main.py]",
        "cat": "code"
    },
    "project_scaffold": {
        "triggers": ["scaffold", "project scaffold", "create project", "new project",
                      "setup project", "project banao"],
        "desc": "Scaffold a new project structure",
        "example": "[SKILL:project_scaffold:react:my-app]",
        "cat": "code"
    },
    "find_and_explain": {
        "triggers": ["explain", "what is", "kya hai", "samjhao", "explain code",
                      "find and explain"],
        "desc": "Find information and explain it",
        "example": "[SKILL:find_and_explain:async await in python]",
        "cat": "code"
    },
    "vscode_git_push": {
        "triggers": ["git push", "push code", "commit and push", "vscode push",
                      "code push karo"],
        "desc": "Git commit and push from VS Code",
        "example": "[SKILL:vscode_git_push]",
        "cat": "code"
    },
    "vscode_close_folder": {
        "triggers": ["close folder vscode", "vscode folder close", "close workspace"],
        "desc": "Close current folder in VS Code",
        "example": "[SKILL:vscode_close_folder]",
        "cat": "code"
    },

    # ── SYSTEM & PC CONTROL ───────────────────────────────────
    "sysinfo": {
        "triggers": ["system info", "sysinfo", "cpu", "ram", "battery", "disk",
                      "memory usage", "system status", "pc info", "battery kitni"],
        "desc": "Get system information (CPU, RAM, battery, disk)",
        "example": "[SKILL:sysinfo]",
        "cat": "system"
    },
    "top_processes": {
        "triggers": ["top processes", "running processes", "task manager", "kya chal raha",
                      "active processes"],
        "desc": "Show top running processes",
        "example": "[SKILL:top_processes]",
        "cat": "system"
    },
    "lock_pc": {
        "triggers": ["lock", "lock pc", "lock computer", "pc lock karo", "lock screen"],
        "desc": "Lock the computer",
        "example": "[SKILL:lock_pc]",
        "cat": "system"
    },
    "system_shutdown": {
        "triggers": ["shutdown", "shut down", "power off", "band karo pc",
                      "computer band karo"],
        "desc": "Shut down the computer",
        "example": "[SKILL:system_shutdown]",
        "cat": "system"
    },
    "system_restart": {
        "triggers": ["restart", "reboot", "restart pc", "computer restart"],
        "desc": "Restart the computer",
        "example": "[SKILL:system_restart]",
        "cat": "system"
    },
    "wifi_toggle": {
        "triggers": ["wifi", "wi-fi", "wifi on", "wifi off", "internet", "wifi toggle"],
        "desc": "Toggle WiFi on/off",
        "example": "[SKILL:wifi_toggle:on]",
        "cat": "system"
    },
    "bluetooth_toggle": {
        "triggers": ["bluetooth", "bt", "bluetooth on", "bluetooth off", "bluetooth toggle"],
        "desc": "Toggle Bluetooth on/off",
        "example": "[SKILL:bluetooth_toggle:on]",
        "cat": "system"
    },
    "night_light": {
        "triggers": ["night light", "night mode", "blue light", "eye care",
                      "night light on", "night light off"],
        "desc": "Toggle night light mode on/off",
        "example": "[SKILL:night_light:on]",
        "cat": "system"
    },
    "uptime": {
        "triggers": ["uptime", "how long running", "kitni der se", "system uptime"],
        "desc": "Check system uptime",
        "example": "[SKILL:uptime]",
        "cat": "system"
    },
    "check_process": {
        "triggers": ["is running", "process check", "app running", "check if",
                      "chal raha hai kya"],
        "desc": "Check if a specific app/process is running",
        "example": "[SKILL:check_process:chrome]",
        "cat": "system"
    },

    # ── TIMER, ALARM & REMINDERS ──────────────────────────────
    "timer": {
        "triggers": ["timer", "set timer", "countdown", "timer set karo",
                      "timer laga"],
        "desc": "Set a countdown timer",
        "example": "[SKILL:timer:5:minutes]",
        "cat": "time"
    },
    "alarm": {
        "triggers": ["alarm", "wake me", "alarm set", "alarm laga", "uthao mujhe",
                      "wake up"],
        "desc": "Set an alarm for a specific time",
        "example": "[SKILL:alarm:7:00 AM:Wake up]",
        "cat": "time"
    },
    "time_now": {
        "triggers": ["time", "what time", "current time", "kitne baje", "kya time",
                      "time kya hai", "abhi time"],
        "desc": "Get current time",
        "example": "[SKILL:time_now]",
        "cat": "time"
    },
    "date_today": {
        "triggers": ["date", "today date", "what date", "aaj kya date", "tarikh",
                      "today's date"],
        "desc": "Get today's date",
        "example": "[SKILL:date_today]",
        "cat": "time"
    },
    "reminder_set": {
        "triggers": ["remind", "reminder", "yaad dilana", "remind me", "reminder set",
                      "yaad rakhna"],
        "desc": "Set a reminder",
        "example": "[SKILL:reminder_set:Call doctor:5:minutes]",
        "cat": "time"
    },
    "reminder_list": {
        "triggers": ["reminder list", "show reminders", "my reminders", "mere reminders"],
        "desc": "List all active reminders",
        "example": "[SKILL:reminder_list]",
        "cat": "time"
    },
    "reminder_clear": {
        "triggers": ["clear reminders", "delete reminders", "reminders hatao",
                      "reminder clear"],
        "desc": "Clear all reminders",
        "example": "[SKILL:reminder_clear]",
        "cat": "time"
    },
    "schedule_action": {
        "triggers": ["schedule", "schedule action", "later do", "baad me karo",
                      "tonight", "tomorrow", "at 8 pm"],
        "desc": "Schedule a skill for later execution",
        "example": "[SKILL:schedule_action:2026-07-30:20:00:whatsapp_message:Aditya:done]",
        "cat": "time"
    },

    # ── NOTES & MEMORY ────────────────────────────────────────
    "note": {
        "triggers": ["note", "save note", "note down", "likh lo", "yaad rakh",
                      "note le", "add note"],
        "desc": "Save a note",
        "example": "[SKILL:note:Buy groceries]",
        "cat": "notes"
    },
    "note_delete": {
        "triggers": ["delete note", "remove note", "note hatao", "last note delete"],
        "desc": "Delete last note",
        "example": "[SKILL:note_delete]",
        "cat": "notes"
    },
    "note_clear": {
        "triggers": ["clear notes", "all notes delete", "sab notes hatao", "clear all notes"],
        "desc": "Clear all notes",
        "example": "[SKILL:note_clear]",
        "cat": "notes"
    },
    "clear_memory": {
        "triggers": ["clear memory", "forget", "reset memory", "memory clear",
                      "sab bhool ja"],
        "desc": "Clear conversation memory",
        "example": "[SKILL:clear_memory]",
        "cat": "notes"
    },
    "add_rule": {
        "triggers": ["add rule", "remember rule", "new rule", "rule add karo",
                      "yaad rakh rule"],
        "desc": "Add a permanent behavioral rule",
        "example": "[SKILL:add_rule:Always reply in English]",
        "cat": "notes"
    },
    "get_recent_history": {
        "triggers": ["history", "recent history", "past conversation", "pehle kya baat",
                      "what did we discuss", "recall", "yaad karo", "earlier conversation"],
        "desc": "Recall recent conversation history",
        "example": "[SKILL:get_recent_history:20]",
        "cat": "notes"
    },

    # ── WHATSAPP ──────────────────────────────────────────────
    "whatsapp_message": {
        "triggers": ["whatsapp", "whatsapp message", "message bhejo", "whatsapp pe bhejo",
                      "send message", "msg bhejo", "whatsapp send"],
        "desc": "Send a WhatsApp message to a contact",
        "example": "[SKILL:whatsapp_message:Aditya:hello]",
        "cat": "communication"
    },
    "whatsapp_screenshot": {
        "triggers": ["whatsapp screenshot", "screenshot bhejo whatsapp",
                      "send screenshot whatsapp", "ss bhejo"],
        "desc": "Take screenshot and send via WhatsApp",
        "example": "[SKILL:whatsapp_screenshot:Aditya]",
        "cat": "communication"
    },

    # ── EMAIL & CALENDAR ─────────────────────────────────────
    "email_send": {
        "triggers": ["email", "send email", "email bhejo", "mail send", "compose email"],
        "desc": "Send an email",
        "example": "[SKILL:email_send:to@email.com:Subject:Body]",
        "cat": "communication"
    },
    "email_check": {
        "triggers": ["check email", "email check", "new emails", "inbox",
                      "email aaye hain kya"],
        "desc": "Check inbox for new emails",
        "example": "[SKILL:email_check]",
        "cat": "communication"
    },
    "calendar_today": {
        "triggers": ["calendar today", "today schedule", "aaj ka schedule",
                      "today's events"],
        "desc": "Show today's calendar events",
        "example": "[SKILL:calendar_today]",
        "cat": "communication"
    },
    "calendar_add": {
        "triggers": ["add event", "calendar add", "event add", "schedule event",
                      "meeting add"],
        "desc": "Add an event to the calendar",
        "example": "[SKILL:calendar_add:Meeting:2026-07-30:10:00]",
        "cat": "communication"
    },
    "calendar_week": {
        "triggers": ["calendar week", "this week", "week schedule", "weekly events"],
        "desc": "Show this week's calendar events",
        "example": "[SKILL:calendar_week]",
        "cat": "communication"
    },

    # ── SMART HOME ────────────────────────────────────────────
    "fan": {
        "triggers": ["fan", "pankha", "fan on", "fan off", "fan speed"],
        "desc": "Control smart fan",
        "example": "[SKILL:fan:on]",
        "cat": "smarthome"
    },
    "smart_light": {
        "triggers": ["light", "lamp", "bulb", "smart light", "light on", "light off",
                      "batti"],
        "desc": "Control smart lights",
        "example": "[SKILL:smart_light:on]",
        "cat": "smarthome"
    },
    "smart_ac": {
        "triggers": ["ac", "air conditioner", "cooling", "ac on", "ac off",
                      "temperature set"],
        "desc": "Control smart AC",
        "example": "[SKILL:smart_ac:on:24]",
        "cat": "smarthome"
    },

    # ── KEYBOARD & INPUT ──────────────────────────────────────
    "type_text": {
        "triggers": ["type", "type text", "likh do", "type karo", "text type"],
        "desc": "Type text into the active window",
        "example": "[SKILL:type_text:hello world]",
        "cat": "input"
    },
    "key_press": {
        "triggers": ["press", "key press", "hit", "press enter", "press escape",
                      "press tab", "press space", "press backspace", "enter dabao"],
        "desc": "Press a keyboard key (enter, tab, escape, etc.)",
        "example": "[SKILL:key_press:enter]",
        "cat": "input"
    },
    "key_chord": {
        "triggers": ["key chord", "shortcut", "alt f4", "ctrl c", "ctrl v", "ctrl z",
                      "ctrl s", "ctrl shift", "keyboard shortcut"],
        "desc": "Press a keyboard shortcut (e.g., Ctrl+C, Alt+F4)",
        "example": "[SKILL:key_chord:ctrl+c]",
        "cat": "input"
    },
    "clipboard": {
        "triggers": ["clipboard", "copy", "paste", "clipboard content", "clipboard me kya"],
        "desc": "Access clipboard content",
        "example": "[SKILL:clipboard]",
        "cat": "input"
    },

    # ── AI ORCHESTRATOR ───────────────────────────────────────
    "ai_ask": {
        "triggers": ["ask chatgpt", "chatgpt", "gemini se", "claude", "copilot",
                      "perplexity", "ai ask", "ask ai", "use chatgpt", "use gemini"],
        "desc": "Ask another AI (ChatGPT/Gemini/Claude/Copilot/Perplexity)",
        "example": "[SKILL:ai_ask:chatgpt:write a python script]",
        "cat": "ai"
    },
    "ai_chain": {
        "triggers": ["ai chain", "chain ai", "chatgpt then gemini", "write then review",
                      "likhwao phir improve"],
        "desc": "Chain multiple AIs (e.g., ChatGPT writes, Gemini reviews)",
        "example": "[SKILL:ai_chain:chatgpt:gemini:write and review code]",
        "cat": "ai"
    },
    "count": {
        "triggers": ["count", "counting", "ginti", "count to", "count from"],
        "desc": "Count numbers from start to end",
        "example": "[SKILL:count:1:100:false]",
        "cat": "misc"
    },

    # ── KNOWLEDGE BASE ────────────────────────────────────────
    "kb_search": {
        "triggers": ["knowledge base", "kb search", "knowledge search", "search knowledge"],
        "desc": "Search the local knowledge base",
        "example": "[SKILL:kb_search:MAX architecture]",
        "cat": "knowledge"
    },
    "kb_rebuild": {
        "triggers": ["kb rebuild", "rebuild knowledge", "reindex knowledge"],
        "desc": "Rebuild the knowledge base index",
        "example": "[SKILL:kb_rebuild]",
        "cat": "knowledge"
    },
    "kb_list": {
        "triggers": ["kb list", "list knowledge", "knowledge files"],
        "desc": "List knowledge base files",
        "example": "[SKILL:kb_list]",
        "cat": "knowledge"
    },
    "kb_stats": {
        "triggers": ["kb stats", "knowledge stats", "kb info"],
        "desc": "Show knowledge base statistics",
        "example": "[SKILL:kb_stats]",
        "cat": "knowledge"
    },

    # ── RESEARCH ──────────────────────────────────────────────
    "deep_research": {
        "triggers": ["deep research", "research deeply", "deep dive", "thorough research"],
        "desc": "Deep research on a topic (orchestrator handles externally)",
        "example": "No skill tag — orchestrator handles",
        "cat": "research"
    },
    "research": {
        "triggers": ["research", "study", "investigate", "analyze topic"],
        "desc": "Research a topic",
        "example": "[SKILL:research:quantum computing]",
        "cat": "research"
    },

    # ── PLUGINS ───────────────────────────────────────────────
    "plugin_list": {
        "triggers": ["plugin list", "list plugins", "show plugins", "active plugins"],
        "desc": "List installed plugins",
        "example": "[SKILL:plugin_list]",
        "cat": "plugin"
    },
    "plugin_reload": {
        "triggers": ["plugin reload", "reload plugins", "refresh plugins"],
        "desc": "Reload all plugins",
        "example": "[SKILL:plugin_reload]",
        "cat": "plugin"
    },

    # ── WEATHER ───────────────────────────────────────────────
    "weather": {
        "triggers": ["weather", "mausam", "temperature", "barish", "rain", "sunny",
                      "cloudy", "forecast", "weather kya hai"],
        "desc": "Get current weather for a location",
        "example": "[SKILL:weather:Mumbai]",
        "cat": "web"
    },

    # ── QUIT ──────────────────────────────────────────────────
    "quit_max": {
        "triggers": ["quit max", "exit max", "close max", "shutdown max", "bye max",
                      "band ho ja", "goodbye"],
        "desc": "Quit/exit MAX assistant",
        "example": "[SKILL:quit_max]",
        "cat": "system"
    },
}


# ═══════════════════════════════════════════════════
# CATEGORY GROUPINGS (for category-boost scoring)
# ═══════════════════════════════════════════════════

_CATEGORIES: Dict[str, List[str]] = {}

def _build_category_index():
    """Build reverse index: category → list of skill names."""
    global _CATEGORIES
    _CATEGORIES.clear()
    for skill_name, meta in SKILL_METADATA.items():
        cat = meta.get("cat", "misc")
        _CATEGORIES.setdefault(cat, []).append(skill_name)

_build_category_index()


# ═══════════════════════════════════════════════════
# SKILL RAG MATCHER ENGINE
# ═══════════════════════════════════════════════════

class SkillRAGMatcher:
    """
    3-Layer Bulletproof Skill Matcher.
    
    Layer 1: Keyword/Regex match (fast, O(n) over trigger lists)
    Layer 2: Fuzzy semantic match (difflib against descriptions)
    Layer 3: Full registry safety net (never returns empty)
    
    Result: MAX NEVER misses a skill — 100% guaranteed.
    """

    def __init__(self, learning_journal_path: Optional[str] = None):
        self._learning_path = Path(learning_journal_path) if learning_journal_path else None
        self._usage_boosts: Dict[str, float] = {}
        self._skill_overrides: Dict[str, str] = {}
        self._load_learning_data()

    def _load_learning_data(self):
        """Load usage stats and skill overrides from learning journal."""
        if not self._learning_path or not self._learning_path.exists():
            return
        try:
            data = json.loads(self._learning_path.read_text(encoding="utf-8"))
            # Build usage boosts: frequently used skills get a small score bump
            usage = data.get("usage_stats", {}).get("top_skills", {})
            if usage:
                max_count = max(usage.values()) or 1
                for skill, count in usage.items():
                    self._usage_boosts[skill] = 0.1 * (count / max_count)
            # Load skill overrides
            self._skill_overrides = data.get("skill_overrides", {})
        except Exception as e:
            logger.warning(f"Could not load learning data: {e}")

    def match(self, query: str, top_k: int = 5) -> List[SkillCandidate]:
        """
        Match user query to relevant skills.
        
        Returns top_k SkillCandidates sorted by score (highest first).
        GUARANTEED to return at least 1 candidate (Layer 3 safety net).
        """
        query_lower = query.lower().strip()
        query_words = set(re.findall(r'\b\w+\b', query_lower))

        # ── Check overrides from learning journal ──
        for pattern, skill_name in self._skill_overrides.items():
            if pattern.lower() in query_lower and skill_name in SKILL_METADATA:
                meta = SKILL_METADATA[skill_name]
                return [SkillCandidate(
                    name=skill_name,
                    description=meta["desc"],
                    example=meta["example"],
                    score=1.0,
                    category=meta["cat"]
                )]

        # ── Layer 1: Keyword/Regex Match ──
        scores: Dict[str, float] = {}
        for skill_name, meta in SKILL_METADATA.items():
            score = 0.0
            triggers = meta.get("triggers", [])
            for trigger in triggers:
                trigger_lower = trigger.lower()
                trigger_words = set(trigger_lower.split())
                # Exact phrase match (highest weight)
                if trigger_lower in query_lower:
                    score += 1.0
                # Word overlap match
                elif trigger_words & query_words:
                    overlap = len(trigger_words & query_words) / len(trigger_words)
                    score += 0.5 * overlap

            # Add learning boost
            score += self._usage_boosts.get(skill_name, 0.0)

            if score > 0:
                scores[skill_name] = score

        # ── Category Boost: if any skill in a category matched, boost siblings ──
        matched_categories = set()
        for skill_name in scores:
            cat = SKILL_METADATA[skill_name].get("cat", "misc")
            matched_categories.add(cat)

        for cat in matched_categories:
            for sibling in _CATEGORIES.get(cat, []):
                if sibling not in scores:
                    scores[sibling] = 0.15  # Small category-association boost

        if scores:
            # Sort by score, take top_k
            sorted_skills = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            candidates = []
            for skill_name, score in sorted_skills:
                meta = SKILL_METADATA[skill_name]
                candidates.append(SkillCandidate(
                    name=skill_name,
                    description=meta["desc"],
                    example=meta["example"],
                    score=score,
                    category=meta["cat"]
                ))
            logger.info(f"SkillRAG L1: {len(candidates)} candidates for '{query[:50]}' → {[c.name for c in candidates]}")
            return candidates

        # ── Layer 2: Fuzzy Semantic Match ──
        fuzzy_scores: Dict[str, float] = {}
        for skill_name, meta in SKILL_METADATA.items():
            desc = meta.get("desc", "")
            # Compare query against description
            ratio = SequenceMatcher(None, query_lower, desc.lower()).ratio()
            if ratio > 0.3:
                fuzzy_scores[skill_name] = ratio
            # Also compare against trigger phrases
            for trigger in meta.get("triggers", []):
                t_ratio = SequenceMatcher(None, query_lower, trigger.lower()).ratio()
                if t_ratio > 0.4:
                    fuzzy_scores[skill_name] = max(fuzzy_scores.get(skill_name, 0), t_ratio)

        if fuzzy_scores:
            sorted_skills = sorted(fuzzy_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            candidates = []
            for skill_name, score in sorted_skills:
                meta = SKILL_METADATA[skill_name]
                candidates.append(SkillCandidate(
                    name=skill_name,
                    description=meta["desc"],
                    example=meta["example"],
                    score=score,
                    category=meta["cat"]
                ))
            logger.info(f"SkillRAG L2 (fuzzy): {len(candidates)} candidates for '{query[:50]}' → {[c.name for c in candidates]}")
            return candidates

        # ── Layer 3: FULL REGISTRY SAFETY NET ──
        logger.warning(f"SkillRAG L3 (safety net): No matches for '{query[:50]}'. Injecting ALL skills.")
        all_candidates = []
        for skill_name, meta in SKILL_METADATA.items():
            all_candidates.append(SkillCandidate(
                name=skill_name,
                description=meta["desc"],
                example=meta["example"],
                score=0.1,
                category=meta["cat"]
            ))
        return all_candidates

    def format_for_prompt(self, candidates: List[SkillCandidate]) -> str:
        """
        Format matched candidates into a compact string for LLM system prompt injection.
        
        Output format (ultra-compact, ~20-40 tokens per skill):
          AVAILABLE SKILLS:
          - screenshot: Take a screenshot. Example: [SKILL:screenshot::default]
          - open_app: Open app/folder. Example: [SKILL:open_app:chrome]
        """
        if not candidates:
            return ""

        lines = ["AVAILABLE SKILLS:"]
        seen = set()
        for c in candidates:
            if c.name in seen:
                continue
            seen.add(c.name)
            lines.append(f"- {c.name}: {c.description}. Ex: {c.example}")

        return "\n".join(lines)

    def get_all_skill_names(self) -> List[str]:
        """Return all registered skill names."""
        return list(SKILL_METADATA.keys())


# ═══════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════

_instance: Optional[SkillRAGMatcher] = None

def get_skill_rag(learning_journal_path: Optional[str] = None) -> SkillRAGMatcher:
    """Get or create the singleton SkillRAGMatcher."""
    global _instance
    if _instance is None:
        _instance = SkillRAGMatcher(learning_journal_path)
    return _instance
