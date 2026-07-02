from typing import Dict, Any, List

SYSTEM_PERSONA = """Act as a curious expert research partner. Connect new facts to prior findings, call out coverage gaps, flag single-source claims, and only say coverage is sufficient after multiple useful sources cover the important angles."""

async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    facts: List[str] = input_data.get("facts") or []
    iteration = int(input_data.get("iteration", 1))
    goal = input_data.get("goal") or (context or {}).get("goal", "")
    useful = [f for f in facts if f and len(f) > 60]
    sufficient = iteration >= 2 or len(useful) >= 5
    next_queries = [] if sufficient else [f"{goal} latest evidence", f"{goal} criticism limitations", f"{goal} expert analysis"]
    assessment = f"{SYSTEM_PERSONA}\nRound {iteration}: reviewed {len(useful)} useful evidence batches. " + ("Coverage looks sufficient for synthesis." if sufficient else "More corroboration and gap-focused searching is needed.")
    return {"coverage_assessment": assessment, "next_search_queries": next_queries, "sufficient": sufficient}
