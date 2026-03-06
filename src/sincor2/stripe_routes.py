"""
Stripe Checkout Routes for SINCOR
Handles payment creation, webhooks, and Customer Portal
"""

import os
import json
import sqlite3
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, redirect
from functools import wraps

logger = logging.getLogger('sincor.stripe_routes')

stripe_bp = Blueprint('stripe', __name__, url_prefix='/api/stripe')

# ----- Abandoned checkout SQLite helpers -----

def _get_abandoned_db():
    db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'abandoned_checkouts.db')
    conn = sqlite3.connect(db_path)
    conn.execute('''CREATE TABLE IF NOT EXISTS abandoned_checkouts (
        session_id TEXT PRIMARY KEY,
        email TEXT,
        plan_name TEXT,
        price_cents INTEGER,
        billing TEXT,
        created_at TEXT,
        recovered INTEGER DEFAULT 0
    )''')
    conn.commit()
    return conn

def _save_abandoned(session_id, email, plan_name, price_cents, billing):
    try:
        conn = _get_abandoned_db()
        conn.execute(
            'INSERT OR REPLACE INTO abandoned_checkouts (session_id, email, plan_name, price_cents, billing, created_at) VALUES (?,?,?,?,?,?)',
            (session_id, email, plan_name, price_cents, billing, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[ABANDONED] Could not save to DB: {e}")

def _mark_recovered(session_id):
    try:
        conn = _get_abandoned_db()
        conn.execute('UPDATE abandoned_checkouts SET recovered=1 WHERE session_id=?', (session_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def _lookup_abandoned(session_id):
    try:
        conn = _get_abandoned_db()
        row = conn.execute(
            'SELECT email, plan_name, price_cents, billing FROM abandoned_checkouts WHERE session_id=? AND recovered=0',
            (session_id,)
        ).fetchone()
        conn.close()
        if row:
            return {'email': row[0], 'plan_name': row[1], 'price_cents': row[2], 'billing': row[3]}
    except Exception as e:
        logger.warning(f"[ABANDONED] Could not lookup: {e}")
    return None


def init_stripe_routes(app, stripe_processor):
    """Initialize Stripe routes with Flask app"""

    @stripe_bp.route('/checkout', methods=['POST'])
    def create_checkout():
        """Create a Stripe checkout session (subscription, supports annual billing)"""
        try:
            data = request.get_json()
            plan_id        = data.get('plan_id')
            plan_name      = data.get('plan_name')
            price_cents    = data.get('price_cents')
            customer_email = data.get('customer_email') or ''
            billing        = data.get('billing', 'month')   # 'month' or 'year'
            is_subscription = data.get('is_subscription', True)

            if not all([plan_id, plan_name, price_cents]):
                return jsonify({'success': False, 'error': 'Missing required fields'}), 400

            result = stripe_processor.create_checkout_session(
                product_name=plan_name,
                price_cents=price_cents,
                is_subscription=is_subscription,
                interval=billing,
                customer_email=customer_email or None,
            )

            if result.get('success'):
                # Track for abandoned checkout recovery
                if customer_email:
                    _save_abandoned(result['session_id'], customer_email, plan_name, price_cents, billing)
                logger.info(f"[STRIPE] Checkout session created: {plan_id} ({billing})")
                return jsonify({
                    'success': True,
                    'session_id': result['session_id'],
                    'checkout_url': result['checkout_url']
                })
            else:
                logger.error(f"[STRIPE] Checkout creation failed: {result.get('error')}")
                return jsonify({'success': False, 'error': result.get('error', 'Failed to create checkout')}), 400

        except Exception as e:
            logger.error(f"[STRIPE] Checkout endpoint error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/webhook', methods=['POST'])
    def webhook():
        """Handle Stripe webhooks for subscription lifecycle"""
        try:
            payload    = request.get_data()
            sig_header = request.headers.get('Stripe-Signature')

            success, event_data = stripe_processor.verify_webhook(payload, sig_header)

            if success:
                event_type = event_data.get('event', 'unknown')
                logger.info(f"[STRIPE] Webhook received: {event_type}")
                _process_payment_event(event_data)
                return jsonify({'success': True}), 200
            else:
                logger.warning("[STRIPE] Webhook verification failed")
                return jsonify({'success': False, 'error': 'Verification failed'}), 400

        except Exception as e:
            logger.error(f"[STRIPE] Webhook error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/portal', methods=['POST'])
    def customer_portal():
        """Create a Stripe Customer Portal session and redirect"""
        try:
            data        = request.get_json()
            customer_id = data.get('customer_id')
            return_url  = data.get('return_url', 'https://getsincor.com/')

            if not customer_id:
                return jsonify({'success': False, 'error': 'customer_id required'}), 400

            result = stripe_processor.create_portal_session(customer_id, return_url)
            if result.get('success'):
                return jsonify({'success': True, 'portal_url': result['portal_url']})
            else:
                return jsonify({'success': False, 'error': result.get('error')}), 400
        except Exception as e:
            logger.error(f"[STRIPE] Portal error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/session/<session_id>', methods=['GET'])
    def get_session_details(session_id):
        """Get checkout session details"""
        try:
            session = stripe_processor.get_session(session_id)
            if session:
                return jsonify({'success': True, 'session': session})
            return jsonify({'success': False, 'error': 'Session not found'}), 404
        except Exception as e:
            logger.error(f"[STRIPE] Session retrieval error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @stripe_bp.route('/cancel/<subscription_id>', methods=['POST'])
    def cancel_subscription_route(subscription_id):
        """Cancel a subscription"""
        try:
            success, message = stripe_processor.cancel_subscription(subscription_id)
            if success:
                return jsonify({'success': True, 'message': message})
            return jsonify({'success': False, 'error': message}), 400
        except Exception as e:
            logger.error(f"[STRIPE] Cancellation error: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    app.register_blueprint(stripe_bp)


def _send_email(to_email: str, subject: str, html: str, text: str = ''):
    """Send email via SendGrid or SMTP fallback"""
    try:
        from sincor2.email_sender import EmailSender
        sender = EmailSender()
        sender.send_email(
            to_email=to_email,
            to_name='',
            subject=subject,
            html_content=html,
            text_content=text or subject,
        )
        logger.info(f"[EMAIL] Sent '{subject}' to {to_email}")
    except Exception as e:
        logger.warning(f"[EMAIL] Could not send email: {e}")


def _process_payment_event(event_data):
    """Process Stripe events — fulfill orders, send emails, handle lifecycle"""
    event_type     = event_data.get('event')
    customer_email = event_data.get('customer_email', '')

    if event_type == 'payment_completed':
        session_id = event_data.get('session_id', '')
        amount     = (event_data.get('amount_total') or 0) / 100
        logger.info(f"[FULFILLMENT] New subscriber: {customer_email} — ${amount:.2f}")

        # Mark as recovered so abandoned emails don't fire
        _mark_recovered(session_id)

        if customer_email:
            _send_email(
                to_email=customer_email,
                subject="Welcome to SINCOR — You're in! 🚀",
                html=f"""
                <h2>Welcome aboard!</h2>
                <p>Your SINCOR subscription is now active. Our AI agents are warming up.</p>
                <p><strong>What happens next:</strong></p>
                <ol>
                  <li>Your dashboard is live at <a href="https://getsincor.com/">getsincor.com</a></li>
                  <li>AI agents activate within 24 hours</li>
                  <li>You'll receive your training materials shortly</li>
                </ol>
                <p>Questions? Reply to this email or visit <a href="https://getsincor.com/">getsincor.com</a></p>
                <p>— The SINCOR Team</p>
                """,
                text=f"Welcome to SINCOR! Your subscription is active. Visit https://getsincor.com/ to get started.",
            )

    elif event_type == 'invoice_paid':
        sub_id = event_data.get('subscription_id', '')
        amount = (event_data.get('amount_paid') or 0) / 100
        logger.info(f"[BILLING] Recurring invoice paid: {sub_id} — ${amount:.2f}")
        # No email for routine renewals to avoid noise

    elif event_type == 'invoice_payment_failed':
        sub_id = event_data.get('subscription_id', '')
        logger.warning(f"[BILLING] Payment failed for subscription: {sub_id}")
        if customer_email:
            _send_email(
                to_email=customer_email,
                subject="Action Required: SINCOR payment failed",
                html=f"""
                <h2>Payment issue — action required</h2>
                <p>We couldn't process your SINCOR subscription renewal.</p>
                <p>Please update your payment method to keep your AI agents running:</p>
                <p><a href="https://getsincor.com/billing" style="background:#7c3aed;color:white;padding:10px 20px;border-radius:6px;text-decoration:none;">Update Payment Method</a></p>
                <p>Your account remains active while we retry. If you need help, reply to this email.</p>
                """,
                text="Your SINCOR payment failed. Visit https://getsincor.com/billing to update your payment method.",
            )

    elif event_type == 'checkout_abandoned':
        session_id = event_data.get('session_id', '')
        email      = customer_email or ''

        # Try to get email from our DB if Stripe didn't have it
        if not email:
            row = _lookup_abandoned(session_id)
            if row:
                email = row.get('email', '')

        if email:
            _mark_recovered(session_id)
            logger.info(f"[ABANDONED] Sending recovery email to {email}")
            _send_email(
                to_email=email,
                subject="You left something behind — come back to SINCOR",
                html=f"""
                <h2>Did something come up?</h2>
                <p>You were SO close to activating your SINCOR AI agents.</p>
                <p>Your 30-day free trial is still waiting for you — no charge until the trial ends.</p>
                <p><a href="https://getsincor.com/buy" style="background:#7c3aed;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;">Claim Your Free Trial →</a></p>
                <p style="color:#888;font-size:12px;">This offer expires in 48 hours.</p>
                """,
                text="Your SINCOR 30-day free trial is still available. Visit https://getsincor.com/buy to claim it.",
            )

    elif event_type == 'subscription_cancelled':
        sub_id = event_data.get('subscription_id', '')
        logger.info(f"[BILLING] Subscription cancelled: {sub_id}")
        if customer_email:
            _send_email(
                to_email=customer_email,
                subject="Your SINCOR subscription has been cancelled",
                html=f"""
                <h2>We're sorry to see you go</h2>
                <p>Your SINCOR subscription has been cancelled. Your access remains active until the end of your billing period.</p>
                <p>Changed your mind? <a href="https://getsincor.com/buy">Reactivate anytime</a>.</p>
                <p>— The SINCOR Team</p>
                """,
                text="Your SINCOR subscription has been cancelled. You can reactivate at https://getsincor.com/buy",
            )

    elif event_type == 'subscription_updated':
        logger.info(f"[BILLING] Subscription updated: {event_data.get('subscription_id')}")

