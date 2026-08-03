# Path: backend/modules/tts.py
# Use: Converts text responses into spoken voice output.
"""
tts.py — MAX v5.3 (Kokoro Local Offline TTS Integration)
Replaced Edge-TTS with Kokoro for zero-limit, expressive local audio.
"""
import os
import re
import asyncio
import logging
import tempfile
import threading
# codex-changes detail: keep the TTS module importable when optional Kokoro audio dependencies are missing.
try:
    import numpy as np
except ImportError:
    np = None
try:
    import soundfile as sf
except ImportError:
    sf = None
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
            # codex-changes detail: Kokoro needs numpy and soundfile for file generation, so do not mark the engine ready without them.
            if np is None or sf is None:
                missing = ", ".join(name for name, mod in {"numpy": np, "soundfile": sf}.items() if mod is None)
                raise RuntimeError(f"Missing optional audio dependency/dependencies: {missing}")
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
    """Generate TTS audio using local Kokoro with edge-tts fallback."""
    if not text or not text.strip():
        logger.warning("TTS called with empty text, skipping.")
        return ""

    # codex-changes detail: choose an output suffix based on the actually available local audio stack.
    kokoro_ready = _engine.is_ready and _engine.pipeline and np is not None and sf is not None
    if not output_path:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav" if kokoro_ready else ".mp3", delete=False)
        output_path = tmp.name
        tmp.close()

    # Clean text for Kokoro phoneme tokenizer (strip markdown, emojis, special symbols)
    clean_text = re.sub(r"```[\s\S]*?```", "", text)
    clean_text = re.sub(r"`[^`]*`", "", clean_text)
    clean_text = re.sub(r"[#*_~`\[\]()<>@$%^&+=|{\}]", " ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    if not clean_text:
        return ""

    # Attempt Kokoro generation if engine is ready
    # codex-changes detail: only run Kokoro when all optional runtime dependencies are available.
    if kokoro_ready:
        async with _tts_lock:
            try:
                chosen_voice = voice if voice else _engine.voice_name

                def _generate_audio_file():
                    generator = _engine.pipeline(clean_text, voice=chosen_voice, speed=1.0, split_pattern=r'\n+')
                    audio_chunks = []
                    for i, (graphemes, phonemes, audio) in enumerate(generator):
                        audio_chunks.append(audio)
                    if audio_chunks:
                        full_audio = np.concatenate(audio_chunks)
                        wav_path = output_path if output_path.endswith(".wav") else output_path + ".wav"
                        sf.write(wav_path, full_audio, 24000)
                        return wav_path
                    return ""

                res_path = await asyncio.to_thread(_generate_audio_file)
                if res_path and os.path.exists(res_path):
                    logger.info(f"Kokoro TTS generated successfully: {res_path}")
                    return res_path
            except Exception as e:
                logger.warning(f"Kokoro TTS generation failed: {e}. Falling back to edge-tts.")

    # Fallback 2: Edge-TTS
    try:
        import edge_tts
        mp3_path = output_path if output_path.endswith(".mp3") else output_path + ".mp3"
        tts_voice = voice if voice else getattr(config, "TTS_VOICE_HINDI", "hi-IN-SwaraNeural")
        communicate = edge_tts.Communicate(clean_text[:400], tts_voice)
        await communicate.save(mp3_path)
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            logger.info(f"Edge-TTS generated successfully: {mp3_path}")
            return mp3_path
    except Exception as fallback_err:
        logger.warning(f"Edge-TTS fallback failed: {fallback_err}")

    # Fallback 3: Pyttsx3 (Windows Native Offline SAPI5 TTS)
    try:
        import pyttsx3
        wav_path = output_path if output_path.endswith(".wav") else output_path + ".wav"
        engine = pyttsx3.init()
        engine.save_to_file(clean_text[:300], wav_path)
        engine.runAndWait()
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            logger.info(f"Pyttsx3 native offline TTS generated successfully: {wav_path}")
            return wav_path
    except Exception as pyttsx_err:
        logger.warning(f"Pyttsx3 fallback failed: {pyttsx_err}")

    return ""


async def generate_tts_paced(text: str, pause_seconds: float = 0.8, voice: str = "", output_path: str = "") -> str:
    """Generate TTS with natural pauses between lines (using numpy zero padding)."""
    if not text or not text.strip():
        return ""
    # codex-changes detail: paced local generation requires numpy and soundfile; return cleanly if unavailable.
    if not _engine.is_ready or not _engine.pipeline or np is None or sf is None:
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

