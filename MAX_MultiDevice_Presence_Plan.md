# MAX Multi-Device Presence Switching — System Design (v1.0)

**Scope:** Chat-only continuity (voice + text) across laptop and phone. NO device-control actions execute from a non-primary/phone session — this is a hard security boundary, not just a UI limitation. Laptop is always the brain (LLM calls, memory, conversation state, STT/TTS processing). Phone is always a thin client (mic capture + audio playback + UI render only).

**Explicit non-goals for v1:** Auto-detection of device switching (proximity/lock-based triggers) is parked for v2 — false-positive risk is too high to build now. v1 is explicit command-triggered only ("phone pe aa ja" / "laptop pe wapas jao"), which is reliable and simple.

---

## 1. Why Context Never Gets Lost (Core Principle)

Conversation state, memory, and all processing live ONLY on the laptop server — they never move. "Switching devices" is never a data transfer; it is only a change in **which device is currently the audio/UI endpoint**. This is what makes the switch instant, cheap, and impossible to get out of sync.

---

## 2. Architecture Overview

```
┌─────────────────────────┐                          ┌──────────────────────────┐
│   LAPTOP (Server/Brain)  │                          │   PHONE (Thin Client)     │
│  - FastAPI + WebSocket   │◄──── Persistent WSS ────►│  - Tauri 2.0 mobile app   │
│  - LLM / Memory / STT/TTS│      (always connected,   │   (same React codebase   │
│  - Blackboard/Orchestrator│      heartbeat-monitored) │    as laptop frontend)   │
│  - active_device flag    │                           │  - Mic capture (VAD-gated)│
│  - mDNS service advertise│                           │  - Audio playback + UI    │
└─────────────────────────┘                          └──────────────────────────┘
```

- **One codebase, two builds** — the phone app is a Tauri mobile build of the existing React frontend. No separate app to design/maintain.
- **Hotspot scenario (confirmed use case):** phone provides hotspot to laptop → both land on the same local network automatically → no external tunnel, no port-forwarding, no public exposure needed or wanted.

---

## 3. One-Time Pairing (Setup Happens Once, Never Again)

1. On first run, laptop MAX UI displays a QR code containing: a service name to resolve (`max-server.local`) + a randomly generated long-lived pairing secret.
2. Phone app scans the QR code once. Pairing secret is stored in the phone's OS-level secure storage (Android Keystore / iOS Keychain) — never stored in plaintext, never re-entered manually again.
3. Laptop stores its own copy of the secret in its local secure credential store (OS keychain equivalent, or a permission-locked-down local file as fallback).

This single scan is the ONLY manual setup step, ever — satisfies "no repeated setup" requirement.

---

## 4. Automatic Discovery (Solves the "IP Changes Every Hotspot Connection" Problem)

**Root cause of the problem:** Every time the phone's hotspot restarts or laptop reconnects, the laptop gets a new local IP. Hardcoding an IP would break every time.

