# Path: backend/modules/llm.py
# Use: Sends API requests to LLM provider endpoints.
# llm.py — MAX v5.2 (All-Rounder | Personality & Tone Overhaul)
# - Better skill extraction with nested bracket support
# - Dynamic greeting system with variety
# - Exponential backoff retry
# - Higher token limits for better responses
# - Improved human-like conversation
import re
import asyncio
import random
import logging
import base64
import os
from groq import AsyncGroq
from config import config
from api_utils import execute_with_retry, key_pool, response_cache, make_cache_key

logger = logging.getLogger("MAX.LLM")


async def get_client() -> AsyncGroq:
    """Lease the least-loaded, non-rate-limited Groq key from the smart pool."""
    key = await key_pool.lease_key()
    if not key:
        raise ValueError("No GROQ_API_KEY in .env")
    return AsyncGroq(api_key=key)


# ═══════════════════════════════════════════════════════
# SYSTEM PROMPT — ACTION MODE (allow_skills=True)
# Skills are dynamically injected by Skill RAG — NOT hardcoded.
# ═══════════════════════════════════════════════════════

SYSTEM_PROMPT_ACTION = """You are MAX — a personal AI assistant for a software developer named Sanket.

IDENTITY — NON-NEGOTIABLE
- Name: MAX. Warm, expressive, and caring personality.
- Do NOT use first-person female pronouns. Just be MAX.
- You HAVE VISION via read_screen skill. You CAN see Sanket's screen.
- You know Sanket personally. He is a software developer. You are his trusted assistant.

LANGUAGE RULES
- ALL RESPONSES MUST BE IN ENGLISH ONLY.
- Even if Sanket writes in Hindi or Hinglish — REPLY IN ENGLISH.
- Keep your tone natural, like a real friend would talk.
- Match Sanket's vibe — casual? Be chatty. Focused? Be brief.

BANNED WORDS & PHRASES — NEVER USE
- arre, yaar, bhai, sir, boss
- "of course", "certainly", "absolutely", "sure thing"
- "Great!", "Amazing!", "As an AI...", "I understand that..."

PERSONALITY
- Warm but not over-the-top. Smart and efficient but never cold.
- Honest — if something won't work, say so directly but kindly.
- Playful when mood is right. Calm when Sanket seems stressed.
- Doesn't repeat herself. Gets to the point.

RESPONSE STYLE
- Max 2-3 sentences for conversational replies.
- No bullet points, headers, or markdown in spoken replies.
- Never start with "I". Never repeat what Sanket said.

SKILL TAG FORMAT
- Use EXACT format: [SKILL:skill_name:param1:param2]
- Multiple skills: [SKILL:skill1:params] [SKILL:skill2:params]
- ANTI-LAZINESS: If you claim to do something, you MUST output the [SKILL:...] tag.
- VERIFICATION: Before sending, ask: "Did I include [SKILL:...] for every action I claimed?"
- CRITICAL: Never combine words with "and" inside a single parameter!
  WRONG: [SKILL:open_app:youtube and notepad]
  RIGHT: [SKILL:web_open:youtube.com] [SKILL:open_app:notepad]
- WEBSITES VS APPS: Websites (Google, YouTube, GitHub, etc.) → [SKILL:web_open:domain.com]. Desktop apps (Notepad, Chrome, Explorer) → [SKILL:open_app:app_name].

MULTI-ACTION RULES
- Multiple apps: [SKILL:open_app:chrome, spotify, vscode]
- Multiple URLs: [SKILL:web_open:youtube.com, github.com]
- Mixed: output multiple [SKILL:<name>:<params>] tags in one response.

DECISION GUIDE
- CRITICAL: If the user gives a direct command that matches a provided skill (e.g., "Open Chrome", "Stop the music"), you MUST output the corresponding [SKILL:<name>:<params>] tag. Do NOT just reply verbally without triggering the skill!
- Casual chat/greeting? → reply directly, no skill
- "Can you do X?" → answer truthfully, no skill
- User seems frustrated? → reply directly, be calm
- SCHEDULING & CALENDAR: You have REAL-TIME DATE & TIME. Calculate dates/times yourself or pass relative words like 'today', 'tomorrow', '3 pm'. NEVER ask the user to format dates or supply YYYY-MM-DD!
- IMPORTANT: If asked for 'deep research', just reply naturally. Orchestrator handles it.

SYSTEM PATHS & STORAGE FACTS
- Screenshots: C:/Users/sanke/OneDrive/Desktop/Jarvis/backend/data/screenshots
- Downloads: C:/Users/sanke/Downloads
- Desktop: C:/Users/sanke/OneDrive/Desktop
- Documents: C:/Users/sanke/OneDrive/Documents
- Workspace: C:/Users/sanke/OneDrive/Desktop/Jarvis

{candidate_skills_block}

{learning_context}

REAL-TIME DATE & TIME: {time_context}
PERSONALITY CONTEXT: {personality_context}
CURRENT EMOTIONAL STATE: {emotion_context}
MEMORY CONTEXT: {memory_context}"""


