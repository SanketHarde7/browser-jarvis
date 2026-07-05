# Path: backend/modules/orchestrator.py
# Use: Dynamic Orchestrator — a Master AI in the browser drives every decision.
#      No hardcoded pipeline. The Master AI issues ACTIONs, the orchestrator executes them.
import asyncio, logging, re, uuid
from typing import Any, Dict, List
from modules.blackboard import blackboard
from modules.agents import search_agent, scrape_agent, master_orchestrator_agent, ai_delegate_agent, writer_agent

logger = logging.getLogger("MAX.ORCHESTRATOR")
DIRECT = "DIRECT"
NEEDS_APPROVAL = "NEEDS_APPROVAL"

_COMPLEX_PATTERNS = [r"\bresearch\b", r"\bdeep research\b", r"\bresearch .* thoroughly\b", r"\bcompare\b.*\b(across|sources|websites)\b", r"\bkeep digging\b", r"\bfull understanding\b", r"\bbuild and test\b", r"\bcomprehensive\b", r"\buntil .* accurate\b", r"\bstudy\b.*\bdeeply\b"]
_DIRECT_PATTERNS = [r"\b(open|close|timer|volume|brightness|screenshot|read screen|play|pause|note|weather|time)\b"]
_APPROVAL_YES = {"yes","deep","orchestrator","go deep","use orchestrator","haan","ha","ok","okay","kar do","approve"}
_APPROVAL_NO = {"no","normal","direct","nahi","mat karo","best effort"}

_pending_approvals: Dict[str, Dict[str, Any]] = {}
_running_tasks: Dict[str, asyncio.Task] = {}

async def classify_complexity(query: str, context: str = "") -> str:
    q = (query or "").lower().strip()
    if any(re.search(p, q) for p in _COMPLEX_PATTERNS): return NEEDS_APPROVAL
    signals = sum(bool(w in q) for w in ["research", "thorough", "multiple", "sources", "verify", "write report", "long", "detailed"])
    if signals >= 2: return NEEDS_APPROVAL
    if any(re.search(p, q) for p in _DIRECT_PATTERNS): return DIRECT
    return DIRECT

def approval_reply_kind(text: str) -> str:
    q=(text or "").lower().strip()
    if any(x in q for x in _APPROVAL_YES): return "yes"
    if any(x in q for x in _APPROVAL_NO): return "no"
    return "unknown"

def remember_pending_approval(query: str, context: str = "") -> str:
    token = str(uuid.uuid4())
    _pending_approvals["latest"] = {"token": token, "query": query, "context": context}
    return token

def pop_pending_approval() -> Dict[str, Any]:
    return _pending_approvals.pop("latest", {})

def get_status_summary(task_id: str = None) -> str:
    if task_id: return blackboard.summarize_task(task_id)
    if _running_tasks:
        return "\n".join(blackboard.summarize_task(tid) for tid in _running_tasks)
    return "No deep Orchestrator task is running right now."

def cancel_task(task_id: str = None) -> str:
    targets=[task_id] if task_id else list(_running_tasks)
    for tid in targets:
        task=_running_tasks.get(tid)
        if task: task.cancel()
    return "Task cancelled, sir."


