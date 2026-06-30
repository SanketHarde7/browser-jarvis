// Path: max-desktop/src/App.tsx
// Use: Main component layout for Tauri desktop view.
/**
 * App.tsx — MAX v5.0 (Fixed Stale Closures & WebSocket Handling)
 * Orb UI: centered, draggable, full voice pipeline.
 *
 * CLICK BEHAVIOR:
 * Single click (idle)      → start listening (auto-stops on silence)
 * Single click (listening) → force-stop recording, process immediately
 * Single click (speaking)  → STOP MAX (kill audio playback & abort)
 * Single click (processing)→ STOP MAX (abort backend request)
 * Double click             → open real frontend in system browser
 * Long press + drag        → move the orb
 */

import React, { useEffect, useState, useRef, useCallback } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { availableMonitors, getCurrentWindow } from "@tauri-apps/api/window";
import { LogicalSize, PhysicalPosition } from "@tauri-apps/api/dpi";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { useBackend, BackendMessage, BackendStatus } from "./hooks/useBackend";
import { useVoice } from "./hooks/useVoice";
import { start_listening_animation, stop_listening_animation } from "./overlayController";
import "./App.css";

const DRAG_THRESHOLD_MS = 200;
const POSITION_SAVE_DEBOUNCE_MS = 300;
const TOAST_DURATION_MS = 3_000;
const DBLCLICK_DELAY_MS = 280;
const TEXT_WINDOW_LABEL = "overlay";
const TEXT_WINDOW_CONTENT_KEY = "max-text-window-content";
const TEXT_WINDOW_VISIBLE_KEY = "max-text-window-visible";

type OrbState = "idle" | "listening" | "processing" | "speaking" | "error" | "offline";

