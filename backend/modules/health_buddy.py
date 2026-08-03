# Path: backend/modules/health_buddy.py
# Use: Tracks fitness stats, health metrics, and reminders.
"""
health_buddy.py — MAX v4.8 (Health & Focus Agent — Fixed Idle Detection)
- Runs a background thread tracking developer screen time.
- Uses Windows GetLastInputInfo API for TRUE idle detection (keyboard + mouse).
- Triggers non-intrusive TTS audio responses without UI toast or pop-ups.
- Automatically resets on system idle/sleep.
"""
import time
import logging
import threading
import platform
from datetime import datetime

logger = logging.getLogger("MAX.HEALTH")

# ── Windows Idle Detection via ctypes ──
_get_idle_seconds = None

if platform.system() == "Windows":
    try:
        import ctypes
        import ctypes.wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.UINT),
                ("dwTime", ctypes.wintypes.DWORD),
            ]

        def _win_get_idle_seconds() -> float:
            """Returns seconds since last user input (keyboard OR mouse)."""
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
            return 0.0

        _get_idle_seconds = _win_get_idle_seconds
        logger.info("Using Windows GetLastInputInfo for idle detection.")
    except Exception as e:
        logger.warning(f"Windows idle detection setup failed: {e}")

# Fallback: pyautogui mouse-only detection
if _get_idle_seconds is None:
    try:
        import pyautogui
        _last_mouse_pos = [None]
        _last_activity_ts = [time.time()]

        def _fallback_get_idle_seconds() -> float:
            pos = pyautogui.position()
            if pos != _last_mouse_pos[0]:
                _last_mouse_pos[0] = pos
                _last_activity_ts[0] = time.time()
            return time.time() - _last_activity_ts[0]

        _get_idle_seconds = _fallback_get_idle_seconds
        logger.info("Using pyautogui mouse fallback for idle detection.")
    # codex-changes detail: pyautogui can fail in headless Linux with KeyError('DISPLAY'), so catch all setup failures.
    except Exception as e:
        def _get_idle_seconds_noop() -> float:
            return 0.0
        _get_idle_seconds = _get_idle_seconds_noop
        logger.warning(f"No idle detection available (ctypes/pyautogui setup failed): {e}")


class HealthBuddy:
    def __init__(self, api_send_func):
        """
        api_send_func: Callback function to push messages/audio to the frontend websocket.
        """
        self.send_to_frontend = api_send_func
        self.running = False

        # Timers in seconds
        self.EYE_INTERVAL = 45 * 60      # 45 minutes
        self.POSTURE_INTERVAL = 90 * 60   # 90 minutes
        self.IDLE_THRESHOLD = 15 * 60     # 15 minutes of NO input = idle

        self.eye_timer = 0
        self.posture_timer = 0
        self.tick_rate = 5  # Check system state every 5 seconds

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._health_loop, daemon=True)
            self.thread.start()
            logger.info("MAX Health Focus Buddy active in background.")

    def stop(self):
        self.running = False

    def _health_loop(self):
        while self.running:
            time.sleep(self.tick_rate)

            try:
                idle_secs = _get_idle_seconds()
            except Exception:
                idle_secs = 0.0

            # 1. If user is truly idle (no keyboard or mouse for 15 min), reset timers
            if idle_secs >= self.IDLE_THRESHOLD:
                if self.eye_timer > 0 or self.posture_timer > 0:
                    logger.info(f"User idle for {idle_secs:.0f}s. Resetting health trackers.")
                    self.eye_timer = 0
                    self.posture_timer = 0
                continue

            # 2. User is active — increment tracking timers
            self.eye_timer += self.tick_rate
            self.posture_timer += self.tick_rate

            # 3. Eye Strain Check (45 Mins)
            if self.eye_timer >= self.EYE_INTERVAL:
                self._trigger_voice_alert(
                    text="Sanket, forty-five minutes passed. Look away from the screen for twenty seconds.",
                    reason="eye_strain"
                )
                self.eye_timer = 0

            # 4. Posture Check (90 Mins)
            if self.posture_timer >= self.POSTURE_INTERVAL:
                self._trigger_voice_alert(
                    text="Time to stretch. Roll your shoulders back and sit up straight.",
                    reason="posture"
                )
                self.posture_timer = 0

    def _trigger_voice_alert(self, text: str, reason: str):
        """Generates dynamic local audio response through MAX text-to-speech pipeline."""
        logger.info(f"Triggering soft voice reminder for: {reason}")
        try:
            import asyncio
            import base64
            import os
            from modules.tts import generate_tts

            # Create a thread-specific event loop to safely run async functions in background thread
            loop = asyncio.new_event_loop()
            try:
                audio_path = loop.run_until_complete(generate_tts(text))
            finally:
                loop.close()

            if audio_path and os.path.exists(audio_path):
                try:
                    with open(audio_path, "rb") as f:
                        audio_data = f.read()
                    audio_base64 = base64.b64encode(audio_data).decode("utf-8")

                    payload = {
                        "event": "audio_response",
                        "audio": audio_base64,
                        "metadata": {"type": "health_alert", "reason": reason}
                    }
                    self.send_to_frontend(payload)
                finally:
                    try:
                        os.unlink(audio_path)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Failed to generate health voice payload: {e}")
