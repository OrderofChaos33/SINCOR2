import os, asyncio, json, yaml, random, time
from fastapi import FastAPI
import aioredis
from libs.pkg_bus.bus import redis, consume, xadd, STREAM_SCORED, STREAM_ROUTE, STREAM_DELIVERED, STREAM_DLQ
from libs.pkg_rules.engine import evaluate_rules

CONFIG_DIR = os.getenv("CONFIG_DIR","/app/config")
with open(os.path.join(CONFIG_DIR, "routing/smartlists.yaml")) as f:
    SMART = yaml.safe_load(f)
with open(os.path.join(CONFIG_DIR, "routing/caps.yaml")) as f:
    CAPS = yaml.safe_load(f)["destinations"]

app = FastAPI()

async def rconn():
    if not hasattr(app.state, "r"):
        app.state.r = await redis()
    return app.state.r

def eligible_lists(lead: dict):
    payload = {"lead": lead}
    out = []
    for l in SMART["lists"]:
        if evaluate_rules(payload, l["rules"]):
            out.append(l["name"])
    return out

def cap_keys(dest):
    now = time.gmtime()
    day = f"{now.tm_year}{now.tm_mon:02}{now.tm_mday:02}"
    hour = f"{day}{now.tm_hour:02}"
    return (f"cap:{dest}:day:{day}", f"cap:{dest}:hour:{hour}", f"cap:{dest}:concurrency")

async def within_caps(dest):
    r = await rconn()
    daily, hourly, conc = cap_keys(dest)
    cfg = CAPS.get(dest, {})
    if cfg.get("daily") and int(await r.get(daily) or 0) >= cfg["daily"]:
        return False
    if cfg.get("hourly") and int(await r.get(hourly) or 0) >= cfg["hourly"]:
        return False
    if cfg.get("concurrency") and int(await r.get(conc) or 0) >= cfg["concurrency"]:
        return False
    return True

async def inc_caps(dest):
    r = await rconn()
    daily, hourly, conc = cap_keys(dest)
    await r.incr(daily); await r.expire(daily, 60*60*26)
    await r.incr(hourly); await r.expire(hourly, 60*90)
    await r.incr(conc)

async def dec_conc(dest):
    r = await rconn()
    _, _, conc = cap_keys(dest)
    await r.decr(conc)

async def deliver(lead: dict, dest: str):
    # simple adapter dispatch
    if dest == "LOCAL_DETAILING_ELIGIBLE":
        from adapters.local_booking import send_to_booking
        return await send_to_booking(lead)
    elif dest == "SAAS_ELIGIBLE":
        from adapters.saas_checkout import send_to_saas
        return await send_to_saas(lead)
    else:
        from adapters.outreach_queue import enqueue_outreach
        return await enqueue_outreach(lead)

async def try_deliver(lead, dest, attempt=1, max_attempts=4):
    # jittered exponential backoff on adapter exceptions or 5xx responses
    try:
        await inc_caps(dest)
        res = await deliver(lead, dest)
        await dec_conc(dest)
        return res
    except Exception as e:
        await dec_conc(dest)
        if attempt >= max_attempts:
            # DLQ
            await xadd(await redis(), STREAM_DLQ, {
                "lead_id": lead["lead_id"], "dest": dest,
                "err": str(e), "attempts": attempt
            })
            return {"status":"failed", "error": str(e)}
        await asyncio.sleep((2 ** attempt) + random.random())
        return await try_deliver(lead, dest, attempt+1, max_attempts)

async def handler(msg):
    lead = msg["lead"]
    lists = eligible_lists(lead)
    dest = "OUTREACH" if not lists else lists[0]
    if not await within_caps(dest):
        # Push to DLQ with reason 'caps'
        await xadd(await redis(), STREAM_DLQ, {"lead_id": lead["lead_id"], "dest": dest, "err": "caps_exceeded"})
        return
    res = await try_deliver(lead, dest)
    await xadd(await redis(), STREAM_DELIVERED, {"lead_id": lead["lead_id"], "destination": dest, "result": res})

@app.get("/health")
async def health(): 
    return {"ok": True, "service": "lead_router"}

async def main():
    r = await redis()
    await consume(r, STREAM_SCORED, "g.router", "router-1", handler)

if __name__ == "__main__":
    asyncio.run(main())