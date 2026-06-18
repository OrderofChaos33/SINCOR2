import re, asyncio
from bs4 import BeautifulSoup
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=6))
async def fetch(client, url):
    r = await client.get(url, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r

async def extract_emails(client, site_url: str):
    if not site_url: return []
    emails = set()
    for path in ["", "/contact", "/about"]:
        u = site_url.rstrip("/") + path
        try:
            r = await fetch(client, u)
            if r.status_code >= 400 or "text/html" not in r.headers.get("content-type",""):
                continue
            soup = BeautifulSoup(r.text, "lxml")
            text = soup.get_text(" ", strip=True)
            emails.update(EMAIL_RE.findall(text))
            for a in soup.select("a[href^=mailto]"):
                addr = a.get("href","").replace("mailto:","").strip()
                if addr: emails.add(addr)
        except Exception:
            pass
    return sorted(emails)
