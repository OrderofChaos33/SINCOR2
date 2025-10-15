from fastapi import FastAPI, HTTPException, Response, Request, Query, Depends, Header
from pydantic import BaseModel
import asyncpg
import os
from datetime import datetime, timedelta
from typing import Optional, List
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from rules import ReferralRules
from libs.pkg_bus.bus import xadd, redis
import secrets
import string

app = FastAPI()
DB_DSN = os.getenv("DB_DSN")
REFERRAL_COOKIE_DAYS = int(os.getenv("REFERRAL_COOKIE_DAYS", "30"))
ADMIN_API_KEY = os.getenv("REFERRAL_ADMIN_KEY", "admin-dev-key")

# Initialize rules engine
referral_rules = ReferralRules(cookie_days=REFERRAL_COOKIE_DAYS)

# Metrics
REFERRAL_CLICKS = Counter("referral_clicks_total", "Total referral link clicks", ["code"])
REFERRAL_CONVERSIONS = Counter("referral_conversions_total", "Total referral conversions", ["code", "type"])
REFERRAL_PAYOUTS = Counter("referral_payouts_total", "Total referral payouts in cents", ["code"])

class RefCodeRequest(BaseModel):
    tenant_id: str
    campaign_name: str
    payout_cents: int
    payout_type: str = "fixed"  # fixed or percentage
    expires_days: Optional[int] = None

class ConversionRequest(BaseModel):
    fingerprint: Optional[str] = None
    conversion_type: str  # purchase, booking, subscription
    conversion_ref: str  # purchase_id, booking_id, etc
    amount_cents: int

async def verify_admin(authorization: str = Header(None)):
    """Admin authentication"""
    if authorization != f"Bearer {ADMIN_API_KEY}":
        raise HTTPException(status_code=403, detail="Admin access required")

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.connect(DB_DSN)

@app.get("/health")
async def health():
    return {"ok": True, "service": "referrals"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

def generate_ref_code() -> str:
    """Generate a unique referral code"""
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))

# Referral Code Management
@app.post("/ref/codes", dependencies=[Depends(verify_admin)])
async def create_ref_code(ref_request: RefCodeRequest):
    """Create a new referral code"""
    
    # Generate unique code
    code = generate_ref_code()
    while await app.state.db.fetchval("SELECT id FROM ref_codes WHERE code = $1", code):
        code = generate_ref_code()
    
    expires_at = None
    if ref_request.expires_days:
        expires_at = datetime.now() + timedelta(days=ref_request.expires_days)
    
    ref_id = await app.state.db.fetchval("""
        INSERT INTO ref_codes (tenant_id, code, campaign_name, payout_cents, payout_type, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    """, ref_request.tenant_id, code, ref_request.campaign_name, 
        ref_request.payout_cents, ref_request.payout_type, expires_at)
    
    return {
        "ref_id": str(ref_id),
        "code": code,
        "campaign_name": ref_request.campaign_name,
        "payout_cents": ref_request.payout_cents,
        "tracking_url": f"/ref/{code}",
        "embed_script": referral_rules.generate_tracking_script(code)
    }

@app.get("/ref/codes")
async def list_ref_codes(tenant_id: Optional[str] = None, active_only: bool = True):
    """List referral codes"""
    query = "SELECT * FROM ref_codes WHERE 1=1"
    params = []
    
    if tenant_id:
        params.append(tenant_id)
        query += f" AND tenant_id = ${len(params)}"
    
    if active_only:
        params.append(True)
        query += f" AND active = ${len(params)}"
        query += " AND (expires_at IS NULL OR expires_at > now())"
    
    query += " ORDER BY created_at DESC"
    
    rows = await app.state.db.fetch(query, *params)
    
    codes = []
    for row in rows:
        # Get conversion stats
        stats = await app.state.db.fetchrow("""
            SELECT 
                COUNT(*) as total_conversions,
                COALESCE(SUM(amount_cents), 0) as total_revenue,
                COALESCE(SUM(payout_amount_cents), 0) as total_payouts
            FROM ref_conversions 
            WHERE code = $1
        """, row['code'])
        
        codes.append({
            "id": str(row['id']),
            "code": row['code'],
            "campaign_name": row['campaign_name'],
            "payout_cents": row['payout_cents'],
            "payout_type": row['payout_type'],
            "active": row['active'],
            "expires_at": row['expires_at'].isoformat() if row['expires_at'] else None,
            "stats": {
                "total_conversions": stats['total_conversions'],
                "total_revenue": stats['total_revenue'],
                "total_payouts": stats['total_payouts']
            }
        })
    
    return {"codes": codes}

# Referral Tracking
@app.get("/ref/{code}.js")
async def get_tracking_script(code: str, request: Request):
    """Serve embeddable referral tracking script"""
    
    # Verify code exists and is active
    ref_code = await app.state.db.fetchrow("SELECT * FROM ref_codes WHERE code = $1 AND active = true", code)
    if not ref_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    
    # Get domain from request
    host = request.headers.get("host", "")
    protocol = "https" if "https" in str(request.url) else "http"
    domain = f"{protocol}://{host}"
    
    script = referral_rules.generate_tracking_script(code, domain)
    
    return Response(content=script, media_type="application/javascript")

