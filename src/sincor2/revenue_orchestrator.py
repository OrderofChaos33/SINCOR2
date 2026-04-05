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


# Initialize the orchestrator
orchestrator = RevenueOrchestrator()


async def handle_stripe_webhook(stripe_event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle incoming Stripe webhook"""
    return await orchestrator.process_stripe_payment(stripe_event)


def get_dashboard_data() -> Dict[str, Any]:
    """Get all revenue metrics for dashboard"""
    
    summary_1day = orchestrator.get_revenue_summary(days=1)
    summary_30day = orchestrator.get_revenue_summary(days=30)
    mrr = orchestrator.get_mrr()
    
    return {
        'today': summary_1day,
        'last_30_days': summary_30day,
        'mrr': mrr,
        'timestamp': datetime.utcnow().isoformat()
    }
