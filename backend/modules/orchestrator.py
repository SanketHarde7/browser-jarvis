# Path: backend/modules/orchestrator.py
# Use: Orchestrator core, complexity gate, roadmap dispatch, and Deep Research mode.
import asyncio, re, uuid
from typing import Any, Dict, List
from modules.blackboard import blackboard
from modules.resource_manager import resource_manager
from modules.agents import search_agent, scrape_agent, evaluate_agent, browser_reasoning_agent, writer_agent

DIRECT = "DIRECT"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
AGENT_REGISTRY = {
    "search_agent": search_agent.run,
    "scrape_agent": scrape_agent.run,
    "evaluate_agent": evaluate_agent.run,
    "browser_reasoning_agent": browser_reasoning_agent.run,
    "writer_agent": writer_agent.run,
}
_COMPLEX_PATTERNS = [r"\bdeep research\b", r"\bresearch .* thoroughly\b", r"\bcompare\b.*\b(across|sources|websites)\b", r"\bkeep digging\b", r"\bfull understanding\b", r"\bbuild and test\b", r"\bcomprehensive\b", r"\buntil .* accurate\b"]
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

def build_deep_research_roadmap(query: str, task_id: str) -> Dict[str, Any]:
    return {"task_id": task_id, "goal": query, "steps": [{"step_id":"s1","agent":"search_agent","depends_on":[],"input_spec":query,"output_spec":"candidate_urls[]","parallel_group":None}], "stopping_condition":"coverage_based", "max_iterations_safety_cap":8}

async def _execute_step(task_id: str, step: Dict[str, Any], context: Dict[str, Any]):
    blackboard.update_step(task_id, step["step_id"], "running")
    try:
        agent = AGENT_REGISTRY[step["agent"]]
        output = await agent(step.get("input_data") or step, context)
        blackboard.update_step(task_id, step["step_id"], "success", output)
    except Exception as e:
        # one automatic retry for transient-like failures
        try:
            await asyncio.sleep(1)
            output = await AGENT_REGISTRY[step["agent"]](step.get("input_data") or step, context)
            blackboard.update_step(task_id, step["step_id"], "success", output)
        except Exception as e2:
            blackboard.update_step(task_id, step["step_id"], "failed", {}, str(e2 or e))
    finally:
        await resource_manager.release()

async def run_orchestrator(query: str, conversation_context: str = "", task_id: str = None) -> Dict[str, Any]:
    task_id = task_id or str(uuid.uuid4())
    roadmap = build_deep_research_roadmap(query, task_id)
    blackboard.init_task(task_id, roadmap)
    _running_tasks[task_id] = asyncio.current_task()
    context={"goal": query, "conversation_context": conversation_context}
    evaluations=[]; iteration=1; max_iter=roadmap["max_iterations_safety_cap"]
    try:
        while iteration <= max_iter:
            # run ready roadmap steps
            while True:
                ready = await resource_manager.filter_by_capacity(blackboard.get_ready_steps(task_id))
                if not ready: break
                await asyncio.gather(*[_execute_step(task_id, s, context) for s in ready])
            steps = blackboard.get_task_steps(task_id)
            searches=[s for s in steps if s["agent_name"]=="search_agent" and s["status"]=="success"]
            urls=[]
            for s in searches: urls += s["output_data"].get("candidate_urls", [])[:5]
            existing={s["input_data"].get("url") for s in steps if s["agent_name"]=="scrape_agent"}
            new_urls=[u for u in urls if u not in existing][:8]
            if new_urls:
                for i,u in enumerate(new_urls): blackboard.create_step(task_id, f"scrape_{iteration}_{i}", "scrape_agent", [], {"url":u})
                continue
            scrapes=[s for s in blackboard.get_task_steps(task_id) if s["agent_name"]=="scrape_agent" and s["status"]=="success"]
            evaluated={s["input_data"].get("url") for s in steps if s["agent_name"]=="evaluate_agent"}
            new_scrapes=[s for s in scrapes if s["output_data"].get("url") not in evaluated]
            if new_scrapes:
                for i,s in enumerate(new_scrapes): blackboard.create_step(task_id, f"eval_{iteration}_{i}", "evaluate_agent", [], {"url":s["output_data"].get("url"), "raw_text":s["output_data"].get("raw_text"), "question":query})
                continue
            evaluations=[s["output_data"] for s in blackboard.get_task_steps(task_id) if s["agent_name"]=="evaluate_agent" and s["status"]=="success" and s["output_data"].get("is_useful")]
            reason = await browser_reasoning_agent.run({"goal":query,"facts":[e.get("extracted_facts","") for e in evaluations],"iteration":iteration}, context)
            blackboard.create_step(task_id, f"reason_{iteration}", "browser_reasoning_agent", [], {"iteration":iteration})
            blackboard.update_step(task_id, f"reason_{iteration}", "success", reason)
            if reason.get("sufficient") or iteration >= max_iter:
                out = await writer_agent.run({"goal":query,"evaluations":evaluations}, context)
                blackboard.create_step(task_id, "writer", "writer_agent", [], {"goal":query})
                blackboard.update_step(task_id, "writer", "success", out)
                return {"task_id":task_id, "response": out.get("message"), "file_path": out.get("file_path")}
            for i,nq in enumerate(reason.get("next_search_queries", [])):
                blackboard.create_step(task_id, f"search_{iteration}_{i}", "search_agent", [], {"query":nq, "max_results":8})
            iteration += 1
        return {"task_id":task_id, "response":"Deep task stopped at safety cap.", "file_path":None}
    finally:
        _running_tasks.pop(task_id, None)

def start_orchestrator_background(query: str, context: str = "") -> str:
    task_id = str(uuid.uuid4())
    task = asyncio.create_task(run_orchestrator(query, context, task_id))
    _running_tasks[task_id]=task
    return task_id
