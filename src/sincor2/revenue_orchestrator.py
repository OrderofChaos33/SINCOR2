#!/usr/bin/env python3
"""
SINCOR Revenue Orchestrator
Connects your monetization engine, pricing engine, fulfillment engine, and Stripe
into one unified revenue machine.

Wires together:
- Stripe checkout → dynamic pricing + monetization
- Stripe webhook → fulfillment + revenue tracking
- Revenue streams → automated fulfillment per stream type
- Real-time metrics → revenue dashboard

No new dependencies. Just orchestration.
"""

import os
import json
import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

# Import your existing engines (no new code needed)
try:
    from sincor2.monetization_engine import MonetizationEngine, RevenueStream
    from sincor2.dynamic_pricing_engine import DynamicPricingEngine, TaskMetrics, ComplexityLevel
    from sincor2.fulfillment_engine import trigger_autonomous_fulfillment
    MONETIZATION_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Monetization engines not available: {e}")
    MONETIZATION_AVAILABLE = False

# Setup logging
logger = logging.getLogger("revenue_orchestrator")
# The file handler below is created at import time: make sure its directory
# exists first so importing this module never crashes on a missing logs/
# dir (e.g. the x402 rail imports it lazily during pack fulfillment).
Path("logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/revenue_orchestrator.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Paths
DATA_DIR = Path("data/revenue")
DATA_DIR.mkdir(parents=True, exist_ok=True)
REVENUE_DB = DATA_DIR / "revenue_ledger.db"


# --- Digital packs (ported from the parked Stripe branch, reworked onto the
# x402/USDC rail) ------------------------------------------------------------
# money_assets/ digital products sold through SINCOR's own x402 checkout
# (platform-native; the founder rejected Gumroad AND Stripe). Prices from
# money_assets/HOW_TO_MAKE_MONEY_NOW.md — server-side source of truth, the
# checkout route ignores client prices. A digital pack is delivered as a
# token-gated download link (unguessable delivery token bound to the intake
# record); no vertical-agent job is queued for digital packs. The rail
# (src/sincor2/rail_routes.py) merges this table into its PACK_SKUS so
# digital packs ride the same checkout/settle/idempotency machinery.
DIGITAL_SKUS: Dict[str, Dict[str, Any]] = {
    "digital_bi_template_pack": {
        "name": "Instant BI Template Pack",
        "asset": "money_assets/instant_bi_template_pack.md",
        "price_cents": 9700,
        "digital": True,
    },
    "digital_ai_agent_bundle": {
        "name": "AI Agent Bundle",
        "asset": "money_assets/ai_agent_bundle.md",
        "price_cents": 29700,
        "digital": True,
    },
    "digital_growth_forecast": {
        "name": "Growth Forecast Template",
        "asset": "money_assets/growth_forecast_template.md",
        "price_cents": 14700,
        "digital": True,
    },
}


def _digital_intake_db_path() -> Path:
    return Path(os.environ.get("SINCOR_REVENUE_DB") or REVENUE_DB)


def issue_delivery_token() -> str:
    """Mint an unguessable delivery token for a digital-pack purchase.

    32 bytes via secrets.token_urlsafe (43 chars) — bound to the intake
    record at fulfillment time. The download link is intake-gated AND
    token-gated: the intake id alone is never enough to fetch the asset.
    """
    import secrets as _secrets
    return _secrets.token_urlsafe(32)


def verify_digital_delivery(intake_id: str, token: str,
                            db_path: Optional[Path] = None) -> str:
    """Verify a token-gated digital download and return the asset path.

    The download link is intake-gated AND token-gated: the intake id alone
    is not enough. The token must match the unguessable delivery token
    bound to the intake record at fulfillment time, and the asset must be
    one of the known digital pack assets (no path games). Raises
    ValueError on any mismatch — the download route turns this into a 404.
    """
    import hmac as _hmac

    from sincor2.payment_ledger import get_pack_intake

    intake = get_pack_intake(
        db_path or _digital_intake_db_path(), intake_id or "")
    if intake is None:
        raise ValueError("unknown download")
    details = intake.details or {}
    expected = str(details.get("delivery_token") or "")
    if not expected or not _hmac.compare_digest(expected, str(token or "")):
        raise ValueError("invalid download token")
    asset = str(details.get("asset") or "")
    known_assets = {
        sku["asset"] for sku in DIGITAL_SKUS.values() if sku.get("asset")
    }
    if asset not in known_assets:
        raise ValueError("no digital asset bound to this download")
    return asset


class RevenueOrchestrator:
    """Orchestrates the entire revenue pipeline"""
    
    def __init__(self):
        self.monetization_engine = MonetizationEngine() if MONETIZATION_AVAILABLE else None
        self.pricing_engine = DynamicPricingEngine() if MONETIZATION_AVAILABLE else None
        self.revenue_ledger_path = REVENUE_DB
        self._init_revenue_db()
        
    def _init_revenue_db(self):
        """Initialize revenue tracking database"""
        conn = sqlite3.connect(self.revenue_ledger_path)
        cursor = conn.cursor()
        
        # Revenue events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS revenue_events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT,
                customer_email TEXT,
                revenue_stream TEXT,
                amount REAL,
                currency TEXT,
                status TEXT,
                payment_processor TEXT,
                stripe_session_id TEXT,
                fulfillment_status TEXT,
                metadata TEXT
            )
        ''')
        
        # Revenue summary table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS revenue_summary (
                date TEXT PRIMARY KEY,
                total_revenue REAL,
                transactions INT,
                revenue_by_stream TEXT,
                mrr REAL,
                updated_at TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    async def process_stripe_payment(self, stripe_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a Stripe payment and orchestrate the full pipeline:
        1. Identify revenue stream from product
        2. Apply dynamic pricing if needed
        3. Trigger appropriate fulfillment
        4. Log revenue event
        """
        
        try:
            event_type = stripe_event.get('type')
            
            if event_type == 'checkout.session.completed':
                session = stripe_event.get('data', {}).get('object', {})
                customer_email = session.get('customer_email', 'unknown')
                session_id = session.get('id')
                amount = (session.get('amount_total') or 0) / 100
                
                # Determine revenue stream from metadata
                metadata = session.get('metadata', {})
                revenue_stream = metadata.get('revenue_stream', 'INSTANT_BI')
                plan_id = metadata.get('plan_id', 'starter')
                
                logger.info(f"[REVENUE] Payment: {customer_email} | {revenue_stream} | ${amount}")
                
                # Log the revenue event
                event_id = f"stripe-{session_id}"
                self._log_revenue_event(
                    event_id=event_id,
                    customer_email=customer_email,
                    revenue_stream=revenue_stream,
                    amount=amount,
                    currency='USD',
                    payment_processor='stripe',
                    stripe_session_id=session_id,
                    status='completed'
                )
                
                # Trigger fulfillment based on revenue stream
                fulfillment_result = await self._fulfill_order(
                    customer_email=customer_email,
                    revenue_stream=revenue_stream,
                    plan_id=plan_id,
                    amount=amount
                )
                
                # Update event with fulfillment status
                self._update_revenue_event(
                    event_id=event_id,
                    fulfillment_status=fulfillment_result.get('status', 'pending')
                )
                
                return {
                    'success': True,
                    'event_id': event_id,
                    'amount': amount,
                    'stream': revenue_stream,
                    'fulfillment': fulfillment_result
                }
            
            elif event_type == 'invoice.payment_succeeded':
                # Recurring subscription payment
                invoice = stripe_event.get('data', {}).get('object', {})
                customer_email = invoice.get('customer_email', 'unknown')
                amount = (invoice.get('amount_paid') or 0) / 100
                
                logger.info(f"[RECURRING] Subscription: {customer_email} | ${amount}")
                
                event_id = f"stripe-invoice-{invoice.get('id')}"
                self._log_revenue_event(
                    event_id=event_id,
                    customer_email=customer_email,
                    revenue_stream='SUBSCRIPTIONS',
                    amount=amount,
                    currency='USD',
                    payment_processor='stripe',
                    status='completed'
                )
                
                return {'success': True, 'event_id': event_id, 'amount': amount}
        
        except Exception as e:
            logger.error(f"[ERROR] Payment processing failed: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _fulfill_order(self, customer_email: str, revenue_stream: str, 
                            plan_id: str, amount: float) -> Dict[str, Any]:
        """
        Route fulfillment based on revenue stream type.
        Different streams get different fulfillment paths.
        """
        
        try:
            if revenue_stream == 'INSTANT_BI':
                # Competitive intelligence report
                result = await trigger_autonomous_fulfillment(
                    customer_email=customer_email,
                    plan_id=plan_id,
                    plan_name=f"Competitive Intelligence - ${amount}",
                    order_id=f"stripe-{customer_email}",
                    amount=amount
                )
                return {'status': 'fulfilled', 'type': 'report', 'result': result}
            
            elif revenue_stream == 'AGENT_SERVICES':
                # Deploy autonomous agents for customer
                logger.info(f"[FULFILLMENT] Deploying agents for {customer_email}")
                # TODO: Implement agent deployment fulfillment
                return {'status': 'pending', 'type': 'agents', 'message': 'Agents deploying...'}
            
            elif revenue_stream == 'SUBSCRIPTIONS':
                # Recurring subscription
                logger.info(f"[FULFILLMENT] Subscription active for {customer_email}")
                return {'status': 'active', 'type': 'subscription'}
            
            elif revenue_stream == 'CONSULTING':
                # Custom consulting engagement
                logger.info(f"[FULFILLMENT] Consulting engagement for {customer_email}")
                return {'status': 'pending', 'type': 'consulting', 'message': 'Scheduling call...'}
            
            elif revenue_stream == 'PARTNERSHIPS':
                # Partnership revenue
                logger.info(f"[FULFILLMENT] Partnership setup for {customer_email}")
                return {'status': 'pending', 'type': 'partnership'}
            
            else:
                # Default: send report
                result = await trigger_autonomous_fulfillment(
                    customer_email=customer_email,
                    plan_id=plan_id,
                    plan_name='SINCOR Analysis',
                    order_id=f"stripe-{customer_email}",
                    amount=amount
                )
                return {'status': 'fulfilled', 'result': result}
        
        except Exception as e:
            logger.error(f"[FULFILLMENT] Error for {customer_email}: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _log_revenue_event(self, event_id: str, customer_email: str, revenue_stream: str,
                          amount: float, currency: str, payment_processor: str,
                          stripe_session_id: str, status: str):
        """Log a revenue event to the ledger"""
        
        conn = sqlite3.connect(self.revenue_ledger_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO revenue_events 
            (event_id, timestamp, customer_email, revenue_stream, amount, currency, 
             status, payment_processor, stripe_session_id, fulfillment_status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_id,
            datetime.utcnow().isoformat(),
            customer_email,
            revenue_stream,
            amount,
            currency,
            status,
            payment_processor,
            stripe_session_id,
            'pending',
            json.dumps({})
        ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"[LOGGED] Revenue event: {event_id} | ${amount}")
    
    def _update_revenue_event(self, event_id: str, fulfillment_status: str):
        """Update a revenue event with fulfillment status"""
        
        conn = sqlite3.connect(self.revenue_ledger_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE revenue_events 
            SET fulfillment_status = ?, status = ?
            WHERE event_id = ?
        ''', (fulfillment_status, 'completed', event_id))
        
        conn.commit()
        conn.close()
    
    def get_revenue_summary(self, days: int = 1) -> Dict[str, Any]:
        """Get revenue summary for the last N days"""
        
        conn = sqlite3.connect(self.revenue_ledger_path)
        cursor = conn.cursor()
        
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        cursor.execute('''
            SELECT 
                revenue_stream,
                COUNT(*) as transactions,
                SUM(amount) as total,
                AVG(amount) as avg_amount
            FROM revenue_events
            WHERE timestamp > ? AND status = 'completed'
            GROUP BY revenue_stream
            ORDER BY total DESC
        ''', (cutoff_date,))
        
        streams = cursor.fetchall()
        
        cursor.execute('''
            SELECT SUM(amount) FROM revenue_events
            WHERE timestamp > ? AND status = 'completed'
        ''', (cutoff_date,))
        
        total_revenue = cursor.fetchone()[0] or 0.0
        
        cursor.execute('''
            SELECT COUNT(*) FROM revenue_events
            WHERE timestamp > ? AND status = 'completed'
        ''', (cutoff_date,))
        
        transaction_count = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return {
            'total_revenue': total_revenue,
            'transaction_count': transaction_count,
            'by_stream': [
                {
                    'stream': s[0],
                    'transactions': s[1],
                    'total': s[2],
                    'average': s[3]
                }
                for s in streams
            ],
            'period_days': days
        }
    
    def get_mrr(self) -> float:
        """Calculate monthly recurring revenue"""
        
        conn = sqlite3.connect(self.revenue_ledger_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT SUM(amount) FROM revenue_events
            WHERE revenue_stream = 'SUBSCRIPTIONS' AND status = 'completed'
        ''')
        
        result = cursor.fetchone()[0] or 0.0
        conn.close()
        
        return result

    @staticmethod
    def _create_webbuilder_project(*, customer_email: str,
                                   customer_name: str,
                                   order: Dict[str, Any]) -> Dict[str, Any]:
        """Create the WebBuilder studio project (the non-idempotent half).

        Creates the project, stamps the buyer's phone into the stored
        discovery record, and rebuilds the draft lane. The caller persists
        the returned ``project_id`` before running the completion half so
        a settle retry can resume instead of building a second project.

        Raises on any failure so the caller marks the revenue event
        ``failed`` and records an alert.
        """
        from sincor2 import webbuilder_studio as _wb

        details = order.get("details") or {}
        business_name = (
            details.get("business_name") or customer_name or "Your Business"
        ).strip() or "Your Business"
        phone = (details.get("phone") or "").strip()
        territory = (details.get("territory") or "").strip()
        niche = (details.get("niche") or "").strip()
        prompt = (
            details.get("prompt") or f"Website for {business_name}"
        ).strip()

        project = _wb.create_project(
            name=business_name,
            niche=niche,
            territory=territory,
            owner_email=customer_email,
            prompt=prompt,
            source_type="none",
        )
        if not project or not project.get("id"):
            raise RuntimeError("webbuilder project creation failed")
        project_id = project["id"]

        # Stamp the buyer's phone into the stored discovery record, then
        # rebuild the draft lane so the rendered bundle carries the tel:
        # phone CTA alongside the business name and contact CTA.
        if phone:
            data = _wb._load()
            for proj in data.get("projects", []):
                if proj.get("id") == project_id:
                    (proj.setdefault("discovery", {}) or {})["phone"] = phone
            _wb._save(data)
            rebuilt = _wb.rebuild_draft(project_id)
            if not rebuilt.get("ok"):
                raise RuntimeError(
                    f"webbuilder draft rebuild failed: {rebuilt.get('error')}"
                )

        return {
            "project_id": project_id,
            "project_name": project.get("name", ""),
            "slug": project.get("slug", ""),
            "preview_url": project.get("preview_url", ""),
            "phone": phone,
        }

    @staticmethod
    def _complete_webbuilder_bundle(*, project: Dict[str, Any], phone: str,
                                    intake_id: str, customer_email: str,
                                    customer_name: str) -> Dict[str, Any]:
        """Finish the WebBuilder deliverable (idempotent across retries).

        Records the webbuilder_crm contact and builds the receipt. The CRM
        record is idempotent per project: an existing ``pack_purchase``
        contact (from a partially-completed earlier attempt) is reused
        instead of inserted twice.
        """
        from sincor2 import webbuilder_crm as _crm
        from sincor2 import webbuilder_studio as _wb

        project_id = project["id"]
        existing = [c for c in _crm.list_contacts(_wb.data_dir(), project_id)
                    if c.get("source") == "pack_purchase"]
        if existing:
            contact = existing[0]
            contact_id = contact.get("id") or contact.get("contact_id")
            logger.info(
                "[FULFILLMENT] WebBuilder CRM record already exists for "
                "project=%s — reusing contact=%s", project_id, contact_id)
        else:
            contact = _crm.record_contact(
                _wb.data_dir(),
                project_id=project_id,
                project_name=project.get("name", ""),
                owner_email=customer_email,
                name=customer_name,
                email=customer_email,
                phone=phone,
                message=f"WebBuilder Site purchase (intake {intake_id})",
                source="pack_purchase",
            )
            contact_id = contact.get("contact_id")

        site_dir = _wb._site_dir(project["slug"])
        draft_html = site_dir / "draft" / "index.html"
        logger.info(
            "[FULFILLMENT] WebBuilder bundle built: project=%s html=%s contact=%s",
            project_id, draft_html, contact_id,
        )
        return {
            "project_id": project_id,
            "project_name": project.get("name", ""),
            "slug": project.get("slug", ""),
            "preview_url": project.get("preview_url", ""),
            "html_path": str(draft_html),
            "contact_id": contact_id,
            "phone": phone,
        }

    @staticmethod
    def _build_webbuilder_bundle(*, intake_id: str, customer_email: str,
                                 customer_name: str,
                                 order: Dict[str, Any]) -> Dict[str, Any]:
        """Produce the zero-to-revenue 4.1 WebBuilder deliverable.

        Ported verbatim from xioix/z2r2-w4-webbuilder-sku (the Stripe-based
        branch) — reuses the webbuilder modules (no reimplementation):
        creates a WebBuilder studio project for the buyer, renders the
        static HTML site bundle carrying the business name / phone CTA /
        contact CTA, and writes a webbuilder_crm record for the project.

        A staticmethod (the body never used ``self``): the x402 rail calls
        it from ``sincor2.rail_routes._fulfill_rail_pack`` without
        constructing the Stripe-era orchestrator.

        Raises on any failure so the caller marks the revenue event
        ``failed`` and records an alert — exactly like the other packs.
        """
        created = RevenueOrchestrator._create_webbuilder_project(
            customer_email=customer_email,
            customer_name=customer_name,
            order=order,
        )
        from sincor2 import webbuilder_studio as _wb
        project = _wb.get_project(created["project_id"]) or {
            "id": created["project_id"], "slug": created.get("slug"),
            "name": created.get("project_name", ""),
            "preview_url": created.get("preview_url", ""),
        }
        return RevenueOrchestrator._complete_webbuilder_bundle(
            project=project,
            phone=created.get("phone", ""),
            intake_id=intake_id,
            customer_email=customer_email,
            customer_name=customer_name,
        )



# Initialize the orchestrator (lazy-loaded to avoid import-time failures)
orchestrator = None

def get_orchestrator():
    """Lazy-load orchestrator on first use"""
    global orchestrator
    if orchestrator is None:
        orchestrator = RevenueOrchestrator()
    return orchestrator


async def handle_stripe_webhook(stripe_event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle incoming Stripe webhook"""
    return await get_orchestrator().process_stripe_payment(stripe_event)


def get_dashboard_data() -> Dict[str, Any]:
    """Get all revenue metrics for dashboard"""
    
    orch = get_orchestrator()
    summary_1day = orch.get_revenue_summary(days=1)
    summary_30day = orch.get_revenue_summary(days=30)
    mrr = orch.get_mrr()
    
    return {
        'today': summary_1day,
        'last_30_days': summary_30day,
        'mrr': mrr,
        'timestamp': datetime.utcnow().isoformat()
    }
