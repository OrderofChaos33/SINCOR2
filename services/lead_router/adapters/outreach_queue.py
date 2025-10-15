from libs.pkg_bus.bus import xadd, redis, STREAM_ROUTE

async def enqueue_outreach(lead: dict):
    await xadd(await redis(), "stream.outreach.queued", {"lead": lead})
    return {"status": "queued_outreach"}