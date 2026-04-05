"""
Stripe Checkout Integration for SINCOR
Handles real payment processing for all subscription and one-time products
"""

import os
import logging
from typing import Dict, Optional, Tuple

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    logging.warning("[STRIPE] Stripe library not installed")

logger = logging.getLogger('sincor.stripe')


def _safe_success_url() -> str:
    """Hardcoded success URL — avoids Railway env var encoding issues."""
    return 'https://getsincor.com/payment/success?session_id={CHECKOUT_SESSION_ID}'


class StripeCheckout:
    """Handles Stripe payment processing"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (
            api_key
            or os.getenv('STRIPE_API_KEY')
            or os.getenv('STRIPE_API_SECRET')
            or os.getenv('STRIPE_SECRET_KEY')
            or os.getenv('STRIPE_SECRET')
        )
        self.mode = 'test' if 'sk_test' in (self.api_key or '') else 'live'

        if self.api_key and STRIPE_AVAILABLE:
            stripe.api_key = self.api_key
            self.enabled = True
            logger.info(f"[STRIPE] Initialized in {self.mode} mode")
        else:
            self.enabled = False
            logger.warning("[STRIPE] Stripe not available or no API key configured")

    def create_checkout_session(self, product_name: str, price_cents: int,
                                quantity: int = 1, customer_email: str = None,
                                is_subscription: bool = True,
                                interval: str = 'month',
                                price_id: str = None,
                                plan_id: str = None) -> Dict:
        """
        Create a Stripe checkout session.
        Subscriptions: charge $10 now, renews at full price after 30 days.
        """
        if not self.enabled:
            return {'success': False, 'error': 'Payment processor not available'}

        try:
            if price_id:
                line_item = {'price': price_id, 'quantity': quantity}
            else:
                price_data_obj: Dict = {
                    'currency': 'usd',
                    'unit_amount': price_cents,
                    'product_data': {'name': product_name},
                }
                if is_subscription:
                    price_data_obj['recurring'] = {'interval': interval}
                line_item = {'price_data': price_data_obj, 'quantity': quantity}

            meta = {
                'plan_id': plan_id or '',
                'plan_name': product_name or '',
            }

            session_params: Dict = {
                'payment_method_types': ['card'],
                'line_items': [line_item],
                'mode': 'subscription' if is_subscription else 'payment',
                'success_url': _safe_success_url(),
                'cancel_url': 'https://getsincor.com/buy',
                'allow_promotion_codes': True,
                'metadata': meta,
            }

            if customer_email:
                session_params['customer_email'] = customer_email

            if is_subscription:
                # Charge immediately on subscription start (no trial, no intro pricing)
                # Customer pays $149 now, then $149/month recurring
                session_params['subscription_data'] = {
                    'metadata': meta,
                }

            session = stripe.checkout.Session.create(**session_params)
            logger.info(f"[STRIPE] Created session: {session.id} for {product_name}")

            return {
                'success': True,
                'session_id': session.id,
                'checkout_url': session.url,
            }

        except Exception as e:
            logger.error(f"[STRIPE] Checkout creation failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    def get_session(self, session_id: str) -> Dict:
        if not self.enabled:
            return None
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                'session_id': session.id,
                'customer_email': session.customer_email,
                'payment_status': session.payment_status,
                'status': session.status,
                'amount_total': session.amount_total,
                'currency': session.currency,
                'customer_id': session.customer,
                'subscription_id': session.subscription,
            }
        except Exception as e:
            logger.error(f"[STRIPE] Session retrieval failed: {str(e)}")
            return None

    def verify_webhook(self, payload: bytes, sig_header: str) -> Tuple[bool, Dict]:
        if not self.enabled:
            return False, {}
        try:
            endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
            if not endpoint_secret:
                logger.error("[STRIPE] No webhook secret configured")
                return False, {}

            event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
            data = event['data']['object']
            handlers = {
                'checkout.session.completed':    self._handle_session_completed,
                'checkout.session.expired':      self._handle_session_expired,
                'invoice.paid':                  self._handle_invoice_paid,
                'invoice.payment_failed':        self._handle_invoice_payment_failed,
                'customer.subscription.updated': self._handle_subscription_updated,
                'customer.subscription.deleted': self._handle_subscription_deleted,
                'payment_intent.succeeded':      self._handle_payment_succeeded,
            }
            handler = handlers.get(event['type'])
            if handler:
                return handler(data)
            logger.info(f"[STRIPE] Unhandled event type: {event['type']}")
            return True, {'event_type': event['type']}

        except Exception as e:
            logger.error(f"[STRIPE] Webhook verification failed: {str(e)}")
            return False, {}

    def _handle_session_completed(self, session: Dict) -> Tuple[bool, Dict]:
        logger.info(f"[STRIPE] Session completed: {session['id']}")
        return True, {
            'event': 'payment_completed',
            'session_id': session['id'],
            'customer_email': session.get('customer_email') or session.get('customer_details', {}).get('email'),
            'customer_id': session.get('customer'),
            'amount_total': session.get('amount_total'),
            'currency': session.get('currency'),
            'subscription_id': session.get('subscription'),
            'metadata': session.get('metadata') or {},
        }

    def _handle_session_expired(self, session: Dict) -> Tuple[bool, Dict]:
        logger.info(f"[STRIPE] Session expired: {session['id']}")
        return True, {
            'event': 'checkout_abandoned',
            'session_id': session['id'],
            'customer_email': session.get('customer_email') or session.get('customer_details', {}).get('email'),
            'amount_total': session.get('amount_total'),
        }

    def _handle_invoice_paid(self, invoice: Dict) -> Tuple[bool, Dict]:
        logger.info(f"[STRIPE] Invoice paid: {invoice['id']}")
        return True, {
            'event': 'invoice_paid',
            'invoice_id': invoice['id'],
            'subscription_id': invoice.get('subscription'),
            'customer_id': invoice.get('customer'),
            'customer_email': invoice.get('customer_email'),
            'amount_paid': invoice.get('amount_paid'),
        }

    def _handle_invoice_payment_failed(self, invoice: Dict) -> Tuple[bool, Dict]:
        logger.warning(f"[STRIPE] Invoice payment failed: {invoice['id']}")
        return True, {
            'event': 'invoice_payment_failed',
            'invoice_id': invoice['id'],
            'subscription_id': invoice.get('subscription'),
            'customer_id': invoice.get('customer'),
            'customer_email': invoice.get('customer_email'),
            'amount_due': invoice.get('amount_due'),
            'next_payment_attempt': invoice.get('next_payment_attempt'),
        }

    def _handle_subscription_updated(self, subscription: Dict) -> Tuple[bool, Dict]:
        logger.info(f"[STRIPE] Subscription updated: {subscription['id']}")
        return True, {
            'event': 'subscription_updated',
            'subscription_id': subscription['id'],
            'status': subscription.get('status'),
            'customer_id': subscription.get('customer'),
            'current_period_end': subscription.get('current_period_end'),
            'cancel_at_period_end': subscription.get('cancel_at_period_end'),
        }

    def _handle_subscription_deleted(self, subscription: Dict) -> Tuple[bool, Dict]:
        logger.info(f"[STRIPE] Subscription cancelled: {subscription['id']}")
        return True, {
            'event': 'subscription_cancelled',
            'subscription_id': subscription['id'],
            'customer_id': subscription.get('customer'),
            'ended_at': subscription.get('ended_at'),
        }

    def _handle_payment_succeeded(self, payment: Dict) -> Tuple[bool, Dict]:
        logger.info(f"[STRIPE] Payment succeeded: {payment['id']}")
        return True, {
            'event': 'payment_succeeded',
            'payment_id': payment['id'],
            'amount': payment.get('amount'),
            'currency': payment.get('currency'),
        }

    def cancel_subscription(self, subscription_id: str) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "Payment processor not available"
        try:
            stripe.Subscription.delete(subscription_id)
            logger.info(f"[STRIPE] Subscription cancelled: {subscription_id}")
            return True, "Subscription cancelled successfully"
        except Exception as e:
            logger.error(f"[STRIPE] Cancellation failed: {str(e)}")
            return False, str(e)

    def get_subscription(self, subscription_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        try:
            sub = stripe.Subscription.retrieve(subscription_id)
            return {
                'subscription_id': sub.id,
                'status': sub.status,
                'customer': sub.customer,
                'current_period_start': sub.current_period_start,
                'current_period_end': sub.current_period_end,
                'amount': sub.items.data[0].price.unit_amount if sub.items.data else 0,
                'currency': sub.items.data[0].price.currency if sub.items.data else 'usd',
            }
        except Exception as e:
            logger.error(f"[STRIPE] Subscription retrieval failed: {str(e)}")
            return None

    def create_portal_session(self, customer_id: str, return_url: str = None) -> Dict:
        if not self.enabled:
            return {'success': False, 'error': 'Payment processor not available'}
        try:
            portal = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url or 'https://getsincor.com/',
            )
            logger.info(f"[STRIPE] Portal session created for customer: {customer_id}")
            return {'success': True, 'portal_url': portal.url}
        except Exception as e:
            logger.error(f"[STRIPE] Portal session creation failed: {str(e)}")
            return {'success': False, 'error': str(e)}


def get_stripe_checkout(api_key: Optional[str] = None) -> StripeCheckout:
    return StripeCheckout(api_key)
