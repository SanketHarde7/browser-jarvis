// Path: max-desktop/src/hooks/useBackend.ts
// Use: Connects Tauri front-end to Rust commands.
/**
 * useBackend.ts — MAX v4.3
 * WebSocket connection manager for MAX backend.
 *
 * WHY WEBSOCKET over REST:
 *   main.py was designed around WS. It streams three separate events per request:
 *   transcript → response_text → audio_response
 *   REST /api/voice returns all three in one call (blocking), WS is faster UX.
 *
 * CONNECTION STRATEGY:
 *   Exponential backoff reconnect: 1s → 2s → 4s → ... → 30s max
 *   On fresh connect: auto-sends request_greeting (backend sends welcome audio)
 *   Status: connecting | connected | disconnected | offline
 *
 * AUDIO FORMAT:
 *   Backend sends RAW base64 (no data URI prefix).
 *   Caller must prepend: "data:audio/mp3;base64," before playing.
 */

import { useEffect, useRef, useCallback } from "react";

const WS_AUTH_TOKEN = import.meta.env.VITE_WS_AUTH_TOKEN || "";
const WS_URL  = `ws://127.0.0.1:8000/ws?token=${WS_AUTH_TOKEN}&device=laptop`;
const MAX_RECONNECT_DELAY_MS = 30_000;

export type BackendStatus = "connecting" | "connected" | "disconnected" | "offline";

export interface BackendMessage {
  event:      string;            // "greeting" | "transcript" | "response_text" | "audio_response" | "error" | "pong" | "start_continuous_listening" | "stop_continuous_listening" | "stale_discard"
  text?:      string;
  audio?:     string;            // RAW base64 — prepend "data:audio/mp3;base64,"
  skill_used?: string | null;
  message?:   string;
  command_id?: string;
}

interface UseBackendOptions {
  onMessage:      (msg: BackendMessage) => void;
  onStatusChange: (status: BackendStatus) => void;
}

export function useBackend({ onMessage, onStatusChange }: UseBackendOptions) {
  const wsRef              = useRef<WebSocket | null>(null);
  const reconnectTimerRef  = useRef<number | null>(null);
  const reconnectDelayRef  = useRef(1_000);
  const pingIntervalRef    = useRef<number | null>(null);
  const mountedRef         = useRef(true);
  // Stable refs so connect() closure never goes stale
  const onMessageRef       = useRef(onMessage);
  const onStatusChangeRef  = useRef(onStatusChange);
  onMessageRef.current      = onMessage;
  onStatusChangeRef.current = onStatusChange;

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    // Don't double-connect
    if (wsRef.current?.readyState === WebSocket.CONNECTING ||
        wsRef.current?.readyState === WebSocket.OPEN) return;

    onStatusChangeRef.current("connecting");
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      reconnectDelayRef.current = 1_000; // reset backoff on success
      onStatusChangeRef.current("connected");
      // Backend expects explicit greeting request — not sent automatically
      ws.send(JSON.stringify({ type: "request_greeting" }));

      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try {
            ws.send(JSON.stringify({ type: "ping" }));
          } catch {
            // ignore send errors; onclose/onerror will handle state
          }
        }
      }, 25_000);
    };

    ws.onmessage = (e) => {
      if (!mountedRef.current) return;
      try {
        const msg = JSON.parse(e.data) as BackendMessage;
        onMessageRef.current(msg);
      } catch {
        // Ignore malformed frames
      }
    };

    ws.onclose = (event) => {
      if (!mountedRef.current) return;
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = null;
      }
      onStatusChangeRef.current("disconnected");
      if (event && (event.code || event.reason)) {
        console.warn(`WS closed: code=${event.code} reason='${event.reason || ""}'`);
      }
      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY_MS);
      reconnectTimerRef.current = window.setTimeout(connect, delay);
    };

    ws.onerror = () => {
      // onerror always precedes onclose — just mark offline here
      if (!mountedRef.current) return;
      console.warn("WS error");
      onStatusChangeRef.current("offline");
    };
  }, []); // no deps — uses refs only

  const send = useCallback((msg: object): boolean => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify(msg));
        return true;
      } catch {
        onStatusChangeRef.current("offline");
        return false;
      }
    }
    return false;
  }, []);

  const ping = useCallback(() => {
    send({ type: "ping" });
  }, [send]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { send, ping };
}
