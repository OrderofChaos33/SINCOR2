import os, asyncio, json
from libs.pkg_bus.bus import redis, consume, xadd, STREAM_IN, STREAM_CLEANED
from libs.pkg_lead_model.hashing import dedupe_key
from validators import email_syntax, mx_exists, smtp_ping, phone_basic, is_proxy_ip

async def handler(msg):
    lead = msg["lead"]
    c = lead.get("contact", {})
    reasons = []

    # dedupe
    dkey = dedupe_key(c.get("email"), c.get("phone"))

    r = await app_redis()
    exists = await r.get(f"dedupe:{dkey}")
    if exists:
        reasons.append("duplicate")
    else:
        await r.setex(f"dedupe:{dkey}", 60*60*24*14, "1")  # 14-day TTL

    # validators
    if c.get("email"):
        if not email_syntax(c["email"]): reasons.append("bad_email_syntax")
        elif not mx_exists(c["email"]): reasons.append("no_mx")
        elif not smtp_ping(c["email"]): reasons.append("smtp_fail")
    if c.get("phone") and not phone_basic(c["phone"]): reasons.append("bad_phone")
    if c.get("ip") and is_proxy_ip(c["ip"]): reasons.append("proxy_ip")

    lead["validation"] = {"ok": len(reasons)==0, "reasons": reasons}
    await xadd(await app_redis(), STREAM_CLEANED, {"lead": lead})

_redis = None
async def app_redis():
    global _redis
    if _redis: return _redis
    _redis = await redis()
    return _redis

async def main():
    r = await app_redis()
    await consume(r, STREAM_IN, "g.cleaner", "cleaner-1", handler)

if __name__ == "__main__":
    asyncio.run(main())