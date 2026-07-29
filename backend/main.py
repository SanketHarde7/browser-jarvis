# Path: backend/main.py
# Use: Main entry point starting the backend API server.
"""
main.py — MAX v4.2
Backend: FastAPI + WebSocket + REST endpoints.
Added: /api/wake-check endpoint for wake word detection.
"""
import os
import sys
import logging

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# Safe excepthook to silence WatchFiles / KeyboardInterrupt reload teardown errors
_orig_excepthook = sys.excepthook
def _safe_excepthook(type_, value_, traceback_):
    if type_ is KeyboardInterrupt or "WatchFiles" in str(type_):
        return
    if _orig_excepthook:
        try:
            _orig_excepthook(type_, value_, traceback_)
        except Exception:
            pass

sys.excepthook = _safe_excepthook

import base64
import re
import uuid
import time as _time_module
import hmac
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# Global state for multi-device voice concurrency lock
_last_voice_ts = 0.0
_last_voice_text = ""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Form, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ── Ensure project root in path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import config
from agent_core import get_agent, register_websocket
from modules.stt import transcribe_audio, transcribe_file, transcribe_wake_word, is_valid_transcript
from modules.tts import generate_tts
from modules.llm import get_greeting
from modules.skills import get_skills_engine
from modules.memory import get_memory_manager
from modules.email_agent import get_email_agent
from modules.calendar_agent import get_calendar_agent
from modules.browser_agent import get_browser_agent
from modules.smarthome_agent import get_smarthome_agent
from modules.plugin_loader import get_plugin_loader
from modules.knowledge_indexer import get_knowledge_indexer
from modules.knowledge_base import get_knowledge_base
import threading as _threading
import asyncio
from modules.health_buddy import HealthBuddy

# ── Global WebSocket & Health Buddy References ──
active_websocket: Optional[WebSocket] = None
main_loop: Optional[asyncio.AbstractEventLoop] = None
health_buddy_instance: Optional[HealthBuddy] = None

# ═══════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO if not config.DEBUG else logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("MAX.API")

def send_health_buddy_alert(payload):
    from agent_core import get_active_websockets, get_main_loop
    ws_list = get_active_websockets()
    loop = get_main_loop()
    if ws_list and loop:
        async def _send():
            for ws in list(ws_list):
                try:
                    await ws.send_json(payload)
                except Exception as e:
                    logger.warning(f"Failed to stream health alert: {e}")
        asyncio.run_coroutine_threadsafe(_send(), loop)

# ═══════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════

app = FastAPI(
    title="MAX API",
    description="the user's AI Assistant Backend",
    version="4.2.0",
)

# ── CORS — locked to known local origins only ──
_ALLOWED_ORIGINS = [
    "http://localhost",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
    "tauri://localhost",
    "https://tauri.localhost",
    "capacitor://localhost",
    "http://localhost:8100",
]
# Add phone/LAN IPs dynamically from env
_extra_origins = os.getenv("CORS_EXTRA_ORIGINS", "")
if _extra_origins:
    _ALLOWED_ORIGINS.extend([o.strip() for o in _extra_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════
# SECURITY — Rate Limiting, Auth, Connection Tracking
# ═══════════════════════════════════════════════════

# Track failed auth attempts per IP: {ip: [timestamp, ...]}
_auth_failures: Dict[str, list] = defaultdict(list)
# Track banned IPs: {ip: ban_expiry_timestamp}
_banned_ips: Dict[str, float] = {}
# Active WebSocket connection count
_active_ws_count = 0


def _is_ip_banned(ip: str) -> bool:
    """Check if an IP is currently banned from auth failures."""
    if ip in _banned_ips:
        if _time_module.time() < _banned_ips[ip]:
            return True
        else:
            del _banned_ips[ip]  # Ban expired
    return False


def _record_auth_failure(ip: str):
    """Record a failed auth attempt and ban IP if threshold exceeded."""
    now = _time_module.time()
    window = config.AUTH_RATE_LIMIT_WINDOW
    # Prune old entries outside the window
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < window]
    _auth_failures[ip].append(now)
    if len(_auth_failures[ip]) >= config.AUTH_RATE_LIMIT_MAX:
        _banned_ips[ip] = now + config.AUTH_BAN_DURATION
        logger.warning(f"🔒 IP {ip} BANNED for {config.AUTH_BAN_DURATION}s after {len(_auth_failures[ip])} failed auth attempts")
        _auth_failures[ip].clear()


def _constant_time_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison to prevent timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())


