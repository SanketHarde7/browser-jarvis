# Path: backend/modules/tts.py
# Use: Converts text responses into spoken voice output.
"""
tts.py — MAX v5.3 (Kokoro Local Offline TTS Integration)
Replaced Edge-TTS with Kokoro for zero-limit, expressive local audio.
"""
import os
import asyncio
import logging
import tempfile
import threading
import numpy as np
import soundfile as sf
from pathlib import Path
from config import config

logger = logging.getLogger("MAX.TTS")

# 🚀 FIX: Disable HuggingFace Progress Bars that crash the Windows Terminal
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
try:
    import huggingface_hub
    huggingface_hub.utils.disable_progress_bars()
except:
    pass

class LocalVoiceEngine:
    """Singleton to keep Kokoro loaded in RAM (Warm Start)"""
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.pipeline = None
        self.voice_name = 'af_bella'  # Default expressive voice
        self.is_ready = False
        
        # Start loading engine in background immediately when server starts
        threading.Thread(target=self._init_engine, daemon=True).start()

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = LocalVoiceEngine()
        return cls._instance

    def _init_engine(self):
        try:
            logger.info("🎙️ Initializing Local Voice Engine (Kokoro) in background...")
            from kokoro import KPipeline
            self.pipeline = KPipeline(lang_code='a')
            self.is_ready = True
            logger.info("✅ Kokoro Voice Engine is Online and Ready!")
        except Exception as e:
            logger.error(f"❌ Failed to load Voice Engine: {e}")

# Initialize the singleton immediately when this module is imported
_engine = LocalVoiceEngine.get_instance()


_tts_lock = asyncio.Lock()

async def generate_tts(text: str, voice: str = "", output_path: str = "") -> str:
    """Generate TTS audio using local Kokoro and return the file path."""
    if not text or not text.strip():
        logger.warning("TTS called with empty text, skipping.")
        return ""

    if not _engine.is_ready or not _engine.pipeline:
        logger.warning("Kokoro Engine is still loading. Falling back to silent/empty.")
        return ""

    async with _tts_lock:
        try:
            # Clean text for better pronunciation and remove markdown
            clean_text = text.replace("*", "").replace("#", "").strip()
            
            if not output_path:
                # Kokoro works best with .wav
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                output_path = tmp.name
                tmp.close()

            chosen_voice = voice if voice else _engine.voice_name

            # Run generation in a background thread so it doesn't block FastAPI/Uvicorn
            def _generate_audio_file():
                generator = _engine.pipeline(clean_text, voice=chosen_voice, speed=1.0, split_pattern=r'\n+')
                audio_chunks = []
                
                for i, (graphemes, phonemes, audio) in enumerate(generator):
                    audio_chunks.append(audio)
                
                if audio_chunks:
                    # Concatenate all lines into one single audio array
                    full_audio = np.concatenate(audio_chunks)
                    # Save to WAV file at 24000Hz (Kokoro's native sample rate)
                    sf.write(output_path, full_audio, 24000)
                    return True
                return False

            success = await asyncio.to_thread(_generate_audio_file)

            if success and os.path.exists(output_path):
                logger.info(f"Kokoro TTS generated successfully: {output_path}")
                return output_path
            
            return ""

        except Exception as e:
            import traceback
            logger.error(f"Kokoro TTS generation failed: {e}\n{traceback.format_exc()}")
            return ""


async def generate_tts_paced(text: str, pause_seconds: float = 0.8, voice: str = "", output_path: str = "") -> str:
    """Generate TTS with natural pauses between lines (using numpy zero padding)."""
    if not text or not text.strip():
        return ""
    if not _engine.is_ready or not _engine.pipeline:
        return ""
        
    try:
        clean_text = text.replace("*", "").replace("#", "").strip()
        if not output_path:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            output_path = tmp.name
            tmp.close()
            
        chosen_voice = voice if voice else _engine.voice_name
        
        def _generate_paced():
            generator = _engine.pipeline(clean_text, voice=chosen_voice, speed=1.0, split_pattern=r'\n+')
            audio_chunks = []
            
            silence_samples = int(pause_seconds * 24000)
            silence_array = np.zeros(silence_samples, dtype=np.float32)
            
            for i, (graphemes, phonemes, audio) in enumerate(generator):
                if i > 0:
                    audio_chunks.append(silence_array)
                audio_chunks.append(audio)
                
            if audio_chunks:
                full_audio = np.concatenate(audio_chunks)
                sf.write(output_path, full_audio, 24000)
                return True
            return False
            
        success = await asyncio.to_thread(_generate_paced)
        if success and os.path.exists(output_path):
            return output_path
        return ""
    except Exception as e:
        logger.error(f"Kokoro Paced TTS failed: {e}")
        return ""

async def speak_text(text: str) -> bool:
    """Convenience: generate TTS and return path."""
    path = await generate_tts(text)
    return bool(path)