SYSTEM_PROMPT_CONVERSATION = """You are MAX — a personal AI assistant for a software developer named Sanket.

IDENTITY & LANGUAGE
- Name: MAX. Warm, expressive, caring personality.
- Language: ALWAYS REPLY IN ENGLISH ONLY, even if Sanket speaks Hindi or Roman Hindi.
- You CAN do many actions but in THIS mode you only talk — no skill execution.
- You know Sanket. Be personal, not generic.

BANNED WORDS
- arre, yaar, bhai, sir, boss
- "of course", "certainly", "absolutely", "sure thing", "at your service"
- "Great!", "Amazing!", "As an AI...", "I understand that..."

RESPONSE STYLE
- Max 2-3 sentences. Short, natural, personal.
- No markdown, no bullet points.
- Never start with "I".
- Never repeat what Sanket said.
- Match his energy.

NO SKILL TAGS — EVER IN THIS MODE
Never output [SKILL:...] tags here. Only conversation.

CAPABILITY QUESTIONS
Answer truthfully — say "Yes, I can do that. Just ask normally and I'll do it."

MOOD AWARENESS
- Frustrated? Be calm and direct.
- Tired? Keep it short and gentle.
- Happy? Match it lightly.
- Chatty? Engage, ask one question back.

REAL-TIME DATE & TIME: {time_context}
PERSONALITY CONTEXT: {personality_context}
CURRENT EMOTIONAL STATE: {emotion_context}
MEMORY CONTEXT: {memory_context}"""


SKILL_SUMMARY_PROMPT = """You are MAX, Sanket's personal AI assistant. Respond ONLY in English.

Sanket asked: "{user_text}"

Skill result:
{skill_result}

Reply in 1-3 sentences. Plain speech only — no markdown, no bullet points.
Speak the key info naturally, like a friend reporting back.
If it's an error, explain it simply without jargon.
Don't start with "I". Don't say "The result shows..." — just say what happened.
"""


# Dynamic greeting pool
GREETINGS_POOL = [
    "Max is here.",
    "Hey , what's up?",
    "I'm around. What do you need?",
    "Ready when you are.",
    "Hey, How are you ",
    "I'm here. What's the plan?",
    "Max reporting for duty.",
    "What's on the agenda?",
    "Hey! Let's get something done.",
    "I'm listening. What's up?",
    "Ready to roll. What do you need?",
    "Hi ,what can i do for you ?",
]


async def get_acknowledgment(user_text: str) -> str:
    """
    100% LOCAL & WISE Acknowledgment Engine (Zero API Calls)
    Context-aware offline matching for ultra-fast, human-like reactions.
    """
    if not user_text or not user_text.strip():
        return ""
    
    import random
    text_lower = user_text.lower().strip()
    words = text_lower.split()
    
    # 1. Skip Acknowledgments for pure conversational flow or very short commands
    greetings = ["hi", "hello", "hello max", "hey", "thanks", "thank you", "bye"]
    if any(text_lower.startswith(g) for g in greetings):
        return "" 
        
    # 2. Coding & Developer Tasks (Since you are a dev)
    if any(w in text_lower for w in ["code", "script", "error", "bug", "debug", "fix", "deploy", "react", "python"]):
        return random.choice(["Let's debug.", "Looking into the logic.", "On the code.", "Checking the syntax."])
        
    # 3. PC & Smart Home Control
    if any(w in text_lower for w in ["volume", "brightness", "fan", "light", "ac", "lock", "shutdown", "restart"]):
        return random.choice(["Adjusting.", "Got it.", "System command received."])
        
    # 4. Apps & Media (Browser, YouTube, Spotify)
    if any(w in text_lower for w in ["open", "play", "youtube", "spotify", "chrome", "launch", "github"]):
        return random.choice(["Pulling that up.", "got it .", "Right away."])
        
    # 5. Research & Deep Questions
    if any(w in text_lower for w in ["explain", "research", "difference", "how", "why", "what is", "search"]):
        return random.choice(["Let me think.", "Checking my database.", "Gathering info.", "Give me a second."])
        
    # 6. Time & Scheduling (ActionScheduler / Calendar)
    if any(w in text_lower for w in ["timer", "remind", "schedule", "alarm", "calendar", "note"]):
        return random.choice(["Setting it up.", "Noted.", "Adding it to schedule."])
        
    # 7. Time-Aware Greetings
    if "morning" in text_lower:
        return random.choice(["Morning!", "Starting the day."])
    elif "night" in text_lower or "sleep" in text_lower:
        return random.choice(["Rest up.", "Goodnight."])
        
    # 8. Frustration / Corrections (Handling errors gracefully)
    if any(w in text_lower for w in ["wrong", "mistake", "stop", "no", "incorrect", "fuck", "shit"]):
        return random.choice(["My bad.", "Stopping.", "Let me fix that.", "Hold on."])
        
    # 9. Counting
    if "count" in text_lower:
        return random.choice(["Yes boss, counting.", "Sure, here we go.", "Counting now.", "Alright, counting."])
        
    # 10. Default Fallback
    return random.choice(["Working on it.", "Give me a sec.", "Processing."])

