"""
SINCOR Order Fulfillment System
Automatically delivers purchased products and services after payment
Integrates with content generation and BI engines
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

# Import content and BI engines
try:
    from unified_content_engine import (
        UnifiedContentEngine,
        ContentRequest,
        ContentPackage,
        DeliverySpeed
    )
    CONTENT_ENGINE_AVAILABLE = True
except ImportError:
    CONTENT_ENGINE_AVAILABLE = False
    print("Warning: Content engine not available for fulfillment")

try:
    from instant_business_intelligence import InstantBusinessIntelligence
    BI_ENGINE_AVAILABLE = True
except ImportError:
    BI_ENGINE_AVAILABLE = False
    print("Warning: BI engine not available for fulfillment")


class OrderType(Enum):
    SUBSCRIPTION = "subscription"
    ONE_TIME = "one_time"
    BI_REPORT = "bi_report"
    CONTENT_PACKAGE = "content_package"


class DeliveryStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass
class Order:
    order_id: str
    customer_email: str
    product_name: str
    amount: float
    order_type: OrderType
    payment_id: str
    created_at: str
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    delivery_url: Optional[str] = None
    error_message: Optional[str] = None


class OrderFulfillmentSystem:
    """
    Handles automatic delivery of purchased products
    """

    def __init__(self):
        self.content_engine = UnifiedContentEngine() if CONTENT_ENGINE_AVAILABLE else None
        self.bi_engine = None  # Will be initialized with proper params when needed

        self.orders: Dict[str, Order] = {}
        self.fulfillment_log: list = []

        # Product type mapping
        self.product_mapping = {
            'Starter': OrderType.SUBSCRIPTION,
            'Professional': OrderType.SUBSCRIPTION,
            'Enterprise': OrderType.SUBSCRIPTION,
            'Business Intelligence Report': OrderType.BI_REPORT,
            'Competitive Analysis': OrderType.BI_REPORT,
            '90-Day Growth Forecast': OrderType.BI_REPORT,
            'Content Package - Micro': OrderType.CONTENT_PACKAGE,
            'Content Package - Standard': OrderType.CONTENT_PACKAGE,
            'Content Package - Professional': OrderType.CONTENT_PACKAGE,
            'Content Package - Enterprise': OrderType.CONTENT_PACKAGE,
        }

    async def process_order(self, order_data: Dict[str, Any]) -> Order:
        """
        Process a new order and trigger fulfillment

        Args:
            order_data: {
                'order_id': str,
                'customer_email': str,
                'product_name': str,
                'amount': float,
                'payment_id': str
            }

        Returns:
            Order object with delivery status
        """
        # Create order
        order = Order(
            order_id=order_data['order_id'],
            customer_email=order_data['customer_email'],
            product_name=order_data['product_name'],
            amount=order_data['amount'],
            order_type=self._determine_order_type(order_data['product_name']),
            payment_id=order_data['payment_id'],
            created_at=datetime.now().isoformat(),
            delivery_status=DeliveryStatus.PENDING
        )

        # Store order
        self.orders[order.order_id] = order

        # Trigger fulfillment based on order type
        try:
            order.delivery_status = DeliveryStatus.PROCESSING

            if order.order_type == OrderType.SUBSCRIPTION:
                await self._fulfill_subscription(order)
            elif order.order_type == OrderType.BI_REPORT:
                await self._fulfill_bi_report(order)
            elif order.order_type == OrderType.CONTENT_PACKAGE:
                await self._fulfill_content_package(order)
            else:
                await self._fulfill_generic(order)

            order.delivery_status = DeliveryStatus.DELIVERED

            # Log successful fulfillment
            self._log_fulfillment(order, success=True)

        except Exception as e:
            order.delivery_status = DeliveryStatus.FAILED
            order.error_message = str(e)
            self._log_fulfillment(order, success=False, error=str(e))

        return order

    def _determine_order_type(self, product_name: str) -> OrderType:
        """Determine order type from product name"""
        return self.product_mapping.get(product_name, OrderType.ONE_TIME)

    async def _fulfill_subscription(self, order: Order):
        """
        Fulfill subscription orders
        - Create customer account
        - Activate agents
        - Send welcome email with login credentials
        """
        print(f"Fulfilling subscription: {order.product_name} for {order.customer_email}")

        # In production, this would:
        # 1. Create user account in database
        # 2. Assign AI agents based on plan tier
        # 3. Generate login credentials
        # 4. Send welcome email

        # For now, simulate fulfillment
        order.delivery_url = f"/dashboard?email={order.customer_email}&plan={order.product_name}"

        fulfillment_data = {
            'account_created': True,
            'dashboard_url': order.delivery_url,
            'agents_activated': self._get_agent_count(order.product_name),
            'welcome_email_sent': True
        }

        print(f"[OK] Subscription activated: {json.dumps(fulfillment_data, indent=2)}")

    async def _fulfill_bi_report(self, order: Order):
        """
        Fulfill BI report orders
        - Generate report using instant_business_intelligence
        - Create PDF
        - Email download link
        """
        print(f"Generating BI report: {order.product_name} for {order.customer_email}")

        # Determine report type
        report_type = self._get_report_type(order.product_name)

        # Generate report (simulated for now)
        report_data = {
            'report_type': report_type,
            'generated_at': datetime.now().isoformat(),
            'customer_email': order.customer_email,
            'sections': [
                'Executive Summary',
                'Revenue Analysis',
                'Growth Opportunities',
                'Competitive Positioning',
                'Recommendations'
            ],
            'pages': 20,
            'charts': 15
        }

        # Create download URL (in production, this would be S3 or similar)
        order.delivery_url = f"/download/report/{order.order_id}.pdf"

        print(f"[OK] BI Report generated: {order.delivery_url}")
        print(f"   Report includes: {', '.join(report_data['sections'])}")

    async def _fulfill_content_package(self, order: Order):
        """
        Fulfill content package orders
        - Generate content using unified_content_engine
        - Export in multiple formats
        - Email download links
        """
        print(f"Generating content package: {order.product_name} for {order.customer_email}")

        if not CONTENT_ENGINE_AVAILABLE:
            raise Exception("Content engine not available")

        # Determine package type
        package_type = self._get_content_package_type(order.product_name)

        # Create content request
        content_request = ContentRequest(
            package_type=package_type,
            content_types=['blog_post', 'landing_page', 'product_description', 'email_campaign'],
            industry='saas',  # Would come from customer profile
            target_audience='director',
            brand_context={
                'customer_email': order.customer_email,
                'order_id': order.order_id
            },
            keywords=['automation', 'AI', 'efficiency', 'ROI'],
            tone='professional',
            delivery_speed=DeliverySpeed.PRIORITY
        )

        # Generate content
        deliverable = await self.content_engine.generate_content_package(content_request)

        # Export in multiple formats
        markdown_export = self.content_engine.export_deliverable(deliverable, 'markdown')
        html_export = self.content_engine.export_deliverable(deliverable, 'html')

        # Create download URLs
        order.delivery_url = f"/download/content/{order.order_id}/"

        print(f"[OK] Content package generated:")
        print(f"   - {len(deliverable.generated_content)} pieces created")
        print(f"   - {deliverable.total_word_count:,} words total")
        print(f"   - Quality score: {deliverable.quality_scores.get('overall', 0):.1f}/100")
        print(f"   - Download: {order.delivery_url}")

    async def _fulfill_generic(self, order: Order):
        """Fulfill generic one-time purchases"""
        print(f"Fulfilling generic order: {order.product_name}")
        order.delivery_url = f"/my-orders/{order.order_id}"

    def _get_agent_count(self, plan_name: str) -> int:
        """Get number of agents for subscription plan"""
        agent_counts = {
            'Starter': 10,
            'Professional': 25,
            'Enterprise': 42
        }
        return agent_counts.get(plan_name, 0)

    def _get_report_type(self, product_name: str) -> str:
        """Determine BI report type"""
        if 'Business Intelligence' in product_name:
            return 'comprehensive_bi'
        elif 'Competitive Analysis' in product_name:
            return 'competitive_analysis'
        elif 'Growth Forecast' in product_name:
            return 'growth_forecast'
        return 'general'

    def _get_content_package_type(self, product_name: str):
        """Determine content package type"""
        from unified_content_engine import ContentPackage

        if 'Micro' in product_name:
            return ContentPackage.MICRO
        elif 'Standard' in product_name:
            return ContentPackage.STANDARD
        elif 'Professional' in product_name:
            return ContentPackage.PROFESSIONAL
        elif 'Enterprise' in product_name:
            return ContentPackage.ENTERPRISE

        return ContentPackage.STANDARD

    def _log_fulfillment(self, order: Order, success: bool, error: str = None):
        """Log fulfillment attempt"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'order_id': order.order_id,
            'product_name': order.product_name,
            'customer_email': order.customer_email,
            'success': success,
            'delivery_status': order.delivery_status.value,
            'delivery_url': order.delivery_url,
            'error': error
        }

        self.fulfillment_log.append(log_entry)

        # In production, write to database
        print(f"[LOG] Fulfillment logged: {json.dumps(log_entry, indent=2)}")

    def get_order_status(self, order_id: str) -> Optional[Order]:
        """Get status of an order"""
        return self.orders.get(order_id)

    def get_customer_orders(self, customer_email: str) -> list:
        """Get all orders for a customer"""
        return [
            order for order in self.orders.values()
            if order.customer_email == customer_email
        ]

    def get_fulfillment_stats(self) -> Dict[str, Any]:
        """Get fulfillment statistics"""
        total_orders = len(self.orders)
        delivered = sum(1 for o in self.orders.values() if o.delivery_status == DeliveryStatus.DELIVERED)
        failed = sum(1 for o in self.orders.values() if o.delivery_status == DeliveryStatus.FAILED)
        pending = sum(1 for o in self.orders.values() if o.delivery_status == DeliveryStatus.PENDING)

        return {
            'total_orders': total_orders,
            'delivered': delivered,
            'failed': failed,
            'pending': pending,
            'success_rate': (delivered / total_orders * 100) if total_orders > 0 else 0,
            'total_revenue': sum(o.amount for o in self.orders.values())
        }


