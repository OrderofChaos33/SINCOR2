import stripe
import os
from typing import Dict, Optional
from datetime import datetime

class BillingManager:
    def __init__(self):
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    
    async def create_customer(self, email: str, name: str = None, metadata: Dict = None) -> str:
        """Create a new Stripe customer"""
        customer_data = {"email": email}
        if name:
            customer_data["name"] = name
        if metadata:
            customer_data["metadata"] = metadata
            
        customer = stripe.Customer.create(**customer_data)
        return customer.id
    
    async def create_subscription(self, customer_id: str, price_id: str, 
                                trial_days: int = None, metadata: Dict = None) -> Dict:
        """Create a new subscription"""
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
            
        subscription = stripe.Subscription.create(**subscription_data)
        
        return {
            "subscription_id": subscription.id,
            "client_secret": subscription.latest_invoice.payment_intent.client_secret,
            "status": subscription.status,
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
            "trial_end": datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None
        }
    
    async def cancel_subscription(self, subscription_id: str, at_period_end: bool = True) -> Dict:
        """Cancel a subscription"""
        if at_period_end:
            subscription = stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )
        else:
            subscription = stripe.Subscription.delete(subscription_id)
            
        return {
            "subscription_id": subscription_id,
            "status": subscription.status,
            "cancelled_at": datetime.now() if not at_period_end else None,
            "cancel_at_period_end": subscription.cancel_at_period_end
        }
    
    async def get_subscription(self, subscription_id: str) -> Dict:
        """Get subscription details"""
        subscription = stripe.Subscription.retrieve(subscription_id)
        
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
        """Verify Stripe webhook signature"""
        try:
            stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
            return True
        except ValueError:
            return False
        except stripe.error.SignatureVerificationError:
            return False
    
    def parse_webhook_event(self, payload: str) -> Dict:
        """Parse Stripe webhook event"""
        return stripe.Event.construct_from(payload, stripe.api_key)