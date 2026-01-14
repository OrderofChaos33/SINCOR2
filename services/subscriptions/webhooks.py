from datetime import datetime
import json
from libs.pkg_bus.bus import xadd, redis

async def handle_subscription_updated(event_data: dict, db_conn):
    """Handle subscription.updated webhook"""
    subscription = event_data['object']
    subscription_id = subscription['id']
    
    # Update local subscription record
    await db_conn.execute("""
        UPDATE subscriptions 
        SET 
            status = $1,
            current_period_start = $2,
            current_period_end = $3,
            updated_at = now()
        WHERE stripe_subscription_id = $4
    """, 
        subscription['status'],
        datetime.fromtimestamp(subscription['current_period_start']),
        datetime.fromtimestamp(subscription['current_period_end']),
        subscription_id
    )
    
    # Emit event
    r = await redis()
    await xadd(r, "stream.subscription.updated", {
        "subscription_id": subscription_id,
        "status": subscription['status'],
        "customer_id": subscription['customer']
    })

async def handle_invoice_payment_succeeded(event_data: dict, db_conn):
    """Handle successful payment"""
    invoice = event_data['object']
    subscription_id = invoice['subscription']
    
    if subscription_id:
        # Update subscription status
        await db_conn.execute("""
            UPDATE subscriptions 
            SET status = 'active', updated_at = now()
            WHERE stripe_subscription_id = $1
        """, subscription_id)
        
        # Emit event
        r = await redis()
        await xadd(r, "stream.subscription.renewed", {
            "subscription_id": subscription_id,
            "amount_paid": invoice['amount_paid'],
            "currency": invoice['currency']
        })

async def handle_invoice_payment_failed(event_data: dict, db_conn):
    """Handle failed payment"""
    invoice = event_data['object']
    subscription_id = invoice['subscription']
    
    if subscription_id:
        # Update subscription status
        await db_conn.execute("""
            UPDATE subscriptions 
            SET status = 'past_due', updated_at = now()
            WHERE stripe_subscription_id = $1
        """, subscription_id)
        
        # Emit event
        r = await redis()
        await xadd(r, "stream.subscription.failed", {
            "subscription_id": subscription_id,
            "amount_due": invoice['amount_due'],
            "attempt_count": invoice['attempt_count']
        })

async def handle_customer_subscription_deleted(event_data: dict, db_conn):
    """Handle subscription cancellation"""
    subscription = event_data['object']
    subscription_id = subscription['id']
    
    # Update subscription status
    await db_conn.execute("""
        UPDATE subscriptions 
        SET 
            status = 'cancelled',
            cancelled_at = now(),
            updated_at = now()
        WHERE stripe_subscription_id = $1
    """, subscription_id)
    
    # Emit event
    r = await redis()
    await xadd(r, "stream.subscription.canceled", {
        "subscription_id": subscription_id,
        "customer_id": subscription['customer'],
        "cancelled_at": datetime.now().isoformat()
    })

# Webhook handler mapping
WEBHOOK_HANDLERS = {
    'customer.subscription.updated': handle_subscription_updated,
    'invoice.payment_succeeded': handle_invoice_payment_succeeded,
    'invoice.payment_failed': handle_invoice_payment_failed,
    'customer.subscription.deleted': handle_customer_subscription_deleted
}