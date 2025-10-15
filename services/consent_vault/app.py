from fastapi import FastAPI, HTTPException, Path, Depends, Header, Response
from pydantic import BaseModel
import asyncpg, os, hmac, hashlib
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

DB_DSN = os.getenv("DB_DSN")
HMAC_SECRET = os.getenv("CONSENT_HMAC_SECRET", "dev-secret")
ACCESS_KEY = os.getenv("CONSENT_ACCESS_KEY", "dev-key")

app = FastAPI()

# Metrics
HITS = Counter("consent_requests_total", "Consent vault hits")

class ConsentIn(BaseModel):
    lead_id: str
    ts: str
    ip: str | None = None
    referrer: str | None = None
    form_html: str

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.connect(DB_DSN)

async def auth(authorization: str = Header(None)):
    if authorization != f"Bearer {ACCESS_KEY}":
        raise HTTPException(status_code=403, detail="forbidden")

def sign_digest(sha256_hex: str) -> str:
    return hmac.new(HMAC_SECRET.encode(), sha256_hex.encode(), hashlib.sha256).hexdigest()

@app.get("/health")
async def health():
    return {"ok": True, "service": "consent_vault"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/consent")
async def consent(c: ConsentIn):
    from storage import store_consent
    digest = await store_consent(app.state.db, c.lead_id, c.ts, c.ip, c.referrer, c.form_html)
    return {"ok": True, "sha256": digest, "hmac": sign_digest(digest)}

@app.get("/consent/{lead_id}", dependencies=[Depends(auth)])
async def get_consent(lead_id: str = Path(...)):
    HITS.inc()
    row = await app.state.db.fetchrow("SELECT ts, ip, referrer, sha256_hex FROM consent_artifacts WHERE lead_id=$1", lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "lead_id": lead_id,
        "ts": str(row["ts"]),
        "ip": str(row["ip"]) if row["ip"] else None,
        "referrer": row["referrer"],
        "sha256_hex": row["sha256_hex"],
        "hmac": sign_digest(row["sha256_hex"])
    }