async def verify_token(authorization: str = Header(None)):
    """
    FastAPI dependency to protect REST endpoints.
    Expects: Authorization: Bearer <token>
    If WS_AUTH_TOKEN is not set, all requests pass (dev mode).
    """
    if not config.WS_AUTH_TOKEN:
        return True  # No token configured = dev mode, allow all
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization format. Use: Bearer <token>")
    if not _constant_time_compare(parts[1], config.WS_AUTH_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid token")
    return True


@app.on_event("startup")
async def _on_startup():
    """
    Runs once when FastAPI server starts.
    1. Starts reminder background daemon (checks every 30s for due reminders)
    2. Auto-indexes .md files from backend/knowledge/ into ChromaDB
    """
    # 1. Reminder daemon
    try:
        from modules.reminder_agent import start_reminder_daemon
        start_reminder_daemon(config)
        logger.info("Reminder daemon started")
    except Exception as e:
        logger.warning(f"Reminder daemon failed: {e}")

    # 2. Knowledge base auto-index (runs in background thread, non-blocking)
    def _build_kb():
        try:
            from modules.knowledge_base import auto_index_on_startup
            auto_index_on_startup(config)
        except Exception as e:
            logger.warning(f"KB auto-index: {e}")

    _threading.Thread(target=_build_kb, daemon=True, name="MAX-KB-Init").start()

    # 3. Health Buddy daemon (DISABLED — user reported unsolicited voice alerts)
    # To re-enable, uncomment the lines below:
    # try:
    #     global health_buddy_instance
    #     health_buddy_instance = HealthBuddy(send_health_buddy_alert)
    #     health_buddy_instance.start()
    #     logger.info("Health Buddy started")
    # except Exception as e:
    #     logger.warning(f"Health Buddy start failed: {e}")


zeroconf_instance = None

@app.on_event("shutdown")
async def _on_shutdown():
    global health_buddy_instance, zeroconf_instance
    if health_buddy_instance:
        health_buddy_instance.stop()
        logger.info("Health Buddy stopped")
    if zeroconf_instance:
        try:
            zeroconf_instance.close()
            logger.info("mDNS zeroconf stopped")
        except Exception:
            pass


@app.on_event("startup")
async def startup_event():
    try:
        get_knowledge_indexer(config).refresh_if_needed()
        logger.info("Knowledge index ready.")
    except Exception as e:
        logger.warning(f"Knowledge index startup failed: {e}")
        
    try:
        from zeroconf import ServiceInfo, Zeroconf
        import socket
        global zeroconf_instance
        
        # We need the local IP address for the service
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "127.0.0.1"
        finally:
            s.close()
            
        info = ServiceInfo(
            "_http._tcp.local.",
            "max-server._http._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=8000,
            properties={"version": "4.2.0"},
            server="max-server.local."
        )
        zeroconf_instance = Zeroconf()
        zeroconf_instance.register_service(info)
        logger.info(f"mDNS broadcast started: max-server.local -> {local_ip}:8000")
    except Exception as e:
        logger.warning(f"mDNS zeroconf failed to start: {e}")


# ═══════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════

class TextInput(BaseModel):
    text: str
    tts: bool = True

class VoiceRequest(BaseModel):
    audio: str

class WakeCheckRequest(BaseModel):
    audio: str

class CodeRequest(BaseModel):
    language: str = "python"
    description: str

class RunCodeRequest(BaseModel):
    filepath: str

class ReviewCodeRequest(BaseModel):
    filepath: str

class FixCodeRequest(BaseModel):
    filepath: str
    issue: str

class ProjectScaffoldRequest(BaseModel):
    project_type: str
    project_name: str

class WeatherRequest(BaseModel):
    city: str = "auto"

class VolumeRequest(BaseModel):
    action: str = "up"
    value: int = 10

class OpenAppRequest(BaseModel):
    app_name: str

class OpenUrlRequest(BaseModel):
    url: str

class WhatsAppRequest(BaseModel):
    contact: str
    message: str

class TypeTextRequest(BaseModel):
    text: str

class TimerRequest(BaseModel):
    seconds: int = 60
    label: str = "Timer"

class ShutdownRequest(BaseModel):
    delay: int = 30

class RestartRequest(BaseModel):
    delay: int = 30

class EmailSendRequest(BaseModel):
    to: str
    subject: str
    body: str

class CalendarAddRequest(BaseModel):
    title: str
    date: str
    time: str = ""

class BrowserOpenRequest(BaseModel):
    url: str

class BrowserActionRequest(BaseModel):
    selector: str
    text: str = ""

class BrowserScrapeRequest(BaseModel):
    url: str
    query: str

class FanRequest(BaseModel):
    action: str

class LightRequest(BaseModel):
    action: str

class ACRequest(BaseModel):
    action: str
    value: str = ""

class BrightnessRequest(BaseModel):
    action: str
    value: int = 10

class ClipboardRequest(BaseModel):
    action: str
    text: str = ""

# ═══════════════════════════════════════════════════
# WAKE WORD — NEW ENDPOINT
# ═══════════════════════════════════════════════════

WAKE_PHRASES = ["hey max", "hello max", "ok max", "max", "hi max", "oye max"]

@app.post("/api/wake-check")
async def wake_check(request: WakeCheckRequest, _auth=Depends(verify_token)):
    """
    Lightweight wake word verification.
    Takes audio, runs STT with auto language detect, checks for wake phrases.
    Returns quickly — designed for frequent background checks.
    """
    try:
        transcript = await transcribe_wake_word(request.audio)
        if not transcript:
            return {"wake_detected": False, "transcript": ""}

        transcript_lower = transcript.lower().strip()
        logger.info(f"Wake check transcript: '{transcript_lower}'")

        # Check if any wake phrase is in the transcript
        detected = any(phrase in transcript_lower for phrase in WAKE_PHRASES)

        return {
            "wake_detected": detected,
            "transcript": transcript_lower
        }

    except Exception as e:
        logger.error(f"Wake check failed: {e}")
        return {"wake_detected": False, "transcript": "", "error": str(e)}


# ═══════════════════════════════════════════════════
# WEBSOCKET — Real-time Voice/Text Chat
# ═══════════════════════════════════════════════════

async def process_voice_request(
    rid: str,
    msg_payload: dict,
    websocket: WebSocket,
    agent: Any,
    skills: Any,
    connection_state: dict
):
    try:
        # Staleness Check (Change 7)
        client_ts = msg_payload.get("timestamp")
        if client_ts:
            import time as _time
            current_time_ms = _time.time() * 1000
            latency_sec = (current_time_ms - client_ts) / 1000.0
            logger.info(f"Voice request {rid} latency: {latency_sec:.2f}s")
            if latency_sec > 60.0:
                logger.warning(f"Voice request {rid} is stale ({latency_sec:.2f}s > 60.0s). Discarding.")
                await websocket.send_json({"event": "stale_discard", "command_id": rid})
                return

        if connection_state["current_request_id"] != rid:
            logger.info(f"Voice task {rid} discarded before starting.")
            return

        audio_data = msg_payload.get("audio", msg_payload.get("data", ""))
        if not audio_data:
            return

        from modules.stt import transcribe_audio
        transcript = await transcribe_audio(audio_data)

        if connection_state["current_request_id"] != rid:
            logger.info(f"Voice task {rid} discarded after transcription.")
            return

        # Filter out empty, error, whisper hallucination transcripts, and single word non-commands
        if not is_valid_transcript(transcript):
            logger.info(f"STT returned invalid or hallucinated transcript: '{transcript}', skipping LLM")
            if connection_state["current_request_id"] == rid:
                await websocket.send_json({"event": "error", "message": "I didn't catch that. Please try again.", "command_id": rid})
            return

        lower_trans = transcript.lower().strip()
        logger.info(f"STT: {transcript}")
        
        req_device = connection_state.get("device", "laptop")
        from agent_core import get_active_device, set_active_device, get_active_websockets, is_device_match
        
        # ── STRICT DEVICE LOCK ──
        # Only process voice from the explicitly active device.
        if not is_device_match(req_device, get_active_device()):
            logger.warning(f"Rejecting voice from {req_device} because active device is {get_active_device()}.")
            return

        if connection_state["current_request_id"] != rid:
            logger.info(f"Voice task {rid} discarded before sending transcript.")
            return

        await websocket.send_json({
            "event": "transcript",
            "text": transcript,
            "command_id": rid
        })

        # Intercept conversational follow-ups like "Haan", "Open", "Kholo", "Yes"
        words_count = len(lower_trans.split())
        follow_up_phrases = {"haan", "open", "kholo", "yes", "khol", "open it", "haan kholo", "khol do", "khol de", "open this", "yup", "yeah", "sure"}
        target_names = {"google", "youtube", "notepad", "chrome", "calc", "calculator", "browser", "vscode", "vs code", "github", "chatgpt", "gemini"}
        is_follow_up = False
        if words_count <= 4 and not any(t in lower_trans for t in target_names):
            if lower_trans in follow_up_phrases:
                is_follow_up = True
            elif any(w in lower_trans for w in ["haan", "yes", "yup", "yeah", "sure"]) and any(w in lower_trans for w in ["open", "khol"]):
                is_follow_up = True
        
        intercepted = False
        if is_follow_up:
            import glob
            import time
            from modules.web_autopilot import CACHE_DIR
            
            # Check both research cache AND code save dir for recently created files
            files = glob.glob(str(CACHE_DIR / "*.*"))
            try:
                code_save_dir = config.CODE_SAVE_DIR
                if code_save_dir.exists():
                    files.extend(glob.glob(str(code_save_dir / "*.*")))
            except Exception:
                pass
            latest_file = max(files, key=os.path.getmtime) if files else None
            
            # Check if latest file exists and was created in the last 5 minutes (300s)
            if latest_file and (time.time() - os.path.getmtime(latest_file) < 300):
                logger.info(f"Intercepted follow-up: Opening latest file: {latest_file}")
                skills._skill_open_app(latest_file)
                if connection_state["current_request_id"] == rid:
                    await websocket.send_json({
                        "event": "response_text",
                        "text": f"Opening file: {os.path.basename(latest_file)}",
                        "command_id": rid
                    })
                intercepted = True
            else:
                # Check for LAST_BOT_BYPASS_URL from web_autopilot
                from modules.web_autopilot import LAST_BOT_BYPASS_URL, clear_last_bot_bypass_url
                if LAST_BOT_BYPASS_URL:
                    logger.info(f"Intercepted follow-up: Opening bot bypass URL: {LAST_BOT_BYPASS_URL}")
                    skills._skill_web_open(LAST_BOT_BYPASS_URL)
                    if connection_state["current_request_id"] == rid:
                        await websocket.send_json({
                            "event": "response_text",
                            "text": "Opening blocked website on screen...",
                            "command_id": rid
                        })
                    clear_last_bot_bypass_url()
                    intercepted = True
                    
        if intercepted:
            # Halt downstream LLM execution to stop continuous voice listening bleeding
            return

        result = await agent.process_text_input(transcript, use_tts=True, input_source="voice")
        
        # ── Check for device switch intent ──
        if result.get("intent") == "device_switch":
            logger.info("Device switch executed. Target device notified directly.")
            return

        # ── Check for reserved listening state commands ──
        if result.get("intent") == "reserved":
            cmd = result.get("skill_used", "").replace("reserved:", "")
            if cmd in ["stop listening", "sunna band karo", "cancel", "abort", "emergency stop"]:
                await websocket.send_json({"event": "stop_continuous_listening", "command_id": rid})
            elif cmd in ["start listening", "sunna shuru karo"]:
                await websocket.send_json({"event": "start_continuous_listening", "command_id": rid})
                
        await websocket.send_json({
            "event": "response_text",
            "text": result.get("response", ""),
            "skill_used": result.get("skill_used"),
            "command_id": rid
        })
        
        tts_path = result.get("tts_path", "")
        logger.info(f"Voice TTS path: '{tts_path}', exists: {bool(tts_path and os.path.exists(tts_path))}")
        if tts_path and os.path.exists(tts_path):
            try:
                file_size = os.path.getsize(tts_path)
                logger.info(f"Voice TTS file size: {file_size} bytes")
                with open(tts_path, "rb") as f:
                    audio_bytes = f.read()
                    encoded_audio = base64.b64encode(audio_bytes).decode('utf-8')
                    logger.info(f"Voice TTS encoded audio length: {len(encoded_audio)} chars")
                    await websocket.send_json({
                        "event": "audio_response",
                        "audio": encoded_audio,
                        "command_id": rid
                    })
                # Clean up temp TTS file
                try:
                    os.remove(tts_path)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Voice TTS Read Error: {e}", exc_info=True)
                if connection_state["current_request_id"] == rid:
                    await websocket.send_json({"event": "audio_response", "command_id": rid})
        else:
            logger.warning(f"Voice TTS MISSING — No audio will be sent for rid={rid}")
            if connection_state["current_request_id"] == rid:
                await websocket.send_json({"event": "audio_response", "command_id": rid})
    except asyncio.CancelledError:
        logger.info(f"Voice task {rid} was asynchronously cancelled.")
        raise
    except Exception as e:
        logger.error(f"Error processing voice request: {e}", exc_info=True)
        try:
            if connection_state["current_request_id"] == rid:
                await websocket.send_json({"event": "error", "message": f"Backend Error: {str(e)}", "command_id": rid})
        except Exception:
            pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None, device: str = "laptop"):
    global _active_ws_count
    client_ip = websocket.client.host if websocket.client else "unknown"

    # 1. Check if IP is banned from too many auth failures
    if _is_ip_banned(client_ip):
        logger.warning(f"🚫 Blocked banned IP {client_ip} from connecting")
        await websocket.close(code=1008)
        return

    # 2. Verify auth token (timing-safe comparison)
    if config.WS_AUTH_TOKEN and not _constant_time_compare(token or "", config.WS_AUTH_TOKEN):
        logger.warning(f"Unauthorized WebSocket connection attempt from {client_ip}")
        _record_auth_failure(client_ip)
        await websocket.close(code=1008)
        return

    # 3. Enforce max concurrent connection limit
    if _active_ws_count >= config.MAX_WS_CONNECTIONS:
        logger.warning(f"Connection limit reached ({config.MAX_WS_CONNECTIONS}). Rejecting {client_ip}")
        await websocket.close(code=1013)  # 1013 = Try Again Later
        return

    _active_ws_count += 1
    await websocket.accept()
    logger.info(f"Client connected: {client_ip} as {device} (active: {_active_ws_count}/{config.MAX_WS_CONNECTIONS})")

    global main_loop
    main_loop = asyncio.get_running_loop()
    
    # Register globals in agent_core so acknowledgements can be sent
    from agent_core import register_websocket, unregister_websocket, set_active_device
    register_websocket(websocket, main_loop, device)

    connection_state = {
        "active_task": None,
        "current_request_id": None,
        "device": device
    }

    agent = get_agent()
    skills = get_skills_engine(config)

    try:
        while True:
            try:
                raw_data = await websocket.receive_text()
                # Input size validation
                if len(raw_data) > config.WS_MAX_PAYLOAD_BYTES:
                    logger.warning(f"Oversized payload from {client_ip}: {len(raw_data)} bytes (max {config.WS_MAX_PAYLOAD_BYTES})")
                    await websocket.send_json({"event": "error", "message": "Payload too large"})
                    continue
                import json as _json
                msg = _json.loads(raw_data)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error(f"WebSocket receive error from {client_ip}: {type(e).__name__}")
                try:
                    await websocket.send_json({"event": "error", "message": "Invalid payload format"})
                except Exception:
                    pass
                continue

            try:
                msg_type = msg.get("type", "text")
                
                # 0. HANDLE CLAIM ACTIVE
                if msg_type == "claim_active":
                    req_device = msg.get("device", device)
                    logger.info(f"Device {req_device} claimed active state explicitly.")
                    set_active_device(req_device)
                    from agent_core import get_active_websockets
                    for ws in get_active_websockets():
                        try:
                            await ws.send_json({"event": "SWITCH_ACTIVE", "device": req_device})
                        except:
                            pass
                    continue



                # 1. HANDLE GREETING EXPLICITLY (Fixes Double Greeting)
                if msg_type == "request_greeting":
                    greeting = await agent.get_greeting()
                    await websocket.send_json({
                        "event": "greeting",
                        "text": greeting
                    })
                    tts_path = await generate_tts(greeting[:3000])
                    if tts_path and os.path.exists(tts_path):
                        try:
                            with open(tts_path, "rb") as f:
                                encoded_audio = base64.b64encode(f.read()).decode('utf-8')
                                await websocket.send_json({
                                    "event": "audio_response",
                                    "audio": encoded_audio
                                })
                        except Exception as e:
                            logger.error(f"Greeting TTS error: {e}")

                # 2. HANDLE TEXT INPUT
                elif msg_type == "text":
                    user_text = msg.get("message", msg.get("text", "")).strip()
                    if not user_text:
                        continue
                    logger.info(f"User Text: {user_text}")

                    # ── Research file follow-up interception (matches voice handler) ──
                    lower_text = user_text.lower().strip()
                    words_count = len(lower_text.split())
                    follow_up_phrases = {"haan", "open", "kholo", "yes", "khol", "open it", "haan kholo", "khol do", "khol de", "open this", "yup", "yeah", "sure"}
                    is_text_follow_up = False
                    if words_count <= 4:
                        if lower_text in follow_up_phrases:
                            is_text_follow_up = True
                        elif any(w in lower_text for w in ["haan", "yes", "yup", "yeah", "sure", "please"]) and any(w in lower_text for w in ["open", "khol"]):
                            is_text_follow_up = True

                    text_intercepted = False
                    if is_text_follow_up:
                        import glob
                        import time as _time
                        from modules.web_autopilot import CACHE_DIR

                        # Check both research cache AND code save dir for recently created files
                        files = glob.glob(str(CACHE_DIR / "*.*"))
                        try:
                            code_save_dir = config.CODE_SAVE_DIR
                            if code_save_dir.exists():
                                files.extend(glob.glob(str(code_save_dir / "*.*")))
                        except Exception:
                            pass
                        latest_file = max(files, key=os.path.getmtime) if files else None

                        if latest_file and (_time.time() - os.path.getmtime(latest_file) < 300):
                            logger.info(f"Text follow-up intercepted: Opening latest file: {latest_file}")
                            await skills._skill_open_app(latest_file)
                            await websocket.send_json({
                                "event": "response_text",
                                "text": f"Opening file: {os.path.basename(latest_file)}"
                            })
                            text_intercepted = True
                        else:
                            from modules.web_autopilot import LAST_BOT_BYPASS_URL, clear_last_bot_bypass_url
                            if LAST_BOT_BYPASS_URL:
                                logger.info(f"Text follow-up intercepted: Opening bot bypass URL: {LAST_BOT_BYPASS_URL}")
                                await skills._skill_web_open(LAST_BOT_BYPASS_URL)
                                await websocket.send_json({
                                    "event": "response_text",
                                    "text": "Opening blocked website on screen..."
                                })
                                clear_last_bot_bypass_url()
                                text_intercepted = True

                    if text_intercepted:
                        continue

                    result = await agent.process_text_input(user_text, use_tts=True, input_source="text")
                    
                    if result.get("intent") == "device_switch":
                        logger.info("Device switch executed via text. Target device notified directly.")
                        continue

                    # ── Check for reserved listening state commands ──
                    if result.get("intent") == "reserved":
                        cmd = result.get("skill_used", "").replace("reserved:", "")
                        if cmd in ["stop listening", "sunna band karo", "cancel", "abort", "emergency stop"]:
                            await websocket.send_json({"event": "stop_continuous_listening"})
                        elif cmd in ["start listening", "sunna shuru karo"]:
                            await websocket.send_json({"event": "start_continuous_listening"})

                    # ── Send response back to frontend ──
                    await websocket.send_json({
                        "event": "response_text",
                        "text": result.get("response", ""),
                        "skill_used": result.get("skill_used"),
                    })

                    tts_path = result.get("tts_path", "")
                    logger.info(f"Text TTS path: '{tts_path}', exists: {bool(tts_path and os.path.exists(tts_path))}")
                    if tts_path and os.path.exists(tts_path):
                        try:
                            file_size = os.path.getsize(tts_path)
                            logger.info(f"Text TTS file size: {file_size} bytes")
                            with open(tts_path, "rb") as f:
                                audio_bytes = f.read()
                                encoded_audio = base64.b64encode(audio_bytes).decode('utf-8')
                                logger.info(f"Text TTS encoded audio length: {len(encoded_audio)} chars")
                                await websocket.send_json({
                                    "event": "audio_response",
                                    "audio": encoded_audio,
                                })
                            # Clean up temp TTS file
                            try:
                                os.remove(tts_path)
                            except Exception:
                                pass
                        except Exception as e:
                            logger.error(f"Text TTS Read Error: {e}", exc_info=True)
                            await websocket.send_json({"event": "audio_response"})
                    else:
                        logger.warning(f"Text TTS MISSING — No audio will be sent")
                        await websocket.send_json({"event": "audio_response"})

                elif msg_type == "voice" or msg_type == "audio":
                    rid = msg.get("command_id") or msg.get("rid") or uuid.uuid4().hex
                    
                    # Store this command ID as the pending one
                    connection_state["current_request_id"] = rid
                    
                    # Wait 300ms to debounce rapid-fire commands
                    await asyncio.sleep(0.3)
                    
                    # If another command arrived during this sleep, discard this old one
                    if connection_state["current_request_id"] != rid:
                        logger.info(f"Discarding debounced command {rid}")
                        continue
                    
                    # Cancel any active running task before starting a new one
                    active_task = connection_state.get("active_task")
                    if active_task and not active_task.done():
                        active_task.cancel()
                        logger.info(f"Canceled active task for command ID: {connection_state.get('current_request_id')}")
                        
                    task = asyncio.create_task(
                        process_voice_request(
                            rid=rid,
                            msg_payload=msg,
                            websocket=websocket,
                            agent=agent,
                            skills=skills,
                            connection_state=connection_state
                        )
                    )
                    connection_state["active_task"] = task
                # 3.5 HANDLE IMAGE INPUT
                elif msg_type == "image":
                    image_data = msg.get("image_data", "")
                    prompt = msg.get("prompt", "What is in this image?")
                    
                    if not image_data:
                        continue
                        
                    logger.info(f"Received Image for analysis. Prompt: {prompt}")
                    
                    # Ensure base64 string doesn't have the data URI scheme attached
                    if "," in image_data:
                        image_data = image_data.split(",")[1]
                        
                    import uuid
                    # Save base64 to a temporary physical file
                    temp_filepath = config.DATA_DIR / f"temp_vision_{uuid.uuid4().hex}.jpg"
                    
                    try:
                        with open(temp_filepath, "wb") as f:
                            f.write(base64.b64decode(image_data))
                            
                        # Tell the UI we are analyzing it
                        from modules.llm import analyze_image_with_prompt
                        vision_response = await analyze_image_with_prompt(str(temp_filepath), prompt)
                        
                        # Send back the AI response
                        await websocket.send_json({
                            "event": "response_text",
                            "text": vision_response,
                            "skill_used": "vision",
                        })
                        
                        # Trigger TTS so MAX speaks the analysis aloud
                        tts_path = await generate_tts(vision_response[:3000])
                        if tts_path and os.path.exists(tts_path):
                            with open(tts_path, "rb") as f:
                                encoded_audio = base64.b64encode(f.read()).decode('utf-8')
                                await websocket.send_json({
                                    "event": "audio_response",
                                    "audio": encoded_audio
                                })
                    except Exception as e:
                        logger.error(f"Vision error: {e}")
                        await websocket.send_json({"event": "error", "message": "Failed to analyze image."})
                    finally:
                        # Ensure temp image is deleted to save space
                        if os.path.exists(temp_filepath):
                            os.remove(temp_filepath)                 
 
                # 4. KEEPALIVE / PING
                elif msg_type == "ping":
                    await websocket.send_json({"event": "pong"})
 
                # 5. CLEAR MEMORY
                elif msg_type == "clear_memory":
                    msg_resp = await agent.clear_memory()
                    await websocket.send_json({"type": "system", "text": msg_resp})
 
                # 5.5 EXECUTE SKILL
                elif msg_type == "execute_skill":
                    skill_name = msg.get("skill")
                    params = msg.get("params", [])
                    if skill_name in skills.skills_registry:
                        try:
                            raw = skills.skills_registry[skill_name](*params)
                            result = await raw if asyncio.iscoroutine(raw) else raw
                            logger.info(f"WebSocket execute_skill {skill_name} result: {result}")
                            await websocket.send_json({
                                "event": "response_text",
                                "text": f"Executed: {result}"
                            })
                        except Exception as e:
                            logger.error(f"WebSocket execute_skill failed: {e}")
                            await websocket.send_json({"event": "error", "message": f"Skill execution failed: {e}"})
 
                # 6. ABORT / KILL SWITCH
                elif msg_type == "abort":
                    logger.info("Client sent abort signal")
                    active_task = connection_state.get("active_task")
                    if active_task and not active_task.done():
                        active_task.cancel()
                        logger.info(f"Canceled active task via abort event for command ID: {connection_state.get('current_request_id')}")
            except Exception as e:
                logger.error(f"WebSocket message error: {type(e).__name__}: {e}", exc_info=True)
                try:
                    await websocket.send_json({"event": "error", "message": "An internal error occurred. Please try again."})
                except Exception:
                    pass
                continue

    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {client_ip}")
    except Exception as e:
        logger.error(f"WebSocket error for {client_ip}: {type(e).__name__}: {e}")
        try:
            await websocket.send_json({"event": "error", "message": "Connection error. Please reconnect."})
        except Exception:
            pass
    finally:
        _active_ws_count = max(0, _active_ws_count - 1)
        logger.info(f"Connection closed for {client_ip} (active: {_active_ws_count}/{config.MAX_WS_CONNECTIONS})")
        try:
            from agent_core import unregister_websocket
            unregister_websocket(websocket)
        except Exception:
            pass
