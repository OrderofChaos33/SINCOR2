"""
SINCOR PayPal Payment Integration - Synchronous Wrappers
Adds sync methods for use with Flask (non-async framework)
"""

import asyncio
from paypal_integration import (
    PayPalIntegration,
    SINCORPaymentProcessor,
    PaymentRequest,
    PaymentResult,
    PaymentStatus
)

class PayPalIntegrationSync(PayPalIntegration):
    """Synchronous wrapper for PayPal integration"""

    def get_access_token_sync(self) -> str:
        """Synchronous version of get_access_token"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.get_access_token())
            return result
        finally:
            loop.close()

    def create_payment_sync(self, payment_request: PaymentRequest) -> PaymentResult:
        """Synchronous version of create_payment"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.create_payment(payment_request))
            return result
        finally:
            loop.close()

    def execute_payment_sync(self, payment_id: str, payer_id: str) -> PaymentResult:
        """Synchronous version of execute_payment"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.execute_payment(payment_id, payer_id))
            return result
        finally:
            loop.close()

    def get_payment_details_sync(self, payment_id: str) -> dict:
        """Synchronous version of get_payment_details"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.get_payment_details(payment_id))
            return result
        finally:
            loop.close()

    def create_subscription_sync(self, plan_id: str, customer_email: str,
                                 return_url: str = "", cancel_url: str = "") -> dict:
        """Synchronous version of create_subscription"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                self.create_subscription(plan_id, customer_email, return_url, cancel_url)
            )
            return result
        finally:
            loop.close()


class SINCORPaymentProcessorSync(SINCORPaymentProcessor):
    """Synchronous wrapper for SINCOR payment processor"""

    def __init__(self):
        super().__init__()
        # Replace async PayPal with sync version
        self.paypal = PayPalIntegrationSync()

    def process_instant_bi_payment_sync(self, amount: float, client_email: str,
                                       urgency_level: str = "standard") -> PaymentResult:
        """Synchronous version of process_instant_bi_payment"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                self.process_instant_bi_payment(amount, client_email, urgency_level)
            )
            return result
        finally:
            loop.close()

    def process_agent_subscription_sync(self, monthly_amount: float, client_email: str,
                                       agent_type: str = "standard") -> dict:
        """Synchronous version of process_agent_subscription"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                self.process_agent_subscription(monthly_amount, client_email, agent_type)
            )
            return result
        finally:
            loop.close()

    def get_revenue_metrics_sync(self) -> dict:
        """Synchronous version of get_revenue_metrics"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.get_revenue_metrics())
            return result
        finally:
            loop.close()


# Test function
def test_sync_integration():
    """Test synchronous PayPal integration"""
    try:
        processor = SINCORPaymentProcessorSync()

        print("Testing PayPal connection (sync)...")
        token = processor.paypal.get_access_token_sync()
        print(f"✅ Token received: {token[:20]}...")

        print("\n✅ Synchronous PayPal integration working!")
        return True

    except Exception as e:
        print(f"❌ Sync integration test failed: {e}")
        return False


if __name__ == "__main__":
    test_sync_integration()
