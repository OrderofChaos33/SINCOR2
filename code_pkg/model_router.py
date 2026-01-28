import httpx
from .settings import settings

async def generate(prompt: str) -> str:
    # Try local Ollama first
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{settings.ollama_url}/api/generate",
                                     json={"model": "llama3", "prompt": prompt, "stream": False})
        if resp.status_code == 200:
            data = resp.json()
            return data.get("response", "").strip() or "[empty]"
    except Exception:
        pass

    # Fallback: stub
    return f"[stubbed-response] {prompt[:80]}..."
