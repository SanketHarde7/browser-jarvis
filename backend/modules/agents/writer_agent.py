from pathlib import Path
from typing import Dict, Any, List
from config import config

async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    goal = input_data.get("goal") or "research"
    facts: List[dict] = input_data.get("evaluations") or []
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in goal.lower())[:80]
    out_dir = config.PROJECT_ROOT / "research_outputs"; out_dir.mkdir(exist_ok=True)
    path = out_dir / f"deep_research_{safe}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"DEEP RESEARCH REPORT: {goal}\n\n")
        f.write("## Executive Summary\nThis report was generated through MAX Orchestrator search, scrape, evaluate, reasoning, and synthesis steps.\n\n")
        f.write("## Evidence Notes\n")
        for i, ev in enumerate(facts, 1):
            f.write(f"\n### Source {i}: {ev.get('url','unknown')}\n")
            f.write((ev.get("extracted_facts") or ev.get("relevance_note") or "No details.")[:4000] + "\n")
        f.write("\n## Verification Guidance\nClaims appearing in only one source should be treated as provisional unless corroborated independently.\n")
    return {"file_path": str(path), "message": f"Research complete, saved to {path}"}
