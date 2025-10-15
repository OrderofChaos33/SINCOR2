import httpx

async def get_json(url: str, **kwargs):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, **kwargs)
        r.raise_for_status()
        return r.json()