# ═══════════════════════════════════════════════════
# HEALTH & SYSTEM
# ═══════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "4.2.0",
        "timestamp": datetime.now().isoformat(),
        "features": {
            "voice": True,
            "vision": True,
            "code": True,
            "files": True,
            "email": get_email_agent().is_enabled(),
            "calendar": True,
            "browser": True,
            "smarthome": config.IR_BLASTER_ENABLED,
            "plugins": True,
            "clipboard": True,
            "brightness": True,
            "lock": True,
            "wake_word": True,
        },
        "llm_model": config.LLM_MODEL,
        "tts_voice": config.TTS_VOICE,
    }


# ═══════════════════════════════════════════════════
# TTS / STT
# ═══════════════════════════════════════════════════

@app.post("/api/speak")
async def speak(request: TextInput, _auth=Depends(verify_token)):
    tts_path = await generate_tts(request.text[:3000])
    if tts_path and os.path.exists(tts_path):
        with open(tts_path, "rb") as f:
            return {"audio": base64.b64encode(f.read()).decode('utf-8'), "text": request.text}
    return {"error": "TTS generation failed boss."}

@app.post("/api/listen")
async def listen(audio_path: str = "", _auth=Depends(verify_token)):
    if not audio_path:
        return {"error": "Audio file path do boss."}
    transcript = await transcribe_file(audio_path)
    return {"transcript": transcript}

