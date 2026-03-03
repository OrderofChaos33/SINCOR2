"""
SINCOR Stripe Payment Engine
Handles customer payments via Stripe (primary processor)
"""

import os
import logging
import stripe
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StripePaymentEngine:
    """Handles customer payments via Stripe"""

    def __init__(self):
        self.api_key = os.getenv('STRIPE_API_KEY')
        self.account_id = os.getenv('STRIPE_ACCOUNT_ID')
        if self.api_key:
            stripe.api_key = self.api_key
            logger.info("Stripe API configured")
        else:
            logger.warning("STRIPE_API_KEY not configured")

    def _call_params(self) -> Dict:
        """Return extra kwargs for Stripe API calls (org key support)"""
        params = {}
        if self.account_id:
            params['stripe_account'] = self.account_id
        return params

    def create_payment_intent(self, amount_cents: int, description: str,
                             customer_email: str = None, metadata: Dict[str, Any] = None) -> Dict:
        """
        Create a Stripe PaymentIntent for customer payment

        Args:
            amount_cents: Amount in cents (e.g., 1250 for $12.50)
            description: Payment description
            customer_email: Customer email address
            metadata: Additional metadata to attach to payment

        Returns:
            Payment intent details including client_secret and status
        """
        try:
            intent_params = {
                'amount': int(amount_cents),
                'currency': 'usd',
                'description': description,
                'payment_method_types': ['card'],  # Explicitly enable card payments
                'automatic_payment_methods': {
                    'enabled': False  # Disable auto since we're specifying card
                }
            }

            if customer_email:
                intent_params['receipt_email'] = customer_email

            if metadata:
                intent_params['metadata'] = metadata

            intent = stripe.PaymentIntent.create(**intent_params, **self._call_params())

            logger.info(f"Created PaymentIntent {intent.id}: ${amount_cents/100:.2f}")

            return {
                'status': 'success',
                'payment_intent_id': intent.id,
                'client_secret': intent.client_secret,
                'amount': amount_cents / 100,
                'currency': 'usd',
                'status': intent.status,
                'created_at': datetime.utcnow().isoformat()
            }

        except stripe.error.CardError as e:
            logger.error(f"Card error: {e.user_message}")
            return {
                'status': 'error',
                'message': f"Card error: {e.user_message}",
                'amount': amount_cents / 100
            }
        except stripe.error.RateLimitError:
            logger.error("Rate limit exceeded")
            return {
                'status': 'error',
                'message': 'Rate limit exceeded. Try again later.',
                'amount': amount_cents / 100
            }
        except stripe.error.InvalidRequestError as e:
            logger.error(f"Invalid request: {e.user_message}")
            return {
                'status': 'error',
                'message': f"Invalid request: {e.user_message}",
                'amount': amount_cents / 100
            }
        except stripe.error.AuthenticationError:
            logger.error("Invalid Stripe API key")
            return {
                'status': 'error',
                'message': 'Stripe authentication failed',
                'amount': amount_cents / 100
            }
        except stripe.error.APIConnectionError:
            logger.error("Network error connecting to Stripe")
            return {
                'status': 'error',
                'message': 'Network error. Please try again.',
                'amount': amount_cents / 100
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error: {e}")
            return {
                'status': 'error',
                'message': f"Payment error: {str(e)}",
                'amount': amount_cents / 100
            }
        except Exception as e:
            logger.error(f"Error creating payment intent: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'amount': amount_cents / 100
            }

    def retrieve_payment_intent(self, payment_intent_id: str) -> Dict:
        """Retrieve status of a PaymentIntent"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, **self._call_params())

            return {
                'status': 'success',
                'payment_intent_id': intent.id,
                'amount': intent.amount / 100,
                'currency': intent.currency,
                'payment_status': intent.status,
                'created_at': intent.created
            }

        except stripe.error.InvalidRequestError as e:
            logger.error(f"Payment intent not found: {e}")
            return {
                'status': 'error',
                'message': 'Payment intent not found',
                'payment_intent_id': payment_intent_id
            }
        except Exception as e:
            logger.error(f"Error retrieving payment intent: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'payment_intent_id': payment_intent_id
            }

    def confirm_payment(self, payment_intent_id: str) -> Dict:
        """Confirm a payment after successful client-side confirmation"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, **self._call_params())

            if intent.status == 'succeeded':
                logger.info(f"Payment confirmed: {payment_intent_id}")
                return {
                    'status': 'success',
                    'message': 'Payment confirmed',
                    'payment_intent_id': payment_intent_id,
                    'amount': intent.amount / 100,
                    'charge_id': intent.charges.data[0].id if intent.charges.data else None
                }
            elif intent.status == 'processing':
                return {
                    'status': 'processing',
                    'message': 'Payment is processing',
                    'payment_intent_id': payment_intent_id,
                    'amount': intent.amount / 100
                }
            elif intent.status == 'requires_payment_method':
                return {
                    'status': 'error',
                    'message': 'Payment method required',
                    'payment_intent_id': payment_intent_id
                }
            else:
                return {
                    'status': 'error',
                    'message': f'Payment status: {intent.status}',
                    'payment_intent_id': payment_intent_id
                }

        except Exception as e:
            logger.error(f"Error confirming payment: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'payment_intent_id': payment_intent_id
            }

    def refund_payment(self, charge_id: str, amount_cents: Optional[int] = None) -> Dict:
        """Refund a charge (partial or full)"""
        try:
            refund_params = {'charge': charge_id}
            if amount_cents:
                refund_params['amount'] = int(amount_cents)

            refund = stripe.Refund.create(**refund_params, **self._call_params())

            logger.info(f"Refund created: {refund.id} for charge {charge_id}")

            return {
                'status': 'success',
                'refund_id': refund.id,
                'charge_id': charge_id,
                'amount': refund.amount / 100,
                'status': refund.status
            }

        except stripe.error.InvalidRequestError as e:
            logger.error(f"Invalid refund request: {e}")
            return {
                'status': 'error',
                'message': f"Invalid refund: {e.user_message}",
                'charge_id': charge_id
            }
        except Exception as e:
            logger.error(f"Error refunding payment: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'charge_id': charge_id
            }

    def get_payment_methods(self, customer_id: str) -> Dict:
        """Get all payment methods for a customer"""
        try:
            methods = stripe.PaymentMethod.list(
                customer=customer_id,
                type='card',
                **self._call_params()
            )

            return {
                'status': 'success',
                'customer_id': customer_id,
                'payment_methods': [
                    {
                        'id': method.id,
                        'type': method.type,
                        'card': {
                            'brand': method.card.brand,
                            'last4': method.card.last4,
                            'exp_month': method.card.exp_month,
                            'exp_year': method.card.exp_year
                        } if method.card else None
                    }
                    for method in methods.data
                ]
            }

        except Exception as e:
            logger.error(f"Error getting payment methods: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'customer_id': customer_id
            }
