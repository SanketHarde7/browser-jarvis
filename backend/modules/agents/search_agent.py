import httpx, re, urllib.parse
from typing import Dict, Any

async def run(input_data: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
    query = input_data.get("query") or input_data.get("input_spec") or input_data.get("goal") or ""
    max_results = int(input_data.get("max_results", 10))
    urls=[]
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0 MAX"}) as client:
        r = await client.get(url); r.raise_for_status(); html=r.text
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        if "duckduckgo.com/l/" in href and "uddg=" in href:
            parsed=urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
            if parsed: href=urllib.parse.unquote(parsed[0])
        if href.startswith("http") and "duckduckgo.com" not in href and href not in urls:
            urls.append(href)
        if len(urls) >= max_results: break
    if not urls and query:
        urls.append("https://en.wikipedia.org/wiki/" + urllib.parse.quote(query.replace(" ", "_")))
    return {"query": query, "candidate_urls": urls}
