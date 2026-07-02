import json
from typing import Dict, Any
from modules.llm import get_client
from api_utils import execute_with_retry
from config import config

async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    raw = (input_data.get("raw_text") or "")[:12000]
    url = input_data.get("url", "")
    question = input_data.get("question") or input_data.get("goal") or (context or {}).get("goal", "")
    if not raw:
        return {"url": url, "is_useful": False, "extracted_facts": "", "relevance_note": "No text scraped."}
    prompt = f"Judge this source for the research question. Return strict JSON with is_useful boolean, extracted_facts string, relevance_note string.\nQuestion: {question}\nURL: {url}\nText:\n{raw}"
    async def call():
        client = await get_client()
        return await client.chat.completions.create(model=config.LLM_MODEL, messages=[{"role":"user","content":prompt}], temperature=0.1, max_tokens=900)
    try:
        resp = await execute_with_retry(call, max_retries=3)
        content = resp.choices[0].message.content.strip()
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start:end+1]) if start >= 0 and end >= start else {}
        return {"url": url, "is_useful": bool(data.get("is_useful")), "extracted_facts": data.get("extracted_facts", ""), "relevance_note": data.get("relevance_note", "")}
    except Exception as e:
        # deterministic fallback keeps the pipeline working without keys/network LLM
        return {"url": url, "is_useful": True, "extracted_facts": raw[:2000], "relevance_note": f"Fallback extraction because evaluator LLM failed: {e}"}
