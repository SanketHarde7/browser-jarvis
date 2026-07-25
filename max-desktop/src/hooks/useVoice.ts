// Path: max-desktop/src/hooks/useVoice.ts
// Use: Controls voice recording within Tauri environment.
/**
 * useVoice.ts — MAX-AILE v4 (Silero VAD + Barge-In)
 *
 * Custom voice hook that uses @ricky0123/vad-web for highly accurate,
 * WebAssembly-powered voice activity detection (VAD).
 *
 * Supports:
 *   - Continuous Listening (Ambient VAD via Silero)
 *   - Real-time barge-in trigger (onSpeechStart)
 *   - Push-to-Talk manual recording (MediaRecorder)
 *   - PCM to 16kHz 16-bit Mono WAV container encoder
 */

import { useRef, useState, useCallback, useEffect } from "react";

interface UseVoiceOptions {
  onAudioReady: (base64: string) => void;
  onError:      (message: string) => void;
  onSpeechStart?: () => void;
}

// In-memory Float32 PCM to WAV converter (16kHz, 16-bit, Mono)
function bufferToWav(buffer: Float32Array, sampleRate: number = 16000): Blob {
  const bufferLength = buffer.length;
  const wavBuffer = new ArrayBuffer(44 + bufferLength * 2);
  const view = new DataView(wavBuffer);
  
  function writeString(view: DataView, offset: number, string: string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }
  
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + bufferLength * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // Raw PCM
  view.setUint16(22, 1, true); // Mono channel
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // Byte rate
  view.setUint16(32, 2, true); // Block align
  view.setUint16(34, 16, true); // Bits per sample
  writeString(view, 36, 'data');
  view.setUint32(40, bufferLength * 2, true);
  
  // Find maximum amplitude for peak normalization
  let maxVal = 0;
  for (let i = 0; i < bufferLength; i++) {
    const absVal = Math.abs(buffer[i]);
    if (absVal > maxVal) maxVal = absVal;
  }
  
  // Scale samples so peak reaches 0.9 (avoiding hard clipping)
  const scale = maxVal > 0 ? (0.9 / maxVal) : 1.0;
  
  let offset = 44;
  for (let i = 0; i < bufferLength; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, buffer[i] * scale));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  
  return new Blob([view], { type: 'audio/wav' });
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const base64 = (reader.result as string).split(",")[1];
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export function useVoice({ onAudioReady, onError, onSpeechStart }: UseVoiceOptions) {
  const [isRecording, setIsRecording]     = useState(false);
  const [hasPermission, setHasPermission] = useState<boolean | null>(null);

  const mediaRecorderRef    = useRef<MediaRecorder | null>(null);
  const chunksRef           = useRef<Blob[]>([]);
  const streamRef           = useRef<MediaStream | null>(null);
  const audioContextRef     = useRef<AudioContext | null>(null);
  
  // Silero VAD ref
  const vadRef              = useRef<any>(null);
  const isInitializingRef   = useRef(false);

  // VAD state
  const isContinuousActiveRef = useRef(false);
  const jarvisStateRef        = useRef("idle");

  // Stable callback refs
  const onAudioReadyRef = useRef(onAudioReady);
  const onErrorRef      = useRef(onError);
  const onSpeechStartRef = useRef(onSpeechStart);
  
  onAudioReadyRef.current = onAudioReady;
  onErrorRef.current      = onError;
  onSpeechStartRef.current = onSpeechStart;

  // Cleanup everything
  const cleanup = useCallback(() => {
    if (vadRef.current) {
      try {
        vadRef.current.pause();
        vadRef.current = null;
      } catch (_) {}
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try { mediaRecorderRef.current.stop(); } catch (_) {}
      mediaRecorderRef.current = null;
    }
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      audioContextRef.current.close().catch(() => {});
    }
    audioContextRef.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  // ── Ensure AudioContext + Stream are alive (for manual PTT fallback) ──
  const ensureAudio = async (): Promise<MediaStream> => {
    if (audioContextRef.current?.state === "suspended") {
      await audioContextRef.current.resume();
    }

    if (streamRef.current && audioContextRef.current && audioContextRef.current.state === "running") {
      return streamRef.current;
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    streamRef.current = stream;
    setHasPermission(true);

    const ctx = new AudioContext();
    audioContextRef.current = ctx;
    if (ctx.state === "suspended") await ctx.resume();

    return stream;
  };

  // ── PUBLIC API ──

  const startContinuousListening = useCallback(async (state: string) => {
    try {
      jarvisStateRef.current = state;
      isContinuousActiveRef.current = true;

      const stream = await ensureAudio();

      if (vadRef.current) {
        vadRef.current.start();
        return;
      }

      if (isInitializingRef.current) {
        console.log("[MAX-AILE] VAD initialization already in progress, skipping duplicate call.");
        return;
      }
      isInitializingRef.current = true;

      console.log("[MAX-AILE] Loading Silero VAD via @ricky0123/vad-web...");
      const { MicVAD } = await import("@ricky0123/vad-web");
      
      const myvad = await MicVAD.new({
        stream: stream,
        onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.26.0/dist/",
        baseAssetPath: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/dist/",
        onSpeechStart: () => {
          console.log("[VAD] onSpeechStart triggered");
          if (isContinuousActiveRef.current) {
            onSpeechStartRef.current?.();
          }
        },
        onSpeechEnd: async (audio: Float32Array) => {
          console.log("[VAD] onSpeechEnd triggered, frames:", audio.length);
          if (!isContinuousActiveRef.current) return;

          // Only emit if recording is at least 400ms (16000Hz * 0.4 = 6400 samples)
          if (audio.length >= 6400) {
            try {
              const wavBlob = bufferToWav(audio, 16000);
              const b64 = await blobToBase64(wavBlob);
              onAudioReadyRef.current(b64);
            } catch (err) {
              console.error("[VAD] Error encoding PCM buffer to WAV:", err);
            }
          } else {
            console.log("[VAD] Speech chunk discarded (too short)");
          }
        },
        // Strict Silero parameters for high noise rejection
        // Higher threshold = requires more confidence that it's actual speech
        positiveSpeechThreshold: 0.80,
        negativeSpeechThreshold: 0.45,
        minSpeechMs: 500,
        redemptionMs: 1000,
      } as any);
      
      vadRef.current = myvad;
      isInitializingRef.current = false;
      
      console.log("[MAX-AILE] Continuous listening active (Silero VAD)");
    } catch (err) {
      isInitializingRef.current = false;
      console.error("[MAX-AILE] Failed to start continuous listening:", err);
      onErrorRef.current("Failed to access microphone or load VAD assets.");
    }
  }, []);

  const stopContinuousListening = useCallback(() => {
    isContinuousActiveRef.current = false;
    if (vadRef.current) {
      vadRef.current.pause();
    }
    console.log("[MAX-AILE] Continuous listening paused");
  }, []);

  const updateJarvisState = useCallback((state: string) => {
    jarvisStateRef.current = state;
  }, []);

  // ── MANUAL PUSH-TO-TALK ──
  const startRecording = useCallback(async () => {
    try {
      const stream = await ensureAudio();
      
      // Suspend continuous listening during manual PTT
      const wasContinuous = isContinuousActiveRef.current;
      isContinuousActiveRef.current = false;
      if (vadRef.current) {
        vadRef.current.pause();
      }

      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus" : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      chunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeType });
        if (blob.size >= 512) {
          const reader = new FileReader();
          reader.onloadend = () => {
            const b64 = (reader.result as string).split(",")[1];
            onAudioReadyRef.current(b64);
          };
          reader.readAsDataURL(blob);
        }
        
        // Restore continuous listening if it was active
        if (wasContinuous) {
          isContinuousActiveRef.current = true;
          if (vadRef.current) {
            vadRef.current.start();
          }
        }
      };

      recorder.start(100);
      setIsRecording(true);
    } catch (err) {
      console.error("[MAX-AILE] Manual recording failed:", err);
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (!mediaRecorderRef.current || mediaRecorderRef.current.state === "inactive") return;
    setIsRecording(false);
    mediaRecorderRef.current.stop();
  }, []);

  return {
    isRecording, hasPermission,
    startRecording, stopRecording,
    startContinuousListening, stopContinuousListening,
    updateJarvisState
  };
}
