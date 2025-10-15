import asyncio
from libs.pkg_bus.bus import redis, consume, xadd, STREAM_CLEANED, STREAM_SCORED
from scorer import score_lead

async def handler(msg):
    lead = msg["lead"]
    lead["score"] = score_lead(lead)
    await xadd(await redis(), STREAM_SCORED, {"lead": lead})

async def main():
    r = await redis()
    await consume(r, STREAM_CLEANED, "g.scorer", "scorer-1", handler)

if __name__ == "__main__":
    asyncio.run(main())