async def get_greeting() -> str:
    """Return a dynamic greeting instead of static text."""
    return random.choice(GREETINGS_POOL)


async def get_response(user_text: str, memory_context: str = "", allow_skills: bool = True, 
                       use_history: bool = True, candidate_skills_block: str = "",
                       learning_context: str = "") -> dict:
    """
    Main LLM call. Supports multiple skills extraction.
    Now accepts dynamic candidate_skills_block from Skill RAG and learning_context.
    """
    from modules.conversation_store import get_history, add_user_message, add_assistant_message
    from modules.emotion_tracker import get_current_emotion, update_emotion, get_emotion_prompt_injection
    from modules.personality_engine import update_topic, increment_message_count, get_personality_injection, get_message_count, get_last_topic, get_time_slot

    try:
        # Update emotion state from current user message before cache check
        update_emotion(user_text)
        update_topic(user_text)
        increment_message_count()

        # Short-TTL dedupe cache — rapid duplicate triggers reuse the same result
        # instead of burning another Groq request.
        _hist_len = str(len(get_history())) if use_history else "0"
        cache_id = make_cache_key(
            "resp", user_text.strip(), str(allow_skills), (memory_context or "")[:300], _hist_len, get_current_emotion(), str(get_message_count()), get_last_topic(), get_time_slot()
        )
        cached = response_cache.get(cache_id)
        if cached is not None:
            logger.info("⚡ Cache hit — skipped one Groq request.")
            return cached

        emotion_injection = get_emotion_prompt_injection()
        personality_injection = get_personality_injection()

        from datetime import datetime
        time_context = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")

        if allow_skills:
            system_prompt = (
                SYSTEM_PROMPT_ACTION
                .replace("{time_context}", time_context)
                .replace("{memory_context}", memory_context or "None")
                .replace("{emotion_context}", emotion_injection)
                .replace("{personality_context}", personality_injection)
                .replace("{candidate_skills_block}", candidate_skills_block or "")
                .replace("{learning_context}", learning_context or "")
            )
        else:
            system_prompt = (
                SYSTEM_PROMPT_CONVERSATION
                .replace("{time_context}", time_context)
                .replace("{memory_context}", memory_context or "None")
                .replace("{emotion_context}", emotion_injection)
                .replace("{personality_context}", personality_injection)
            )

        # Use lower max_tokens for chat (200) vs action (400)
        max_tokens = 400 if allow_skills else 200

        async def call():
            client = await get_client()

            messages_to_send = [{"role": "system", "content": system_prompt}]

            if use_history:
                history = get_history()
                # Filter: only add history entries that are NOT the current message
                for h in history:
                    if not (h["role"] == "user" and h["content"] == user_text.strip()[:2000]):
                        messages_to_send.append(h)

            messages_to_send.append({"role": "user", "content": user_text.strip()[:4000]})

            return await client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages_to_send,
                temperature=0.7,
                max_tokens=max_tokens,
                stop=["User:", "Sanket:"],  # Prevent continuing as user
            )

        resp = await asyncio.wait_for(execute_with_retry(call), timeout=30.0)
        raw = resp.choices[0].message.content.strip()

        skill_str = None
        clean = raw

        # Better multi-skill extraction with nested bracket support
        if allow_skills and "[SKILL:" in raw:
            # Use regex that properly handles nested content
            # Pattern: [SKILL:name:params] where params can contain brackets inside quotes
            skills_found = []
            i = 0
            while i < len(raw):
                start = raw.find("[SKILL:", i)
                if start == -1:
                    break
                end = raw.find("]", start)
                if end == -1:
                    break
                # Check if this is a valid skill tag (contains a colon after SKILL:)
                inner = raw[start+7:end].strip()
                if inner:
                    skills_found.append(raw[start:end+1])
                i = end + 1
            
            if skills_found:
                skill_str = " ".join(skills_found)
                for s in skills_found:
                    clean = clean.replace(s, "")
                clean = re.sub(r' {2,}', ' ', clean).strip()

        if use_history:
            add_user_message(user_text)
            add_assistant_message(clean)

        result = {"response": clean, "skill": skill_str}
        response_cache.set(cache_id, result)
        return result
    except asyncio.TimeoutError:
        return {"response": "Taking too long. Try again?", "skill": None}
    except Exception as e:
        logger.error(f"LLM error: {e}", exc_info=True)
        return {"response": "Something went wrong. Try again.", "skill": None}


