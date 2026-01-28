import os
from typing import Dict, Optional
from datetime import datetime

class BillingManager:
    def __init__(self):
        # Stripe is deprecated but can be re-enabled in compatibility mode by
        # setting STRIPE_SECRET_KEY and installing the official 'stripe' package.
        # Prefer PayPal subscriptions or on-chain payment solutions.
        self.stripe_enabled = False
        self.stripe = None
        if os.getenv("STRIPE_SECRET_KEY"):
            try:
                import stripe as stripe_module
                stripe_module.api_key = os.getenv("STRIPE_SECRET_KEY")
                self.stripe = stripe_module
                self.stripe_enabled = True
            except Exception:
                # Stripe package not installed or failed to initialize
                self.stripe = None
                self.stripe_enabled = False
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    def _ensure_stripe_enabled(self):
        if not self.stripe_enabled:
            raise NotImplementedError("Stripe support is not enabled. Use PayPal subscriptions or on-chain payments, or set STRIPE_SECRET_KEY to re-enable compatibility mode.")
    
    async def create_customer(self, email: str, name: str = None, metadata: Dict = None) -> str:
        """Create a new customer in Stripe (compatibility mode only)."""
        self._ensure_stripe_enabled()
        customer_data = {"email": email}
        if name:
            customer_data["name"] = name
        if metadata:
            customer_data["metadata"] = metadata
            
        customer = self.stripe.Customer.create(**customer_data)
        return customer.id
    
    async def create_subscription(self, customer_id: str, price_id: str, 
                                trial_days: int = None, metadata: Dict = None) -> Dict:
        """Create a new subscription in Stripe (compatibility mode only)."""
        self._ensure_stripe_enabled()
        subscription_data = {
            "customer": customer_id,
            "items": [{"price": price_id}],
            "payment_behavior": "default_incomplete",
            "expand": ["latest_invoice.payment_intent"]
        }
        
        if trial_days:
            subscription_data["trial_period_days"] = trial_days
            
        if metadata:
            subscription_data["metadata"] = metadata
            
        subscription = self.stripe.Subscription.create(**subscription_data)
        
        return {
            "subscription_id": subscription.id,
            "client_secret": subscription.latest_invoice.payment_intent.client_secret,
            "status": subscription.status,
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
            "trial_end": datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None
        }
    
    async def cancel_subscription(self, subscription_id: str, at_period_end: bool = True) -> Dict:
        """Cancel a subscription in Stripe (compatibility mode only)."""
        self._ensure_stripe_enabled()
        if at_period_end:
            subscription = self.stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )
        else:
            subscription = self.stripe.Subscription.delete(subscription_id)
            
        return {
            "subscription_id": subscription_id,
            "status": subscription.status,
            "cancelled_at": datetime.now() if not at_period_end else None,
            "cancel_at_period_end": subscription.cancel_at_period_end
        }
    
    async def get_subscription(self, subscription_id: str) -> Dict:
        """Get subscription details (Stripe compatibility mode only)."""
        self._ensure_stripe_enabled()
        subscription = self.stripe.Subscription.retrieve(subscription_id)
        
        return {
            "subscription_id": subscription.id,
            "customer_id": subscription.customer,
            "status": subscription.status,
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
            "trial_end": datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "cancelled_at": datetime.fromtimestamp(subscription.canceled_at) if subscription.canceled_at else None
        }
    
    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        """Verify webhook signature (Stripe compatibility only). Returns False if Stripe not enabled."""
        if not self.stripe_enabled:
            return False
        try:
            self.stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
            return True
        except ValueError:
            return False
        except Exception:
            return False
    
    def parse_webhook_event(self, payload: str) -> Dict:
        """Parse webhook event (Stripe compatibility only)."""
        if not self.stripe_enabled:
            raise NotImplementedError("Webhook parsing for Stripe is disabled. Use PayPal webhook handlers or enable STRIPE_SECRET_KEY for compatibility.")
        return self.stripe.Event.construct_from(payload, self.stripe.api_key)