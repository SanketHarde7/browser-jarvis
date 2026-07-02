import re, httpx
from typing import Dict, Any

async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    url = input_data.get("url") or input_data.get("input_spec") or ""
    if not url: raise ValueError("scrape_agent requires url")
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0 MAX"}) as client:
        r = await client.get(url); r.raise_for_status(); html=r.text
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return {"url": url, "raw_text": text[:120000], "scrape_success": bool(text)}
