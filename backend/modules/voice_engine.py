# Path: backend/modules/voice_engine.py
# codex-changes detail: make sounddevice optional so importing skills does not disable the whole backend on machines without audio drivers.
try:
    import sounddevice as sd
except ImportError:
    sd = None
import os
import threading
import logging


logger = logging.getLogger("MAX.VOICE")

# 🚀 Permanently disable the Windows console crash bug
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
try:
    
    import huggingface_hub
    huggingface_hub.utils.disable_progress_bars()
except:
    pass

class VoiceEngine:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.pipeline = None
        self.voice_name = 'af_bella' # Tum yahan 'am_adam', 'af_sky' set kar sakte ho
        self.is_ready = False
        
        # Load engine in a background thread so it doesn't block server startup
        threading.Thread(target=self._init_engine, daemon=True).start()

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = VoiceEngine()
        return cls._instance

    def _init_engine(self):
        try:
            logger.info("🎙️ Initializing Local Voice Engine (Kokoro)...")
            from kokoro import KPipeline
            self.pipeline = KPipeline(lang_code='a')
            self.is_ready = True
            logger.info("✅ Voice Engine is Online and Ready!")
        except Exception as e:
            logger.error(f"❌ Failed to load Voice Engine: {e}")

    def speak(self, text: str):
        # codex-changes detail: fail explicitly at playback time instead of import time when sounddevice is unavailable.
        if sd is None:
            logger.warning("sounddevice is not installed; skipping local voice playback.")
            return
        if not self.is_ready or not self.pipeline:
            logger.warning("Voice engine is still loading or failed. Cannot speak yet.")
            return

        try:
            # Clean up text a bit for better TTS parsing
            clean_text = text.replace("*", "").replace("#", "").strip()
            if not clean_text:
                return

            generator = self.pipeline(clean_text, voice=self.voice_name, speed=1.0, split_pattern=r'\n+')
            
            for i, (graphemes, phonemes, audio) in enumerate(generator):
                sd.play(audio, samplerate=24000)
                sd.wait() # Wait for the audio chunk to finish
                
        except Exception as e:
            logger.error(f"TTS Playback Error: {e}")
