from fastapi import FastAPI, HTTPException, Depends, Header, Request, Response
from pydantic import BaseModel
from uuid import uuid4
import asyncpg, os, asyncio, json, time, hashlib
import aioredis
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from libs.pkg_bus.bus import redis, xadd, STREAM_IN
from libs.pkg_lead_model.models import Lead

app = FastAPI()

# Security & Rate Limiting Config
API_KEY = os.getenv("INGEST_API_KEY", "dev-key")
RATE_LIMIT_RPS = float(os.getenv("INGEST_RPS", "5"))  # per IP
IDEMP_TTL = int(os.getenv("IDEMP_TTL_SECONDS", "3600"))
DB_DSN = os.getenv("DB_DSN")

# Metrics
REQS = Counter("ingest_requests_total", "Total ingest hits")
REJECTS = Counter("ingest_rejects_total", "Rejected requests by reason", ["reason"])
LAT = Histogram("ingest_latency_seconds", "Ingest handler latency")

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.connect(DB_DSN)
    app.state.r = await redis()
    app.state.r2 = await aioredis.from_url(os.getenv("REDIS_URL","redis://redis:6379/0"))

async def get_r():
    if not hasattr(app.state, "r2"):
        app.state.r2 = await aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    return app.state.r2

async def auth(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    if token != API_KEY:
        raise HTTPException(status_code=403, detail="forbidden")

async def rate_limit(ip: str):
    r = await get_r()
    key = f"ratelimit:{ip}:{int(time.time())}"  # 1s window
    count = await r.incr(key)
    await r.expire(key, 2)
    if count > RATE_LIMIT_RPS:
        REJECTS.labels("rate_limit").inc()
        raise HTTPException(status_code=429, detail="rate limit")

async def idempotency_guard(idem_key: str | None):
    if not idem_key:
        return
    r = await get_r()
    k = f"idem:{hashlib.sha256(idem_key.encode()).hexdigest()}"
    exists = await r.setnx(k, "1")
    if not exists:
        REJECTS.labels("idempotent_replay").inc()
        raise HTTPException(status_code=409, detail="duplicate")
    await r.expire(k, IDEMP_TTL)

@app.get("/health")
async def health():
    return {"ok": True, "service": "lead_ingest"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/leads")
@LAT.time()
async def ingest(lead: Lead, request: Request, authorization: None = Depends(auth), 
                 x_idempotency_key: str | None = Header(None),
                 x_forwarded_for: str | None = Header(None)):
    ip = (x_forwarded_for or request.client.host or "unknown").split(",")[0].strip()
    await rate_limit(ip)
    await idempotency_guard(x_idempotency_key)
    
    # persist raw
    await app.state.db.execute(
        "INSERT INTO leads (id, vertical, email, phone, ip, state, payload) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        lead.lead_id, lead.vertical,
        lead.contact.email, lead.contact.phone, lead.contact.ip, lead.contact.state,
        json.dumps(lead.dict())
    )
    await xadd(app.state.r, STREAM_IN, {"lead": lead.dict()})
    
    REQS.inc()
    return {"status": "queued", "lead_id": str(lead.lead_id)}