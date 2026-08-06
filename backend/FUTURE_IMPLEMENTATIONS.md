# MAX Backend — Future Implementation & Kept Functions Backlog

This document tracks functions/features identified during backend audit that are currently unused/unhooked but saved for future implementation.

---

## 1. Memory Module (`modules/memory.py`)
* **Function**: `get_user_fact(self, key: str, default=None)`
* **Location**: `backend/modules/memory.py:L209`
* **Status**: Kept for future key-based user fact lookup.
* **Implementation Note**: Can be used when skills or agents need to retrieve a specific user fact by key (e.g. `memory.get_user_fact("user_name")`).

---

## 2. Resource Manager & Sub-Agents (`modules/resource_manager.py` & `modules/agents/*`)
* **Status**: Saved (Do not delete).
* **Implementation Note**: Sub-agents, orchestrator, and concurrency ceiling logic.

---

## 3. Browser Agent (`modules/browser_agent.py`)
* **Function**: `get_current_url(self) -> str`
* **Location**: `backend/modules/browser_agent.py:L240`
* **Status**: Kept for future active URL checks in browser automation.
* **Implementation Note**: Returns `self._driver.current_url`. Can be used when web autopilot or browser skills need to verify the active tab URL.

---

## 4. Web Autopilot (`modules/web_autopilot.py`)
* **Function**: `async def resolve_accurate_url(self, query: str) -> str`
* **Location**: `backend/modules/web_autopilot.py:L336`
* **Status**: Kept for future async smart URL resolution.
* **Implementation Note**: Resolves natural search intent queries (e.g. "open python docs") into accurate URLs (e.g. "https://docs.python.org") asynchronously without blocking main event loop.

---

## 5. Cloud Tunneling for Mobile Connectivity (`main.py` / Cloudflare Tunnel / Ngrok)
* **Feature**: Cloudflare Tunnel / Ngrok Integration for persistent Mobile App connection.
* **Status**: Selected architecture choice for future remote & local Wi-Fi connectivity.
* **Implementation Note**: Will generate a secure persistent WebSocket domain (e.g. `wss://max-assistant.trycloudflare.com`) so `max-mobile` connects seamlessly across any Wi-Fi or 4G/5G mobile data without manual IP updates.
