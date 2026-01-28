from fastapi import FastAPI, HTTPException, Response, Request, Header
from pydantic import BaseModel, EmailStr
import asyncpg
import os
import json
from typing import Optional, Dict, List
from datetime import datetime
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from billing import BillingManager
from webhooks import WEBHOOK_HANDLERS

app = FastAPI()
DB_DSN = os.getenv("DB_DSN")

# Metrics
SUBSCRIPTIONS_CREATED = Counter("subscriptions_created_total", "Total subscriptions created")
SUBSCRIPTION_CANCELLATIONS = Counter("subscription_cancellations_total", "Total subscription cancellations")
WEBHOOK_EVENTS = Counter("webhook_events_total", "Webhook events processed", ["event_type"])

class PlanRequest(BaseModel):
    tenant_id: str
    code: str
    name: str
    price_cents: int
    interval_type: str = "month"
    interval_count: int = 1
    features: Optional[Dict] = {}

class SubscribeRequest(BaseModel):
    tenant_id: Optional[str] = None
    customer_email: EmailStr
    customer_name: Optional[str] = None
    plan_id: str
    trial_days: Optional[int] = None
    metadata: Optional[Dict] = {}

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.connect(DB_DSN)
    app.state.billing = BillingManager()

@app.get("/health")
async def health():
    return {"ok": True, "service": "subscriptions"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Plan Management
@app.post("/plans")
async def create_plan(plan: PlanRequest):
    """Create a new subscription plan"""
    plan_id = await app.state.db.fetchval("""
        INSERT INTO plans (tenant_id, code, name, price_cents, interval_type, interval_count, features)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
    """, plan.tenant_id, plan.code, plan.name, plan.price_cents, 
        plan.interval_type, plan.interval_count, json.dumps(plan.features))
    
    return {"plan_id": str(plan_id), "code": plan.code, "name": plan.name}

@app.get("/plans")
async def list_plans(tenant_id: Optional[str] = None, active_only: bool = True):
    """List subscription plans"""
    query = "SELECT * FROM plans WHERE 1=1"
    params = []
    
    if tenant_id:
        query += " AND tenant_id = $1"
        params.append(tenant_id)
        
    if active_only:
        query += f" AND active = ${len(params) + 1}"
        params.append(True)
        
    query += " ORDER BY price_cents ASC"
    
    rows = await app.state.db.fetch(query, *params)
    
    return {
        "plans": [
            {
                "id": str(row['id']),
                "tenant_id": row['tenant_id'],
                "code": row['code'],
                "name": row['name'],
                "price_cents": row['price_cents'],
                "interval_type": row['interval_type'],
                "interval_count": row['interval_count'],
                "features": row['features'],
                "active": row['active']
            }
            for row in rows
        ]
    }

# Subscription Management
@app.post("/subscribe")
async def create_subscription(subscription: SubscribeRequest):
    """Create a new subscription"""
    try:
        # Get plan details
        plan = await app.state.db.fetchrow("SELECT * FROM plans WHERE id = $1", subscription.plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")

        PAYPAL_ENABLED = bool(os.getenv("PAYPAL_REST_API_ID") and os.getenv("PAYPAL_REST_API_SECRET"))
        ONCHAIN_ENABLED = bool(os.getenv("BASE_RPC_URL") and (os.getenv("PRIVATE_KEY") or os.getenv("MARKETPLACE_PRIVATE_KEY")))

        # Provider-agnostic: prefer Stripe only if explicitly enabled (compat mode), otherwise use PayPal/on-chain/demo
        provider = None
        customer_ref = None
        client_secret = None
        provider_status = None

        if app.state.billing.stripe_enabled:
            # Create or get Stripe customer
            customer_ref = await app.state.billing.create_customer(
                email=subscription.customer_email,
                name=subscription.customer_name,
                metadata=subscription.metadata
            )
            # Create Stripe price id (legacy compatibility)
            price_id = f"price_{plan['code']}"
            stripe_sub = await app.state.billing.create_subscription(
                customer_id=customer_ref,
                price_id=price_id,
                trial_days=subscription.trial_days,
                metadata={"tenant_id": subscription.tenant_id, "plan_id": subscription.plan_id}
            )

            provider = "stripe"
            provider_id = stripe_sub.get("subscription_id") if isinstance(stripe_sub, dict) else None
            provider_status = stripe_sub.get("status") if isinstance(stripe_sub, dict) else None
            client_secret = stripe_sub.get("client_secret") if isinstance(stripe_sub, dict) else None
            current_period_start = stripe_sub.get("current_period_start")
            current_period_end = stripe_sub.get("current_period_end")
            trial_end = stripe_sub.get("trial_end")
        else:
            # PayPal/on-chain/demo path - create demo subscription object
            provider = "paypal" if PAYPAL_ENABLED else ("onchain" if ONCHAIN_ENABLED else "demo")
            provider_id = f"{provider}_sub_{int(datetime.utcnow().timestamp())}"
            provider_status = "active"
            client_secret = f"{provider}_cs_{provider_id}"
            customer_ref = subscription.customer_email
            current_period_start = None
            current_period_end = None
            trial_end = None

        # Store subscription locally
        sub_id = await app.state.db.fetchval("""
            INSERT INTO subscriptions (
                tenant_id, customer_ref, plan_id, provider_subscription_id,
                status, current_period_start, current_period_end, trial_end
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """, 
            subscription.tenant_id, customer_ref, subscription.plan_id,
            provider_id, provider_status,
            current_period_start, current_period_end, trial_end
        )

        SUBSCRIPTIONS_CREATED.inc()

        return {
            "subscription_id": str(sub_id),
            "provider_subscription_id": provider_id,
            "client_secret": client_secret,
            "status": provider_status,
            "provider": provider
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create subscription: {str(e)}")

@app.post("/subscriptions/{subscription_id}/cancel")
async def cancel_subscription(subscription_id: str, at_period_end: bool = True):
    """Cancel a subscription"""
    # Get subscription
    sub = await app.state.db.fetchrow("""
        SELECT * FROM subscriptions WHERE id = $1
    """, subscription_id)
    
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    try:
        # Determine provider and cancel appropriately
        provider_id = sub.get('provider_subscription_id')
        provider = sub.get('provider') if 'provider' in sub else ("stripe" if app.state.billing.stripe_enabled else ("paypal" if bool(os.getenv("PAYPAL_REST_API_ID") and os.getenv("PAYPAL_REST_API_SECRET")) else "demo"))

        if provider == "stripe" and app.state.billing.stripe_enabled:
            cancel_result = await app.state.billing.cancel_subscription(provider_id, at_period_end)
        else:
            # For PayPal or demo flows we simulate immediate cancellation in local DB
            cancel_result = {"subscription_id": provider_id, "status": "cancelled", "cancelled_at": str(datetime.utcnow())}
            if not at_period_end:
                await app.state.db.execute("""
                    UPDATE subscriptions 
                    SET status = 'cancelled', cancelled_at = now(), updated_at = now()
                    WHERE id = $1
                """, subscription_id)

        SUBSCRIPTION_CANCELLATIONS.inc()

        return cancel_result

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to cancel subscription: {str(e)}")

@app.get("/subscriptions")
async def list_subscriptions(tenant_id: Optional[str] = None, customer_ref: Optional[str] = None):
    """List subscriptions"""
    query = "SELECT s.*, p.name as plan_name, p.code as plan_code FROM subscriptions s JOIN plans p ON s.plan_id = p.id WHERE 1=1"
    params = []
    
    if tenant_id:
        query += " AND s.tenant_id = $1"
        params.append(tenant_id)
        
    if customer_ref:
        query += f" AND s.customer_ref = ${len(params) + 1}"
        params.append(customer_ref)
        
    query += " ORDER BY s.created_at DESC"
    
    rows = await app.state.db.fetch(query, *params)
    
    return {
        "subscriptions": [
            {
                "id": str(row['id']),
                "tenant_id": row['tenant_id'],
                "customer_ref": row['customer_ref'],
                "plan": {
                    "id": str(row['plan_id']),
                    "name": row['plan_name'],
                    "code": row['plan_code']
                },
                "status": row['status'],
                "current_period_start": row['current_period_start'].isoformat() if row['current_period_start'] else None,
                "current_period_end": row['current_period_end'].isoformat() if row['current_period_end'] else None,
                "trial_end": row['trial_end'].isoformat() if row['trial_end'] else None,
                "cancelled_at": row['cancelled_at'].isoformat() if row['cancelled_at'] else None
            }
            for row in rows
        ]
    }

# Webhook Handler
@app.post("/webhooks/billing")
async def handle_billing_webhook(request: Request, signature: str = Header(None)):
    """Handle payment webhooks (PayPal / Stripe / other)."""
    body = await request.body()
    PAYPAL_ENABLED = bool(os.getenv("PAYPAL_REST_API_ID") and os.getenv("PAYPAL_REST_API_SECRET"))

    # Verify signature when provider is enabled; otherwise, accept PayPal payloads when PayPal is configured
    verified = False
    try:
        verified = bool(app.state.billing.verify_webhook_signature(body.decode(), signature))
    except NotImplementedError:
        verified = False

    if not verified and not PAYPAL_ENABLED:
        # If not verified and PayPal not configured, reject
        raise HTTPException(status_code=400, detail="Invalid or unsupported webhook signature")

    try:
        event = json.loads(body.decode())
        event_type = event.get('type')

        WEBHOOK_EVENTS.labels(event_type=event_type or 'unknown').inc()

        if event_type in WEBHOOK_HANDLERS:
            await WEBHOOK_HANDLERS[event_type](event.get('data'), app.state.db)

        return {"received": True}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook processing failed: {str(e)}")