async def run_orchestrator(query: str, conversation_context: str = "", task_id: str = None, notify_callback=None) -> Dict[str, Any]:
    """
    Dynamic Orchestrator Loop.

    A Master AI (running in a persistent browser tab) decides every move.
    This loop simply:
      1. Asks the Master AI for the next ACTION
      2. Executes it (SEARCH, READ_URL, ASK_AI)
      3. Feeds the result back to the Master AI
      4. Repeats until FINISH
    """
    task_id = task_id or str(uuid.uuid4())
    blackboard.init_task(task_id, {"task_id": task_id, "goal": query, "steps": [], "stopping_condition": "dynamic", "max_iterations_safety_cap": 20})
    _running_tasks[task_id] = asyncio.current_task()
    context = {"goal": query, "conversation_context": conversation_context, "task_id": task_id}

    max_turns = 20  # Safety cap
    collected_evidence: List[Dict[str, Any]] = []  # Fallback data for writer

    try:
        # ── Turn 1: Initialize the Master AI ──
        master_input = {"goal": query, "iteration": 1}
        action_data = await master_orchestrator_agent.run(master_input, context)
        blackboard.create_step(task_id, "master_turn_1", "master_orchestrator_agent", [], {"iteration": 1})
        blackboard.update_step(task_id, "master_turn_1", "success", action_data)

        iteration = 2

        while iteration <= max_turns:
            action = action_data.get("action", "FINISH")
            thinking = action_data.get("thinking", "")
            logger.info(f"🎯 Turn {iteration-1} | Action: {action} | Thinking: {thinking}")

            # ── Execute the ACTION ──
            if action == "FINISH":
                logger.info("🏁 Master AI called FINISH. Starting report generation...")
                break

            elif action == "SEARCH":
                search_query = action_data.get("query", query)
                logger.info(f"🔍 Executing SEARCH: {search_query}")
                step_id = f"search_{iteration}"

                try:
                    result = await search_agent.run({"query": search_query, "max_results": 8}, context)
                    urls = result.get("candidate_urls", [])
                    blackboard.create_step(task_id, step_id, "search_agent", [], {"query": search_query})
                    blackboard.update_step(task_id, step_id, "success", result)

                    if urls:
                        result_text = f"Found {len(urls)} URLs:\n" + "\n".join(f"  {i+1}. {u}" for i, u in enumerate(urls[:8]))
                    else:
                        result_text = "Search returned no results. Try a different query or use ASK_AI."

                    master_input = {
                        "goal": query, "iteration": iteration,
                        "action_type": "SEARCH", "action_result": result_text,
                    }
                except Exception as e:
                    logger.error(f"Search failed: {e}")
                    blackboard.create_step(task_id, step_id, "search_agent", [], {"query": search_query})
                    blackboard.update_step(task_id, step_id, "failed", {}, str(e))

                    master_input = {
                        "goal": query, "iteration": iteration,
                        "action_type": "SEARCH", "action_error": str(e),
                    }

            elif action == "READ_URL":
                url = action_data.get("url", "")
                logger.info(f"📖 Executing READ_URL: {url}")
                step_id = f"read_{iteration}"

                try:
                    result = await scrape_agent.run({"url": url}, context)
                    raw_text = result.get("raw_text", "")
                    blackboard.create_step(task_id, step_id, "scrape_agent", [], {"url": url})
                    blackboard.update_step(task_id, step_id, "success", result)

                    if raw_text:
                        # Store for writer fallback
                        collected_evidence.append({"url": url, "text": raw_text[:3000]})
                        # Send truncated text to Master AI
                        result_text = f"Content from {url} ({len(raw_text)} chars total):\n\n{raw_text[:12000]}"
                    else:
                        result_text = f"Could not extract text from {url}. The page may be blocked or empty."

                    master_input = {
                        "goal": query, "iteration": iteration,
                        "action_type": "READ_URL", "action_result": result_text,
                    }
                except Exception as e:
                    logger.error(f"Read URL failed: {e}")
                    blackboard.create_step(task_id, step_id, "scrape_agent", [], {"url": url})
                    blackboard.update_step(task_id, step_id, "failed", {}, str(e))

                    master_input = {
                        "goal": query, "iteration": iteration,
                        "action_type": "READ_URL", "action_error": f"Failed to read {url}: {e}",
                    }

            elif action == "ASK_AI":
                platform = action_data.get("platform", "perplexity")
                ai_prompt = action_data.get("prompt", query)
                logger.info(f"🤖 Executing ASK_AI on {platform}: {ai_prompt[:80]}...")
                step_id = f"ask_ai_{iteration}"

                try:
                    result = await ai_delegate_agent.run({"platform": platform, "prompt": ai_prompt}, context)
                    blackboard.create_step(task_id, step_id, "ai_delegate_agent", [], {"platform": platform})
                    blackboard.update_step(task_id, step_id, "success", result)

                    if result.get("success"):
                        answer = result.get("answer", "")
                        collected_evidence.append({"url": f"AI:{platform}", "text": answer[:3000]})
                        result_text = f"Answer from {platform.title()}:\n\n{answer[:12000]}"
                    else:
                        result_text = f"ASK_AI failed: {result.get('error', 'unknown error')}"

                    master_input = {
                        "goal": query, "iteration": iteration,
                        "action_type": "ASK_AI", "action_result": result_text,
                    }
                except Exception as e:
                    logger.error(f"ASK_AI failed: {e}")
                    blackboard.create_step(task_id, step_id, "ai_delegate_agent", [], {"platform": platform})
                    blackboard.update_step(task_id, step_id, "failed", {}, str(e))

                    master_input = {
                        "goal": query, "iteration": iteration,
                        "action_type": "ASK_AI", "action_error": str(e),
                    }

            else:
                logger.warning(f"Unknown action: {action}. Asking Master AI to clarify.")
                master_input = {
                    "goal": query, "iteration": iteration,
                    "action_type": action, "action_error": f"Unknown action '{action}'. Use SEARCH, READ_URL, ASK_AI, or FINISH.",
                }

            # ── Ask Master AI for the next ACTION ──
            action_data = await master_orchestrator_agent.run(master_input, context)
            blackboard.create_step(task_id, f"master_turn_{iteration}", "master_orchestrator_agent", [], {"iteration": iteration})
            blackboard.update_step(task_id, f"master_turn_{iteration}", "success", action_data)
            iteration += 1

        # ── FINISH: Generate the report ──
        logger.info("✍️ Triggering Writer Agent for final report...")

        # Build evaluations list from collected evidence for writer fallback
        evaluations = []
        for ev in collected_evidence:
            evaluations.append({
                "url": ev.get("url", "unknown"),
                "is_useful": True,
                "extracted_facts": ev.get("text", ""),
                "relevance_note": "Collected by Master Orchestrator",
            })

        out = await writer_agent.run({"goal": query, "evaluations": evaluations}, context)
        blackboard.create_step(task_id, "writer", "writer_agent", [], {"goal": query})
        blackboard.update_step(task_id, "writer", "success", out)

        result = {"task_id": task_id, "response": out.get("message"), "file_path": out.get("file_path")}

        if notify_callback:
            try:
                await notify_callback(f"Sir, the deep research on '{query}' is complete. The report has been saved.")
            except Exception as ne:
                logger.warning(f"Notification callback failed: {ne}")

        return result

    except asyncio.CancelledError:
        logger.info(f"Task {task_id} was cancelled.")
        return {"task_id": task_id, "response": "Deep research task was cancelled.", "file_path": None}
    except Exception as e:
        logger.error(f"Orchestrator fatal error: {e}", exc_info=True)
        return {"task_id": task_id, "response": f"Deep research failed: {e}", "file_path": None}
    finally:
        _running_tasks.pop(task_id, None)


def start_orchestrator_background(query: str, context: str = "", notify_callback=None) -> str:
    task_id = str(uuid.uuid4())
    task = asyncio.create_task(run_orchestrator(query, context, task_id, notify_callback=notify_callback))
    _running_tasks[task_id]=task
    return task_id