@app.get("/ref/pixel")
async def tracking_pixel(code: str = Query(...), 
                        fp: str = Query(...),
                        request: Request = None):
    """1x1 tracking pixel for referral attribution"""
    
    # Get client info
    ip = request.client.host
    user_agent = request.headers.get("user-agent", "")
    referrer = request.headers.get("referer", "")
    
    # Create or update attribution
    await track_referral_click(code, fp, ip, user_agent, referrer)
    
    REFERRAL_CLICKS.labels(code=code).inc()
    
    # Return 1x1 transparent pixel
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x04\x01\x00\x3b'
    return Response(content=pixel, media_type="image/gif")

async def track_referral_click(code: str, fingerprint: str, ip: str, user_agent: str, referrer: str):
    """Track a referral click and create/update attribution"""
    
    # Check if attribution already exists
    existing = await app.state.db.fetchrow("""
        SELECT * FROM ref_attributions WHERE fingerprint = $1
    """, fingerprint)
    
    if existing:
        # Update last seen
        await app.state.db.execute("""
            UPDATE ref_attributions 
            SET last_seen = now(), code = $1
            WHERE fingerprint = $2
        """, code, fingerprint)
    else:
        # Create new attribution
        await app.state.db.execute("""
            INSERT INTO ref_attributions (code, fingerprint, ip_address, user_agent, referrer)
            VALUES ($1, $2, $3, $4, $5)
        """, code, fingerprint, ip, user_agent, referrer)

# Conversion Tracking
@app.post("/ref/conversions")
async def track_conversion(conversion: ConversionRequest):
    """Track a conversion for referral attribution"""
    
    if not conversion.fingerprint:
        return {"status": "no_attribution", "message": "No fingerprint provided"}
    
    # Find attribution
    attribution = await app.state.db.fetchrow("""
        SELECT a.*, rc.* 
        FROM ref_attributions a
        JOIN ref_codes rc ON a.code = rc.code
        WHERE a.fingerprint = $1 AND rc.active = true
    """, conversion.fingerprint)
    
    if not attribution:
        return {"status": "no_attribution", "message": "No valid referral attribution found"}
    
    # Validate attribution rules
    if not referral_rules.is_valid_attribution(dict(attribution), dict(attribution)):
        return {"status": "expired_attribution", "message": "Attribution window expired"}
    
    # Calculate payout
    payout_amount = referral_rules.calculate_payout(dict(attribution), conversion.amount_cents)
    
    # Record conversion
    conversion_id = await app.state.db.fetchval("""
        INSERT INTO ref_conversions (
            code, attribution_id, amount_cents, conversion_type, 
            conversion_ref, payout_amount_cents
        ) VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
    """, attribution['code'], attribution['id'], conversion.amount_cents,
        conversion.conversion_type, conversion.conversion_ref, payout_amount)
    
    # Mark attribution as converted
    await app.state.db.execute("""
        UPDATE ref_attributions 
        SET converted = true, conversion_value_cents = conversion_value_cents + $1
        WHERE id = $2
    """, conversion.amount_cents, attribution['id'])
    
    # Emit event for payout processing
    r = await redis()
    await xadd(r, "stream.referral.converted", {
        "conversion_id": str(conversion_id),
        "code": attribution['code'],
        "tenant_id": attribution['tenant_id'],
        "payout_amount_cents": payout_amount,
        "conversion_type": conversion.conversion_type
    })
    
    REFERRAL_CONVERSIONS.labels(code=attribution['code'], type=conversion.conversion_type).inc()
    REFERRAL_PAYOUTS.labels(code=attribution['code']).inc(payout_amount)
    
    return {
        "status": "conversion_tracked",
        "conversion_id": str(conversion_id),
        "payout_amount_cents": payout_amount,
        "referral_code": attribution['code']
    }

# Analytics
@app.get("/ref/analytics/{code}")
async def get_referral_analytics(code: str):
    """Get analytics for a referral code"""
    
    # Verify code exists
    ref_code = await app.state.db.fetchrow("SELECT * FROM ref_codes WHERE code = $1", code)
    if not ref_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    
    # Get attribution stats
    attribution_stats = await app.state.db.fetchrow("""
        SELECT 
            COUNT(*) as total_clicks,
            COUNT(CASE WHEN converted = true THEN 1 END) as total_conversions,
            COALESCE(SUM(conversion_value_cents), 0) as total_conversion_value
        FROM ref_attributions
        WHERE code = $1
    """, code)
    
    # Get conversion breakdown
    conversion_breakdown = await app.state.db.fetch("""
        SELECT 
            conversion_type,
            COUNT(*) as count,
            SUM(amount_cents) as total_amount,
            SUM(payout_amount_cents) as total_payout
        FROM ref_conversions
        WHERE code = $1
        GROUP BY conversion_type
    """, code)
    
    return {
        "code": code,
        "campaign_name": ref_code['campaign_name'],
        "summary": {
            "total_clicks": attribution_stats['total_clicks'],
            "total_conversions": attribution_stats['total_conversions'],
            "conversion_rate": round(attribution_stats['total_conversions'] / max(attribution_stats['total_clicks'], 1) * 100, 2),
            "total_conversion_value": attribution_stats['total_conversion_value']
        },
        "breakdown": [
            {
                "type": row['conversion_type'],
                "count": row['count'],
                "total_amount": row['total_amount'],
                "total_payout": row['total_payout']
            } for row in conversion_breakdown
        ]
    }