**Fix — mDNS (zero-config networking), not manual IP entry:**
- Laptop server advertises itself on the local network as `max-server.local` using `python-zeroconf`.
- Phone app resolves `max-server.local` automatically the moment it's on the same network — no IP ever typed or stored.
- **Fallback (only if mDNS is blocked by a hotspot's multicast restrictions, which does happen on some Android hotspots):** a lightweight UDP broadcast discovery ping as backup, triggered only if mDNS resolution fails after a short timeout.

---

## 5. Persistent Connection & Instant Switch Protocol

- Phone app opens ONE persistent authenticated WebSocket to the laptop as soon as it's discovered, and keeps it alive with a lightweight heartbeat (small ping/pong, not continuous data — battery-friendly).
- Server tracks a single `active_device` flag per conversation session: `laptop` or `phone`.
- **Switch flow:**
  1. User says "Max, phone pe aa ja" (on laptop, currently active).
  2. Server flips `active_device = phone` and sends a `SWITCH_ACTIVE` event down the already-open phone WebSocket.
  3. Phone app instantly activates its mic-capture UI and audio playback; laptop UI shows "active on phone" and pauses its own mic capture.
  4. No reconnect, no re-authentication, no data migration — the switch is just a flag flip + one event, which is why it's instant.
- Reverse works identically ("laptop pe wapas jao" from phone).
- If the persistent WebSocket drops (e.g., brief hotspot hiccup), phone app auto-reconnects with exponential backoff and re-resolves `max-server.local` if needed — user never has to manually reconnect.

---

## 6. Security Design (Root-Cause Hardening, Not Surface Patches)

| Risk | Fix |
|---|---|
| Server accidentally exposed beyond LAN | Bind server socket ONLY to the local/hotspot network interface, never `0.0.0.0` with any public-facing rule. No ngrok/tunnel/port-forward — explicitly not needed since hotspot already puts both devices on one LAN. |
| Pairing secret theft via plaintext storage | Stored only in OS-level secure storage (Keystore/Keychain) on phone; equivalent secure store or permission-locked file on laptop. |
| Man-in-the-middle on shared hotspot | All traffic over TLS (`wss://`) even though it's LAN-only. Self-signed certificate's fingerprint is pinned to the phone during the one-time QR pairing step — phone will refuse to connect to any server presenting a different certificate, which blocks a rogue device on the same hotspot from impersonating the laptop. |
| Passive sniffing of the pairing secret over the network | Never transmit the raw secret on every connect. Use an HMAC challenge-response handshake derived from the secret instead — the secret itself never travels on the wire after initial pairing. |
| Brute-forcing the pairing/handshake | Rate-limit and temporarily lock out repeated failed handshake attempts from the server side. |
| Phone session being used to trigger real device actions (the explicit "chat-only" requirement) | This must be enforced **server-side**, not just hidden in the phone UI. Any session flagged `active_device = phone` must have its requests hard-rejected by the skill-execution layer (`skills.py`/Orchestrator dispatch) if they attempt to trigger `[SKILL:...]` actions — treat it as a permissions boundary, not a UI convenience, so it can't be bypassed by a modified/custom client. |

---

## 7. Performance & Resource-Friendliness

- Persistent WebSocket + heartbeat is a negligible, low-frequency traffic pattern — does not meaningfully drain phone battery.
- Audio is only streamed while actively listening, gated by the existing VAD module already used in MAX's stack — reuse it, do not build a second one. No continuous raw audio dump over the network.
- mDNS discovery only runs on initial connect or after a connection-loss event — it is not a constant polling loop.
- All heavy compute (LLM inference calls, ChromaDB/memory lookups, STT, TTS synthesis) stays on the laptop. The phone app never needs to be powerful — it is intentionally kept to capture, playback, and render only, which is what makes a Tauri-mobile thin client viable on any phone.

---

## 8. Tech Stack Summary

| Component | Choice | Why |
|---|---|---|
| Mobile app | Tauri 2.0 mobile build | Reuses existing React frontend, single codebase, no separate app to maintain |
| Discovery | `python-zeroconf` (server) + native mDNS resolution (client) | Solves changing-IP problem with zero manual config |
| Transport | WebSocket over TLS (`wss://`), self-signed cert pinned at pairing | Encrypts LAN traffic, blocks MITM on shared hotspot |
| Auth | One-time QR pairing + HMAC challenge-response handshake | No manual re-entry ever, no secret transmitted in plaintext on reconnect |
| Audio | Existing VAD module (reused, not rebuilt) | Keeps phone-side traffic and battery use minimal |

---

## 9. Open Item for v2 (Not Built Now)

Auto-detection of device switching (e.g., laptop lock event, Bluetooth proximity change) is a real "true Jarvis" enhancement but carries meaningful false-trigger risk (switching at the wrong moment mid-conversation). Explicit command-based switching (v1, this document) should be built, tested, and used for a while first — auto-detection should only be revisited once v1's reliability is proven and a low-false-positive trigger signal is identified.

---

*End of document. This is a self-contained feature design, independent of the Orchestrator/Blackboard/Deep Research plan. It can be implemented in parallel or sequence with that work — there is no dependency between them.*