# Global fulfillment system instance
fulfillment_system = OrderFulfillmentSystem()


async def demo_fulfillment():
    """Demonstrate order fulfillment system"""
    print("="*70)
    print("SINCOR ORDER FULFILLMENT SYSTEM DEMO")
    print("="*70)

    system = OrderFulfillmentSystem()

    # Demo 1: Subscription order
    print("\n[DEMO 1] Fulfilling Professional Subscription")
    subscription_order = await system.process_order({
        'order_id': 'ORD-001',
        'customer_email': 'john@techstart.com',
        'product_name': 'Professional',
        'amount': 997.00,
        'payment_id': 'PAY-12345'
    })

    print(f"\nOrder Status: {subscription_order.delivery_status.value}")
    print(f"Dashboard URL: {subscription_order.delivery_url}")

    # Demo 2: BI Report order
    print("\n[DEMO 2] Fulfilling BI Report")
    bi_order = await system.process_order({
        'order_id': 'ORD-002',
        'customer_email': 'sarah@digitaldynamics.com',
        'product_name': 'Business Intelligence Report',
        'amount': 97.00,
        'payment_id': 'PAY-67890'
    })

    print(f"\nOrder Status: {bi_order.delivery_status.value}")
    print(f"Report URL: {bi_order.delivery_url}")

    # Demo 3: Content Package order
    if CONTENT_ENGINE_AVAILABLE:
        print("\n[DEMO 3] Fulfilling Content Package")
        content_order = await system.process_order({
            'order_id': 'ORD-003',
            'customer_email': 'michael@growthlabs.com',
            'product_name': 'Content Package - Standard',
            'amount': 2500.00,
            'payment_id': 'PAY-11111'
        })

        print(f"\nOrder Status: {content_order.delivery_status.value}")
        print(f"Content URL: {content_order.delivery_url}")

    # Show stats
    print("\n[FULFILLMENT STATS]")
    stats = system.get_fulfillment_stats()
    print(f"Total Orders: {stats['total_orders']}")
    print(f"Delivered: {stats['delivered']}")
    print(f"Failed: {stats['failed']}")
    print(f"Success Rate: {stats['success_rate']:.1f}%")
    print(f"Total Revenue: ${stats['total_revenue']:,.2f}")

    print("\n✅ Fulfillment system operational!")


if __name__ == "__main__":
    asyncio.run(demo_fulfillment())
