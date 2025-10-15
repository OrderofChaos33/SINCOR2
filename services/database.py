import os
import asyncio

async def get_db_status():
    url = os.getenv("DATABASE_URL", "")
    # We keep this a non-blocking placeholder; replace with real connection test
    if not url:
        return {"configured": False}
    # Simulate non-blocking check
    await asyncio.sleep(0)
    return {"configured": True, "driver": "sqlalchemy/psycopg", "url_masked": url[:20] + "..."}  # mask