@app.post("/api/voice")
async def voice(request: VoiceRequest, _auth=Depends(verify_token)):
    agent = get_agent()
    transcript = await transcribe_audio(request.audio)
    
    # Filter out empty, error, whisper hallucination transcripts, and single word non-commands
    if not is_valid_transcript(transcript):
        return {
            "transcript": transcript,
            "response": "I didn't catch that. Please try again.",
            "skill_used": None,
        }

    result = await agent.process_text_input(transcript, use_tts=True, input_source="voice")

    response_data = {
        "transcript": transcript,
        "response": result.get("response", ""),
        "skill_used": result.get("skill_used"),
    }

    tts_path = result.get("tts_path", "")
    if tts_path and os.path.exists(tts_path):
        try:
            with open(tts_path, "rb") as f:
                response_data["audio"] = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Voice TTS Read Error: {e}")

    return response_data


# ═══════════════════════════════════════════════════
# FILE MANAGEMENT
# ═══════════════════════════════════════════════════

@app.get("/api/files/search")
async def search_files(query: str = Query(...), _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_search_files(query)
    return {"result": result}

@app.get("/api/files/list")
async def list_files(folder: str = Query("."), _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_list_files(folder)
    return {"result": result}

@app.get("/api/files/read")
async def read_file_api(filepath: str = Query(...), _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_read_file(filepath)
    return {"result": result}


# ═══════════════════════════════════════════════════
# SCREEN / VISION
# ═══════════════════════════════════════════════════

@app.post("/api/screenshot")
async def screenshot(filename: str = "", _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_screenshot(filename)
    return {"result": result}

@app.post("/api/screen/record")
async def screen_record(_auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_screen_record()
    return {"result": result}

@app.post("/api/screen/read")
async def read_screen(window: str = "", _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = await skills._skill_read_screen(window)
    return {"result": result}


# ═══════════════════════════════════════════════════
# PC CONTROL
# ═══════════════════════════════════════════════════

@app.post("/api/volume")
async def volume(request: VolumeRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_volume_control(request.action, str(request.value))
    return {"result": result}

@app.post("/api/open-app")
async def open_app(request: OpenAppRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_open_app(request.app_name)
    return {"result": result}

@app.post("/api/open-url")
async def open_url(request: OpenUrlRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_web_open(request.url)
    return {"result": result}

@app.post("/api/whatsapp")
async def whatsapp(request: WhatsAppRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_whatsapp_message(request.contact, request.message)
    return {"result": result}

@app.post("/api/type-text")
async def type_text(request: TypeTextRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_type_text(request.text)
    return {"result": result}

@app.post("/api/timer")
async def timer(request: TimerRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_timer(str(request.seconds), request.label)
    return {"result": result}

@app.get("/api/weather")
async def weather(city: str = Query("auto"), _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_weather(city)
    return {"result": result}

@app.post("/api/shutdown")
async def shutdown(request: ShutdownRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_system_shutdown(str(request.delay))
    return {"result": result}

@app.post("/api/restart")
async def restart(request: RestartRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_system_restart(str(request.delay))
    return {"result": result}


# ═══════════════════════════════════════════════════
# CODE ENDPOINTS
# ═══════════════════════════════════════════════════

@app.post("/api/generate-code")
async def generate_code(request: CodeRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = await skills._skill_write_code(request.language, request.description)
    return {"result": result}

@app.post("/api/run-code")
async def run_code(request: RunCodeRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = await skills._skill_run_code(request.filepath)
    return {"result": result}

@app.post("/api/review-code")
async def review_code(request: ReviewCodeRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = await skills._skill_code_review(request.filepath)
    return {"result": result}

@app.post("/api/fix-code")
async def fix_code(request: FixCodeRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = await skills._skill_fix_code(request.filepath, request.issue)
    return {"result": result}

@app.post("/api/project-scaffold")
async def project_scaffold(request: ProjectScaffoldRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = await skills._skill_project_scaffold(request.project_type, request.project_name)
    return {"result": result}


# ═══════════════════════════════════════════════════
# EMAIL ENDPOINTS
# ═══════════════════════════════════════════════════

@app.post("/api/email/send")
async def email_send(request: EmailSendRequest, _auth=Depends(verify_token)):
    agent = get_email_agent()
    result = agent.send_email(request.to, request.subject, request.body)
    return {"result": result}

@app.get("/api/email/check")
async def email_check(_auth=Depends(verify_token)):
    agent = get_email_agent()
    result = agent.check_emails()
    return {"result": result}


# ═══════════════════════════════════════════════════
# CALENDAR ENDPOINTS
# ═══════════════════════════════════════════════════

@app.get("/api/calendar/today")
async def calendar_today(_auth=Depends(verify_token)):
    agent = get_calendar_agent()
    result = agent.today()
    return {"result": result}

@app.get("/api/calendar/week")
async def calendar_week(_auth=Depends(verify_token)):
    agent = get_calendar_agent()
    result = agent.week()
    return {"result": result}

@app.post("/api/calendar/add")
async def calendar_add(request: CalendarAddRequest, _auth=Depends(verify_token)):
    agent = get_calendar_agent()
    result = agent.add_event(request.title, request.date, request.time)
    return {"result": result}


# ═══════════════════════════════════════════════════
# BROWSER ENDPOINTS
# ═══════════════════════════════════════════════════

@app.post("/api/browser/open")
async def browser_open(request: BrowserOpenRequest, _auth=Depends(verify_token)):
    agent = get_browser_agent()
    result = agent.open_url(request.url)
    return {"result": result}

@app.post("/api/browser/click")
async def browser_click(request: BrowserActionRequest, _auth=Depends(verify_token)):
    agent = get_browser_agent()
    result = agent.click(request.selector)
    return {"result": result}

@app.post("/api/browser/type")
async def browser_type(request: BrowserActionRequest, _auth=Depends(verify_token)):
    agent = get_browser_agent()
    result = agent.type_text(request.selector, request.text)
    return {"result": result}

@app.post("/api/browser/scrape")
async def browser_scrape(request: BrowserScrapeRequest, _auth=Depends(verify_token)):
    agent = get_browser_agent()
    result = agent.scrape(request.url, request.query)
    return {"result": result}


# ═══════════════════════════════════════════════════
# SMART HOME ENDPOINTS
# ═══════════════════════════════════════════════════

@app.post("/api/smarthome/fan")
async def smarthome_fan(request: FanRequest, _auth=Depends(verify_token)):
    agent = get_smarthome_agent()
    result = agent.fan_control(request.action)
    return {"result": result}

@app.post("/api/smarthome/light")
async def smarthome_light(request: LightRequest, _auth=Depends(verify_token)):
    agent = get_smarthome_agent()
    result = agent.light_control(request.action)
    return {"result": result}

@app.post("/api/smarthome/ac")
async def smarthome_ac(request: ACRequest, _auth=Depends(verify_token)):
    agent = get_smarthome_agent()
    result = agent.ac_control(request.action, request.value)
    return {"result": result}


# ═══════════════════════════════════════════════════
# PC CONTROL — NEW
# ═══════════════════════════════════════════════════

@app.post("/api/pc/brightness")
async def pc_brightness(request: BrightnessRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_brightness(request.action, str(request.value))
    return {"result": result}

@app.post("/api/pc/clipboard")
async def pc_clipboard(request: ClipboardRequest, _auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_clipboard(request.action, request.text)
    return {"result": result}

@app.post("/api/pc/lock")
async def pc_lock(_auth=Depends(verify_token)):
    skills = get_skills_engine(config)
    result = skills._skill_lock_pc()
    return {"result": result}


# ═══════════════════════════════════════════════════
# PLUGIN ENDPOINTS
# ═══════════════════════════════════════════════════

@app.get("/api/plugins/list")
async def plugins_list(_auth=Depends(verify_token)):
    loader = get_plugin_loader()
    result = loader.list_plugins()
    return {"result": result}

@app.post("/api/plugins/reload")
async def plugins_reload(_auth=Depends(verify_token)):
    loader = get_plugin_loader()
    loader.reload()
    skills = get_skills_engine(config)
    skills.skills_registry = skills._register_skills()
    return {"result": "Plugins reload ho gaye."}


# ═══════════════════════════════════════════════════
# TEXT CHAT (non-WebSocket fallback)
# ═══════════════════════════════════════════════════

@app.post("/api/chat")
async def chat(request: TextInput, _auth=Depends(verify_token)):
    agent = get_agent()
    result = await agent.process_text_input(request.text, use_tts=request.tts, input_source="text")
    response_data = {
        "response": result.get("response", ""),
        "skill_used": result.get("skill_used"),
    }
    tts_path = result.get("tts_path", "")
    if tts_path and os.path.exists(tts_path):
        try:
            with open(tts_path, "rb") as f:
                response_data["audio"] = base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"Chat REST TTS Read Error: {e}")
    return response_data


# ═══════════════════════════════════════════════════
# KNOWLEDGE BASE ENDPOINTS
# ═══════════════════════════════════════════════════

class KBRebuildRequest(BaseModel):
    pass

class KBAddRequest(BaseModel):
    filename: str
    content: str

@app.post("/api/kb/rebuild")
async def kb_rebuild(_auth=Depends(verify_token)):
    kb = get_knowledge_base(config)
    result = kb.build_index()
    return {"result": result}

@app.get("/api/kb/list")
async def kb_list(_auth=Depends(verify_token)):
    kb = get_knowledge_base(config)
    return {"result": kb.list_documents()}

@app.get("/api/kb/stats")
async def kb_stats(_auth=Depends(verify_token)):
    kb = get_knowledge_base(config)
    return {"result": kb.get_stats()}

@app.get("/api/kb/search")
async def kb_search(query: str = Query(...), _auth=Depends(verify_token)):
    kb = get_knowledge_base(config)
    ctx = kb.query(query, top_k=5, min_similarity=0.20)
    return {"result": ctx or "No relevant results found."}

@app.post("/api/kb/add")
async def kb_add(request: KBAddRequest, _auth=Depends(verify_token)):
    kb = get_knowledge_base(config)
    result = kb.add_document(request.filename, request.content)
    return {"result": result}


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def _get_lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    lan_ip = _get_lan_ip()
    logger.info(f"MAX v4.2 starting on {config.HOST}:{config.PORT}")
    logger.info(f"   LAN IP: {lan_ip}")
    logger.info(f"   📱 Mobile App Target: {lan_ip}:{config.PORT}")
    logger.info(f"   LLM: {config.LLM_MODEL}")
    logger.info(f"   TTS: {config.TTS_VOICE}")
    logger.info(f"   Skills: {len(get_skills_engine(config).skills_registry)} registered")
    logger.info(f"   Wake word: enabled")

    if config.DEBUG:
        uvicorn.run(
            "main:app",
            host=config.HOST,
            port=config.PORT,
            reload=True,
            reload_excludes=["*.json", "*.png", "*.jpg", "*.wav", "*.mp3", "*.log", "data/*", "knowledge/*", "brain/*", "scratch/*", ".system_generated/*"],
            log_level="debug",
        )
    else:
        uvicorn.run(
            app,
            host=config.HOST,
            port=config.PORT,
            log_level="info",
        )
