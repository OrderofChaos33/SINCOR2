import os
import asyncio

async def get_cache_status():
    url = os.getenv("REDIS_URL", "")
    if not url:
        return {"configured": False}
    await asyncio.sleep(0)
    return {"configured": True, "driver": "redis", "url_masked": url[:20] + "..."}
