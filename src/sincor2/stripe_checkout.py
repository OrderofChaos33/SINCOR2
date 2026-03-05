"""
Stripe Checkout Integration for SINCOR
Handles real payment processing for all subscription and one-time products
"""

import os
import json
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    logging.warning("[STRIPE] Stripe library not installed")

logger = logging.getLogger('sincor.stripe')

class StripeCheckout:
    """Handles Stripe payment processing"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Stripe checkout processor"""
        self.api_key = api_key or os.getenv('STRIPE_API_KEY')
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
                                is_subscription: bool = False,
                                interval: str = 'month') -> Dict:
        """
        Create a Stripe checkout session for a product

        Args:
            product_name: Name of the product (e.g., "SINCOR Starter")
            price_cents: Price in cents (e.g., 29700 for $297.00)
            quantity: Number of items
            customer_email: Customer email for receipt
            is_subscription: Whether this is a monthly subscription
            interval: Billing interval ('month' or 'year')

        Returns:
            Dict with session_id and checkout_url
        """
        if not self.enabled:
            logger.error("[STRIPE] Stripe not enabled")
            return {'success': False, 'error': 'Payment processor not available'}

        try:
            # Create a product
            product = stripe.Product.create(
                name=product_name,
                type='service'
            )

            # Create a price
            price_data = {
                'currency': 'usd',
                'unit_amount': price_cents,
                'product': product.id,
            }

            if is_subscription:
                price_data['recurring'] = {
                    'interval': interval,
                    'interval_count': 1
                }

            price = stripe.Price.create(**price_data)

            # Create checkout session
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': price.id,
                    'quantity': quantity,
                }],
                mode='subscription' if is_subscription else 'payment',
                success_url=os.getenv('STRIPE_SUCCESS_URL', 'https://getsincor.com/payment/success?session_id={CHECKOUT_SESSION_ID}'),
                cancel_url=os.getenv('STRIPE_CANCEL_URL', 'https://getsincor.com/payment/cancel'),
                customer_email=customer_email,
            )

            logger.info(f"[STRIPE] Created checkout session: {session.id} for {product_name}")

            return {
                'success': True,
                'session_id': session.id,
                'checkout_url': session.url,
                'product_id': product.id,
                'price_id': price.id,
            }

        except Exception as e:
            logger.error(f"[STRIPE] Checkout creation failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    def get_session(self, session_id: str) -> Dict:
        """Get checkout session details"""
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
        """Verify and process Stripe webhook"""
        if not self.enabled:
            return False, {}

        try:
            endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
            if not endpoint_secret:
                logger.error("[STRIPE] No webhook secret configured")
                return False, {}

            event = stripe.Webhook.construct_event(
                payload, sig_header, endpoint_secret
            )

            data = event['data']['object']

            if event['type'] == 'checkout.session.completed':
                return self._handle_session_completed(data)
            elif event['type'] == 'payment_intent.succeeded':
                return self._handle_payment_succeeded(data)
            elif event['type'] == 'customer.subscription.updated':
                return self._handle_subscription_updated(data)
            else:
                logger.info(f"[STRIPE] Unhandled event type: {event['type']}")
                return True, {'event_type': event['type']}

        except Exception as e:
            logger.error(f"[STRIPE] Webhook verification failed: {str(e)}")
            return False, {}

    def _handle_session_completed(self, session: Dict) -> Tuple[bool, Dict]:
        """Handle completed checkout session"""
        logger.info(f"[STRIPE] Session completed: {session['id']}")

        return True, {
            'session_id': session['id'],
            'customer_email': session.get('customer_email'),
            'amount_total': session.get('amount_total'),
            'currency': session.get('currency'),
            'subscription_id': session.get('subscription'),
            'event': 'payment_completed'
        }

    def _handle_payment_succeeded(self, payment: Dict) -> Tuple[bool, Dict]:
        """Handle successful payment intent"""
        logger.info(f"[STRIPE] Payment succeeded: {payment['id']}")

        return True, {
            'payment_id': payment['id'],
            'amount': payment.get('amount'),
            'currency': payment.get('currency'),
            'status': payment.get('status'),
            'event': 'payment_succeeded'
        }

    def _handle_subscription_updated(self, subscription: Dict) -> Tuple[bool, Dict]:
        """Handle subscription updates"""
        logger.info(f"[STRIPE] Subscription updated: {subscription['id']}")

        return True, {
            'subscription_id': subscription['id'],
            'status': subscription.get('status'),
            'current_period_end': subscription.get('current_period_end'),
            'event': 'subscription_updated'
        }

    def cancel_subscription(self, subscription_id: str) -> Tuple[bool, str]:
        """Cancel a subscription"""
        if not self.enabled:
            return False, "Payment processor not available"

        try:
            subscription = stripe.Subscription.delete(subscription_id)
            logger.info(f"[STRIPE] Subscription cancelled: {subscription_id}")
            return True, "Subscription cancelled successfully"
        except Exception as e:
            logger.error(f"[STRIPE] Subscription cancellation failed: {str(e)}")
            return False, str(e)

    def get_subscription(self, subscription_id: str) -> Optional[Dict]:
        """Get subscription details"""
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


def get_stripe_checkout(api_key: Optional[str] = None) -> StripeCheckout:
    """Factory function to get Stripe checkout processor"""
    return StripeCheckout(api_key)
