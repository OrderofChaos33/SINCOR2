import asyncio, json
from libs.pkg_bus.bus import redis, consume
from generator import generate_pack
from sequencer import send_sequence

async def handler(msg):
    lead = msg["lead"]
    pack = generate_pack(lead)
    await send_sequence(lead, pack)
    # could write to delivery_ledger via API or DB later

async def main():
    r = await redis()
    await consume(r, "stream.outreach.queued", "g.outreach", "outreach-1", handler)

if __name__ == "__main__":
    asyncio.run(main())