const App: React.FC = () => {
  const mainWindowRef = useRef(getCurrentWindow());
  const textWindowRef = useRef<WebviewWindow | null>(null);

  const [orbState, setOrbState] = useState<OrbState>("idle");
  const [toastText, setToastText] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [continuousListening, setContinuousListening] = useState(true);
  const [isOrbHidden, setIsOrbHidden] = useState(false);

  // Use refs for values that need to be current in callbacks without triggering re-renders
  const lastResearchFileRef = useRef<string | null>(null);
  const botBypassUrlRef = useRef<string | null>(null);
  const ignoreResponseRef = useRef<boolean>(false);
  const currentCommandIdRef = useRef<string>("");
  const lastSpeechEndRef = useRef<number>(0);
  const orbStateRef = useRef<OrbState>("idle");

  // Keep orbStateRef in sync
  useEffect(() => {
    orbStateRef.current = orbState;
  }, [orbState]);

  const dragTimerRef = useRef<number | null>(null);
  const dragStartedRef = useRef(false);
  const saveTimerRef = useRef<number | null>(null);
  const toastTimerRef = useRef<number | null>(null);
  const errorTimerRef = useRef<number | null>(null);
  const islandTimerRef = useRef<number | null>(null);
  const typewriterRef = useRef<number | null>(null);
  const pendingHideRef = useRef(false);
  const writingCompleteRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const clickCountRef = useRef(0);
  const clickTimerRef = useRef<number | null>(null);
  const pointerDownTime = useRef(0);

  const hibernateTimerRef = useRef<number | null>(null);
  const hibernateInFlightRef = useRef(false);
  const shouldHibernateRef = useRef(false);

  // ── Orb visual state ──
  useEffect(() => {
    const activeStates = ["listening", "processing", "speaking"];
    localStorage.setItem("max-overlay-state", orbState);
    setIsOrbHidden(localStorage.getItem("max-orb-hidden") === "1");
    if (activeStates.includes(orbState)) {
      start_listening_animation().catch(console.error);
    } else {
      stop_listening_animation().catch(console.error);
    }
    mainWindowRef.current.setAlwaysOnTop(true).catch(() => {});
  }, [orbState]);

  // ── Toast & Error helpers ──
  const showToast = useCallback((text: string) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToastText(text);
    toastTimerRef.current = window.setTimeout(() => setToastText(""), TOAST_DURATION_MS);
  }, []);

  const showError = useCallback((msg: string) => {
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    setOrbState("error");
    setErrorMsg(msg);
    errorTimerRef.current = window.setTimeout(() => {
      setOrbState("idle");
      setErrorMsg("");
    }, 3_000);
  }, []);

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
  }, []);

  // ── Text Window helpers ──
  const getTextWindow = useCallback(async () => {
    if (!textWindowRef.current) {
      textWindowRef.current = await WebviewWindow.getByLabel(TEXT_WINDOW_LABEL);
    }
    return textWindowRef.current;
  }, []);

  const hideTextWindow = useCallback(async (force: boolean = false) => {
    const pinned = localStorage.getItem('max-text-window-pinned') === '1';
    if (!force && pinned) return;

    localStorage.setItem("max-orb-hidden", "0");
    setIsOrbHidden(false);
    localStorage.setItem(TEXT_WINDOW_VISIBLE_KEY, "0");
    localStorage.removeItem(TEXT_WINDOW_CONTENT_KEY);
    localStorage.removeItem("max-text-window-position");

    if (!force && typewriterRef.current) {
      pendingHideRef.current = true;
      return;
    }

    if (typewriterRef.current) {
      clearInterval(typewriterRef.current);
      typewriterRef.current = null;
    }
    if (islandTimerRef.current) {
      clearTimeout(islandTimerRef.current);
      islandTimerRef.current = null;
    }

    const textWindow = await getTextWindow();
    if (!textWindow) return;
    try {
      await textWindow.hide();
      pendingHideRef.current = false;
      writingCompleteRef.current = false;
    } catch {}
  }, [getTextWindow]);

  const showTextWindow = useCallback(async (text: string) => {
    if (islandTimerRef.current) {
      clearTimeout(islandTimerRef.current);
      islandTimerRef.current = null;
    }
    if (typewriterRef.current) {
      clearInterval(typewriterRef.current);
      typewriterRef.current = null;
    }

    pendingHideRef.current = false;
    writingCompleteRef.current = false;
    localStorage.setItem("max-orb-hidden", "1");
    setIsOrbHidden(true);

    const textWindow = await getTextWindow();
    if (!textWindow) {
      showToast(text);
      return;
    }

    try {
      const [position, size] = await Promise.all([
        mainWindowRef.current.outerPosition(),
        mainWindowRef.current.outerSize(),
      ]);

      const totalChars = Math.max(1, text.length);
      const preferredWidth = Math.ceil(Math.min(720, Math.max(260, Math.sqrt(totalChars) * 28)));
      const approxCharWidth = 8;
      const charsPerLine = Math.max(18, Math.floor(preferredWidth / approxCharWidth));
      const lines = Math.max(1, Math.ceil(totalChars / charsPerLine));
      const estimatedWidth = Math.max(260, Math.min(900, preferredWidth));
      const estimatedHeight = Math.max(140, Math.min(1200, 80 + lines * 30));

      const desiredX = position.x + size.width + 6;
      localStorage.setItem(
        "max-text-window-position",
        JSON.stringify({ x: desiredX, y: position.y })
      );
      const tw = await getTextWindow();
      if (tw) {
        await tw.setAlwaysOnTop(true).catch(() => {});
        await tw.setResizable(true).catch(() => {});
        await tw.setSize(new LogicalSize(estimatedWidth, estimatedHeight)).catch(() => {});
        await tw.setPosition(new PhysicalPosition(desiredX, position.y)).catch(() => {});
        await tw.show().catch(() => {});
      }
    } catch {}

    localStorage.setItem(TEXT_WINDOW_VISIBLE_KEY, "1");
    localStorage.setItem(TEXT_WINDOW_CONTENT_KEY, text);
    writingCompleteRef.current = true;

    const closeDelay = Math.min(20000, Math.max(2500, text.length * 120));
    islandTimerRef.current = window.setTimeout(() => {
      void hideTextWindow();
    }, closeDelay);

    if (pendingHideRef.current) {
      void hideTextWindow(true);
    }
  }, [getTextWindow, hideTextWindow, showToast]);

  // ── Hibernate ──
  const triggerHibernate = useCallback(async (reason: string) => {
    if (hibernateInFlightRef.current) return;
    hibernateInFlightRef.current = true;

    if (hibernateTimerRef.current) {
      clearTimeout(hibernateTimerRef.current);
      hibernateTimerRef.current = null;
    }

    stopSpeaking();
    setOrbState("idle");

    try {
      console.log(`Exit triggered: ${reason}`);
      await invoke("exit_app");
    } catch (e) {
      console.error("Failed to invoke Rust exit_app:", e);
    } finally {
      hibernateInFlightRef.current = false;
    }
  }, [stopSpeaking]);

  // ── Audio playback queue ──
  const audioQueueRef = useRef<{ rawBase64: string, hibernateAfter: boolean, isHealthAlert: boolean }[]>([]);
  const isPlayingRef = useRef<boolean>(false);

  const processAudioQueue = useCallback(async () => {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
    
    isPlayingRef.current = true;
    const nextAudio = audioQueueRef.current.shift();
    if (!nextAudio) {
      isPlayingRef.current = false;
      return;
    }

    const { rawBase64, hibernateAfter, isHealthAlert } = nextAudio;
    setOrbState("speaking");
    const audio = new Audio(`data:audio/mp3;base64,${rawBase64}`);
    audioRef.current = audio;
    audio.volume = isHealthAlert ? 0.35 : 1.0;

    const scheduleHide = () => {
      if (islandTimerRef.current) clearTimeout(islandTimerRef.current);
      islandTimerRef.current = window.setTimeout(() => {
        void hideTextWindow();
      }, 3000);
    };

    return new Promise<void>((resolve) => {
      audio.onended = async () => {
        audioRef.current = null;
        if (hibernateAfter) {
          await triggerHibernate("audio-ended");
        }
        resolve();
      };

      audio.onerror = () => {
        audioRef.current = null;
        if (hibernateAfter) {
          void triggerHibernate("audio-error");
        }
        resolve();
      };

      audio.play().catch(() => {
        audioRef.current = null;
        if (hibernateAfter) {
          void triggerHibernate("audio-play-failed");
        }
        resolve();
      });
    }).then(() => {
      isPlayingRef.current = false;
      if (audioQueueRef.current.length === 0) {
        setOrbState("idle");
        scheduleHide();
      } else {
        void processAudioQueue();
      }
    });
  }, [triggerHibernate, hideTextWindow]);

  const playAudio = useCallback((rawBase64: string, hibernateAfter: boolean = false, isHealthAlert: boolean = false) => {
    audioQueueRef.current.push({ rawBase64, hibernateAfter, isHealthAlert });
    void processAudioQueue();
  }, [processAudioQueue]);

  // ── Smart follow-up interpreter ──
  const handleVoiceCommandInterpretation = useCallback((userText: string): boolean => {
    const text = userText.toLowerCase().trim();

    // Research file follow-up
    if ((text.includes("yes") || text.includes("open") || text.includes("kholo") || text.includes("haan")) && lastResearchFileRef.current) {
      send({
        type: "execute_skill",
        skill: "open_app",
        params: [lastResearchFileRef.current]
      } as any);
      lastResearchFileRef.current = null;
      ignoreResponseRef.current = true;
      setOrbState("idle");
      return true;
    }

    // Bot bypass follow-up
    if ((text.includes("yes") || text.includes("open") || text.includes("kholo") || text.includes("haan")) && botBypassUrlRef.current) {
      send({
        type: "execute_skill",
        skill: "web_open",
        params: [botBypassUrlRef.current]
      } as any);
      botBypassUrlRef.current = null;
      ignoreResponseRef.current = true;
      setOrbState("idle");
      return true;
    }

    return false;
  }, []);  // send is from useBackend below

  // ── Backend message handler ──
  const handleBackendMessage = useCallback((msg: BackendMessage) => {
    // Stale message check
    if (msg.command_id && msg.command_id !== currentCommandIdRef.current) {
      console.log(`[App] Discarding stale message for ${msg.command_id}`);
      return;
    }

    switch (msg.event) {
      case "stale_discard":
        setOrbState("idle");
        showToast("⚠️ Discarded stale request");
        break;

      case "start_continuous_listening":
        setContinuousListening(true);
        showToast("🎙️ Ambient Listening ON");
        break;

      case "stop_continuous_listening":
        setContinuousListening(false);
        setOrbState("idle");
        showToast("🔇 Ambient Listening OFF");
        break;

      case "greeting":
        if (msg.text) showToast(msg.text);
        break;

      case "transcript":
        if (msg.text) {
          const intercepted = handleVoiceCommandInterpretation(msg.text);
          if (intercepted) break;
        }
        break;

      case "response_text":
        if (msg.command_id && currentCommandIdRef.current && msg.command_id !== currentCommandIdRef.current) {
          console.log(`Ignoring stale response_text for ${msg.command_id}`);
          break;
        }
        if (ignoreResponseRef.current) break;

        if (msg.text) {
          if (msg.text.includes("[ACTION:HIBERNATE]")) {
            console.log("HIBERNATE TAG RECEIVED!");
            shouldHibernateRef.current = true;

            if (hibernateTimerRef.current) clearTimeout(hibernateTimerRef.current);
            hibernateTimerRef.current = window.setTimeout(() => {
              if (shouldHibernateRef.current) {
                void triggerHibernate("response-fallback");
                shouldHibernateRef.current = false;
              }
            }, 1200);
          }
          const cleanText = msg.text.replace("[ACTION:HIBERNATE]", "").trim();
          if (cleanText) {
            const sentences = cleanText.split(/[.!?]+\s*/).filter(Boolean).length;
            if (sentences > 1) {
              void showTextWindow(cleanText);
            } else {
              showToast(cleanText);
            }
          }
        }
        break;

      case "audio_response":
        if (msg.command_id && currentCommandIdRef.current && msg.command_id !== currentCommandIdRef.current) {
          console.log(`Ignoring stale audio_response for ${msg.command_id}`);
          break;
        }
        if (ignoreResponseRef.current) {
          ignoreResponseRef.current = false;
          setOrbState("idle");
          break;
        }

        if (typewriterRef.current) {
          clearInterval(typewriterRef.current);
          typewriterRef.current = null;
        }
        if (islandTimerRef.current) {
          clearTimeout(islandTimerRef.current);
          islandTimerRef.current = null;
        }
        pendingHideRef.current = false;
        writingCompleteRef.current = true;

        if (hibernateTimerRef.current) {
          clearTimeout(hibernateTimerRef.current);
          hibernateTimerRef.current = null;
        }

        const isHealth = (msg as any).metadata?.type === "health_alert";

        // Web Autopilot state hooks
        if ((msg as any).metadata?.status === "file_saved") {
          lastResearchFileRef.current = (msg as any).metadata.file_path;
        }
        if ((msg as any).metadata?.status === "bot_detected") {
          botBypassUrlRef.current = (msg as any).metadata.url;
        }

        if (msg.audio) {
          playAudio(msg.audio, shouldHibernateRef.current, isHealth);
          shouldHibernateRef.current = false;
        } else {
          pendingHideRef.current = false;
          writingCompleteRef.current = true;
          setOrbState("idle");
          
          if (islandTimerRef.current) clearTimeout(islandTimerRef.current);
          islandTimerRef.current = window.setTimeout(() => {
            void hideTextWindow();
          }, 4000);

          if (shouldHibernateRef.current) {
            void triggerHibernate("no-audio");
            shouldHibernateRef.current = false;
          }
        }
        break;

      case "error":
        showError(msg.message || "Something went wrong");
        break;

      default:
        break;
    }
  }, [showToast, showError, playAudio, triggerHibernate, showTextWindow, handleVoiceCommandInterpretation]);

  const handleStatusChange = useCallback((status: BackendStatus) => {
    if (status === "connected") {
      setOrbState(prev => prev === "offline" ? "idle" : prev);
    } else if (status === "offline" || status === "disconnected") {
      mainWindowRef.current.isVisible().then(visible => {
        if (visible) setOrbState("offline");
      });
    }
  }, []);

  const { send } = useBackend({ onMessage: handleBackendMessage, onStatusChange: handleStatusChange });

  // Update send ref for follow-up handler
  const sendRef = useRef(send);
  sendRef.current = send;

  // ── Force stop ──
  const handleForceStop = useCallback(async () => {
    try {
      await fetch("http://localhost:8000/api/stop", { method: "POST" });
    } catch (err) {
      console.warn("Backend /api/stop unreachable:", err);
    }
    void hideTextWindow(true);
    send({ type: "abort", command_id: currentCommandIdRef.current });
    stopSpeaking();
    setOrbState("idle");
    showToast("🛑 Terminated");
  }, [stopSpeaking, showToast, send, hideTextWindow]);

  // ── Voice pipeline ──
  const handleSpeechStart = useCallback(() => {
    if (Date.now() - lastSpeechEndRef.current < 800) {
      console.log("[App] VAD Speech ignored (cooldown)");
      return;
    }
    console.log("[App] VAD Speech started. Interrupting...");
    void hideTextWindow(true);
    stopSpeaking();
    if (currentCommandIdRef.current) {
      send({ type: "abort", command_id: currentCommandIdRef.current });
    }
    setOrbState("listening");
  }, [send, stopSpeaking, hideTextWindow]);

  const handleAudioReady = useCallback((base64: string) => {
    lastSpeechEndRef.current = Date.now();
    const commandId = `cmd_${Date.now()}`;
    currentCommandIdRef.current = commandId;
    setOrbState("processing");
    if (!send({ type: "voice", audio: base64, command_id: commandId, timestamp: Date.now() })) {
      showError("Backend not connected");
      setOrbState("idle");
    }
  }, [send, showError]);

  const { isRecording, startRecording, stopRecording, startContinuousListening, stopContinuousListening, updateJarvisState } = useVoice({
    onAudioReady: handleAudioReady,
    onError: showError,
    onSpeechStart: handleSpeechStart,
  });

  useEffect(() => {
    updateJarvisState(orbState);
  }, [orbState, updateJarvisState]);

  useEffect(() => {
    if (continuousListening) {
      startContinuousListening(orbState).catch(console.error);
    } else {
      stopContinuousListening();
    }
  }, [continuousListening, startContinuousListening, stopContinuousListening, orbState]);

  useEffect(() => {
    if (continuousListening && orbState === "idle") {
      setOrbState("listening");
    }
  }, [continuousListening, orbState]);

  // ── Click handlers ──
  const handleSingleClick = useCallback(() => {
    const currentState = orbStateRef.current;

    if (currentState === "speaking" || currentState === "processing") {
      handleForceStop();
      return;
    }
    if (currentState === "offline") {
      showError("Backend offline");
      return;
    }
    if (continuousListening) {
      return; // VAD handles everything in continuous mode
    }
    if (isRecording) {
      stopRecording();
      setOrbState("processing");
      return;
    }
    startRecording();
    setOrbState("listening");
  }, [isRecording, continuousListening, startRecording, stopRecording, handleForceStop, showError]);

  const handleOpenFrontend = useCallback(async () => {
    try {
      await openUrl("http://localhost:5173");
    } catch (err) {
      console.error("Failed to open frontend:", err);
      showError("Could not open frontend");
    }
  }, [showError]);

  // ── Window position management ──
  useEffect(() => {
    const restorePosition = async () => {
      const saved = localStorage.getItem("max-window-pos");
      if (!saved) return;
      try {
        const { x, y } = JSON.parse(saved) as { x: number; y: number };
        if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("Invalid position");
        const [size, monitors] = await Promise.all([
          mainWindowRef.current.outerSize(),
          availableMonitors(),
        ]);
        const isOnScreen = monitors.some((monitor) => {
          const minX = monitor.position.x;
          const minY = monitor.position.y;
          const maxX = monitor.position.x + monitor.size.width - size.width;
          const maxY = monitor.position.y + monitor.size.height - size.height;
          return x >= minX && x <= maxX && y >= minY && y <= maxY;
        });
        if (!isOnScreen) {
          localStorage.removeItem("max-window-pos");
          return;
        }
        await mainWindowRef.current.setPosition(new PhysicalPosition(x, y));
      } catch {
        localStorage.removeItem("max-window-pos");
      }
    };
    void restorePosition();
  }, []);

  useEffect(() => {
    const unlistenPromise = listen("tauri://move", () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(async () => {
        try {
          const pos = await mainWindowRef.current.outerPosition();
          localStorage.setItem("max-window-pos", JSON.stringify({ x: pos.x, y: pos.y }));
        } catch {}
      }, POSITION_SAVE_DEBOUNCE_MS);
    });
    return () => {
      unlistenPromise.then(fn => fn());
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const unlistenPromise = listen("toggle-listening", async () => {
      await mainWindowRef.current.show();
      await mainWindowRef.current.setFocus();
      handleSingleClick();
    });
    return () => { unlistenPromise.then(fn => fn()); };
  }, [handleSingleClick]);

  // ── Pointer/drag handlers ──
  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    pointerDownTime.current = Date.now();
    dragStartedRef.current = false;
    dragTimerRef.current = window.setTimeout(async () => {
      try {
        await mainWindowRef.current.startDragging();
        dragStartedRef.current = true;
      } catch (err) {
        console.error("startDragging failed:", err);
      }
    }, DRAG_THRESHOLD_MS);
  }, []);

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    if (dragTimerRef.current !== null) {
      clearTimeout(dragTimerRef.current);
      dragTimerRef.current = null;
    }
    if (dragStartedRef.current) {
      dragStartedRef.current = false;
      return;
    }
    clickCountRef.current += 1;
    if (clickCountRef.current === 1) {
      clickTimerRef.current = window.setTimeout(() => {
        clickCountRef.current = 0;
        handleSingleClick();
      }, DBLCLICK_DELAY_MS);
    } else if (clickCountRef.current >= 2) {
      if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
      clickCountRef.current = 0;
      handleOpenFrontend();
    }
  }, [handleSingleClick, handleOpenFrontend]);

  // ── Cleanup ──
  useEffect(() => {
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
      if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
      if (dragTimerRef.current) clearTimeout(dragTimerRef.current);
      if (hibernateTimerRef.current) clearTimeout(hibernateTimerRef.current);
      if (islandTimerRef.current) clearTimeout(islandTimerRef.current);
      if (typewriterRef.current) clearInterval(typewriterRef.current);
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.src = "";
      }
    };
  }, []);

  return (
    <div className={`orb-stage ${isOrbHidden ? "orb-hidden" : ""}`}>
      <div
        className={`circle-icon ${orbState}`}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        role="button"
        aria-label="MAX orb"
      >
        <div className="orb-core" />
        <div className="orb-shell" />
        <div className="orb-ring" />
        <div className="orb-particles" />
      </div>

      {toastText && (
        <div className="toast">{toastText}</div>
      )}
      {errorMsg && (
        <div className="toast error-toast">{errorMsg}</div>
      )}
    </div>
  );
};

export default App;