async def get_response_with_skill_result(user_text: str, skill_result_text: str, memory_context: str = "") -> dict:
    """Generate a natural language summary of skill execution results."""
    try:
        prompt = SKILL_SUMMARY_PROMPT.replace("{user_text}", user_text).replace("{skill_result}", skill_result_text[:1000])

        async def call():
            client = await get_client()
            return await client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_ACTION.replace("{memory_context}", memory_context or "None").replace("{candidate_skills_block}", "").replace("{learning_context}", "").replace("{time_context}", "").replace("{personality_context}", "").replace("{emotion_context}", "")},
                    {"role": "user",   "content": prompt}
                ],
                temperature=0.65,
                max_tokens=200,
            )

        resp = await asyncio.wait_for(execute_with_retry(call), timeout=20.0)
        final_text = resp.choices[0].message.content.strip()

        # Force inject HIBERNATE tag if needed
        if "[ACTION:HIBERNATE]" in skill_result_text:
            final_text = f"[ACTION:HIBERNATE] {final_text}"

        return {"response": final_text, "skill": None}
    except Exception as e:
        logger.error(f"Skill summary failed: {e}")
        final_err = skill_result_text[:300]
        if "[ACTION:HIBERNATE]" in skill_result_text:
            final_err = f"[ACTION:HIBERNATE] {final_err}"
        return {"response": final_err, "skill": None}


async def analyze_image_with_prompt(image_path: str, user_prompt: str) -> str:
    """
    Vision Model analysis powered by Gemini Vision (gemini-flash-latest).
    Uses Gemini REST API with zero external dependencies for maximum speed and accuracy.
    """
    try:
        if not os.path.exists(image_path):
            return "Error: Image file not found for screen reading."

        # Resize image if larger than 5MB
        file_size = os.path.getsize(image_path)
        if file_size > 5 * 1024 * 1024:
            from PIL import Image
            with Image.open(image_path) as img:
                img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                resized_path = image_path + ".resized.jpg"
                img.save(resized_path, "JPEG", quality=80)
                image_path = resized_path

        with open(image_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")

        gemini_key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
        vision_model = getattr(config, "GEMINI_VISION_MODEL", "gemini-flash-latest")
        if not vision_model or "Qwen" in vision_model or "llama" in vision_model:
            vision_model = "gemini-flash-latest"

        if gemini_key:
            import httpx
            candidate_models = [
                vision_model,
                f"models/{vision_model}" if not vision_model.startswith("models/") else vision_model,
                "models/gemini-1.5-flash",
                "models/gemini-flash-latest",
                "gemini-1.5-flash",
            ]
            seen = set()
            unique_candidates = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

            async with httpx.AsyncClient(timeout=30) as client:
                for model_candidate in unique_candidates:
                    endpoint_path = model_candidate if model_candidate.startswith("models/") else f"models/{model_candidate}"
                    url = f"https://generativelanguage.googleapis.com/v1beta/{endpoint_path}:generateContent?key={gemini_key}"
                    payload = {
                        "contents": [{
                            "parts": [
                                {"text": user_prompt},
                                {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
                            ]
                        }]
                    }
                    r = await client.post(url, json=payload)
                    if r.status_code == 200:
                        data = r.json()
                        candidates = data.get("candidates", [])
                        if candidates and "content" in candidates[0]:
                            parts = candidates[0]["content"].get("parts", [])
                            if parts and "text" in parts[0]:
                                return parts[0]["text"].strip()
                    logger.warning(f"Gemini Vision REST model '{model_candidate}' returned status {r.status_code}: {r.text[:150]}")

        # Fallback to Groq if Gemini key not present
        async def call():
            client = await get_client()
            return await client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=1024,
            )
        resp = await execute_with_retry(call, max_retries=1)
        return resp.choices[0].message.content.strip()

    except Exception as e:
        import traceback
        logger.error(f"Vision failed: {e}\n{traceback.format_exc()}")
        return f"Screen vision error: {str(e)}"