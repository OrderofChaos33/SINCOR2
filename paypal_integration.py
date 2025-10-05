"""
SINCOR PayPal Payment Integration
Connects monetization engine to your Railway PayPal configuration
Uses PAYPAL_REST_API_ID and PAYPAL_REST_API_SECRET environment variables
"""

import os
import json
import requests
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class PaymentStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

@dataclass
class PaymentRequest:
    amount: float
    currency: str = "USD"
    description: str = ""
    customer_email: str = ""
    order_id: str = ""
    return_url: str = ""
    cancel_url: str = ""

@dataclass
class PaymentResult:
    success: bool
    payment_id: str
    status: PaymentStatus
    amount: float
    transaction_fee: float
    net_amount: float
    approval_url: Optional[str] = None
    error_message: Optional[str] = None

class PayPalIntegration:
    def __init__(self):
        # Use your Railway environment variable names
        self.client_id = os.getenv('PAYPAL_REST_API_ID')
        self.client_secret = os.getenv('PAYPAL_REST_API_SECRET')
        
        # PayPal API configuration
        self.sandbox_mode = os.getenv('PAYPAL_SANDBOX', 'true').lower() == 'true'
        
        if self.sandbox_mode:
            self.base_url = "https://api.sandbox.paypal.com"
        else:
            self.base_url = "https://api.paypal.com"
        
        self.access_token = None
        self.token_expires_at = None
        
        # Validate configuration
        if not self.client_id or not self.client_secret:
            raise ValueError("PayPal credentials not found. Ensure PAYPAL_REST_API_ID and PAYPAL_REST_API_SECRET are set in Railway")
    
    async def get_access_token(self) -> str:
        """Get or refresh PayPal access token"""
        
        # Check if current token is still valid
        if self.access_token and self.token_expires_at:
            if datetime.now() < self.token_expires_at - timedelta(minutes=5):  # 5 min buffer
                return self.access_token
        
        # Request new token
        url = f"{self.base_url}/v1/oauth2/token"
        
        headers = {
            'Accept': 'application/json',
            'Accept-Language': 'en_US',
        }
        
        data = 'grant_type=client_credentials'
        
        try:
            response = requests.post(
                url, 
                headers=headers, 
                data=data,
                auth=(self.client_id, self.client_secret),
                timeout=30
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 3600)  # Default 1 hour
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                return self.access_token
            else:
                raise Exception(f"PayPal token request failed: {response.status_code} - {response.text}")
                
        except Exception as e:
            raise Exception(f"Failed to get PayPal access token: {str(e)}")
    
    async def create_payment(self, payment_request: PaymentRequest) -> PaymentResult:
        """Create a PayPal payment"""
        
        try:
            access_token = await self.get_access_token()
            
            url = f"{self.base_url}/v1/payments/payment"
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }
            
            # Create payment data
            payment_data = {
                "intent": "sale",
                "payer": {
                    "payment_method": "paypal"
                },
                "transactions": [{
                    "amount": {
                        "total": f"{payment_request.amount:.2f}",
                        "currency": payment_request.currency
                    },
                    "description": payment_request.description,
                    "custom": payment_request.order_id,  # Store our order ID
                    "invoice_number": f"SINCOR-{payment_request.order_id}"
                }],
                "redirect_urls": {
                    "return_url": payment_request.return_url or "https://your-sincor-domain.railway.app/payment/success",
                    "cancel_url": payment_request.cancel_url or "https://your-sincor-domain.railway.app/payment/cancel"
                }
            }
            
            response = requests.post(url, headers=headers, json=payment_data, timeout=30)
            
            if response.status_code == 201:
                payment_response = response.json()
                payment_id = payment_response['id']
                
                # Extract approval URL
                approval_url = None
                for link in payment_response.get('links', []):
                    if link['rel'] == 'approval_url':
                        approval_url = link['href']
                        break
                
                return PaymentResult(
                    success=True,
                    payment_id=payment_id,
                    status=PaymentStatus.PENDING,
                    amount=payment_request.amount,
                    transaction_fee=0.0,  # Will be calculated after completion
                    net_amount=payment_request.amount,
                    approval_url=approval_url
                )
            else:
                error_msg = f"PayPal payment creation failed: {response.status_code} - {response.text}"
                return PaymentResult(
                    success=False,
                    payment_id="",
                    status=PaymentStatus.FAILED,
                    amount=payment_request.amount,
                    transaction_fee=0.0,
                    net_amount=0.0,
                    error_message=error_msg
                )
                
        except Exception as e:
            return PaymentResult(
                success=False,
                payment_id="",
                status=PaymentStatus.FAILED,
                amount=payment_request.amount,
                transaction_fee=0.0,
                net_amount=0.0,
                error_message=f"Payment creation error: {str(e)}"
            )
    
    async def execute_payment(self, payment_id: str, payer_id: str) -> PaymentResult:
        """Execute an approved PayPal payment"""
        
        try:
            access_token = await self.get_access_token()
            
            url = f"{self.base_url}/v1/payments/payment/{payment_id}/execute"
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }
            
            execute_data = {
                "payer_id": payer_id
            }
            
            response = requests.post(url, headers=headers, json=execute_data, timeout=30)
            
            if response.status_code == 200:
                execution_response = response.json()
                
                if execution_response['state'] == 'approved':
                    # Extract transaction details
                    transaction = execution_response['transactions'][0]
                    related_resources = transaction['related_resources'][0]
                    sale = related_resources['sale']
                    
                    amount = float(sale['amount']['total'])
                    
                    # Calculate PayPal fees (approximate)
                    transaction_fee = amount * 0.029 + 0.30  # PayPal standard rate
                    net_amount = amount - transaction_fee
                    
                    return PaymentResult(
                        success=True,
                        payment_id=payment_id,
                        status=PaymentStatus.COMPLETED,
                        amount=amount,
                        transaction_fee=transaction_fee,
                        net_amount=net_amount
                    )
                else:
                    return PaymentResult(
                        success=False,
                        payment_id=payment_id,
                        status=PaymentStatus.FAILED,
                        amount=0.0,
                        transaction_fee=0.0,
                        net_amount=0.0,
                        error_message="Payment not approved"
                    )
            else:
                error_msg = f"PayPal execution failed: {response.status_code} - {response.text}"
                return PaymentResult(
                    success=False,
                    payment_id=payment_id,
                    status=PaymentStatus.FAILED,
                    amount=0.0,
                    transaction_fee=0.0,
                    net_amount=0.0,
                    error_message=error_msg
                )
                
        except Exception as e:
            return PaymentResult(
                success=False,
                payment_id=payment_id,
                status=PaymentStatus.FAILED,
                amount=0.0,
                transaction_fee=0.0,
                net_amount=0.0,
                error_message=f"Payment execution error: {str(e)}"
            )
    
    async def get_payment_details(self, payment_id: str) -> Dict[str, Any]:
        """Get details of a PayPal payment"""
        
        try:
            access_token = await self.get_access_token()
            
            url = f"{self.base_url}/v1/payments/payment/{payment_id}"
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                raise Exception(f"Failed to get payment details: {response.status_code}")
                
        except Exception as e:
            raise Exception(f"Error getting payment details: {str(e)}")
    
    async def create_subscription(self, plan_id: str, customer_email: str, 
                                start_date: Optional[str] = None) -> Dict[str, Any]:
        """Create a PayPal subscription (for recurring revenue streams)"""
        
        try:
            access_token = await self.get_access_token()
            
            url = f"{self.base_url}/v1/billing/subscriptions"
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
                'PayPal-Request-Id': f"SINCOR-SUB-{int(datetime.now().timestamp())}"
            }
            
            subscription_data = {
                "plan_id": plan_id,
                "subscriber": {
                    "email_address": customer_email
                },
                "application_context": {
                    "brand_name": "SINCOR AI Systems",
                    "locale": "en-US",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "SUBSCRIBE_NOW",
                    "payment_method": {
                        "payer_selected": "PAYPAL",
                        "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED"
                    },
                    "return_url": "https://your-sincor-domain.railway.app/subscription/success",
                    "cancel_url": "https://your-sincor-domain.railway.app/subscription/cancel"
                }
            }
            
            if start_date:
                subscription_data["start_time"] = start_date
            
            response = requests.post(url, headers=headers, json=subscription_data, timeout=30)
            
            if response.status_code == 201:
                return response.json()
            else:
                raise Exception(f"Subscription creation failed: {response.status_code} - {response.text}")
                
        except Exception as e:
            raise Exception(f"Error creating subscription: {str(e)}")

class SINCORPaymentProcessor:
    """High-level payment processor for SINCOR monetization"""
    
    def __init__(self):
        self.paypal = PayPalIntegration()
        self.payment_history: List[Dict] = []
    
    async def process_instant_bi_payment(self, amount: float, client_email: str, 
                                       urgency_level: str = "standard") -> PaymentResult:
        """Process payment for instant BI services"""
        
        order_id = f"BI-{urgency_level.upper()}-{int(datetime.now().timestamp())}"
        
        payment_request = PaymentRequest(
            amount=amount,
            currency="USD",
            description=f"SINCOR Business Intelligence - {urgency_level.title()} Delivery",
            customer_email=client_email,
            order_id=order_id
        )
        
        result = await self.paypal.create_payment(payment_request)
        
        # Log payment attempt
        payment_log = {
            'timestamp': datetime.now().isoformat(),
            'service_type': 'instant_bi',
            'amount': amount,
            'urgency': urgency_level,
            'client_email': client_email,
            'payment_id': result.payment_id,
            'status': result.status.value
        }
        
        self.payment_history.append(payment_log)
        
        return result
    
    async def process_agent_subscription(self, monthly_amount: float, client_email: str,
                                       agent_type: str = "standard") -> Dict[str, Any]:
        """Process subscription for agent services (annual upfront payment)"""

        # Convert to annual upfront payment (12 months prepaid)
        annual_amount = monthly_amount * 12

        order_id = f"AGENT-{agent_type.upper()}-ANNUAL-{int(datetime.now().timestamp())}"

        payment_request = PaymentRequest(
            amount=annual_amount,
            currency="USD",
            description=f"SINCOR Agent Service - {agent_type.title()} (12 months prepaid)",
            customer_email=client_email,
            order_id=order_id
        )

        result = await self.paypal.create_payment(payment_request)

        # Log subscription as one-time annual payment
        subscription_log = {
            'timestamp': datetime.now().isoformat(),
            'service_type': 'agent_subscription_annual',
            'monthly_amount': monthly_amount,
            'annual_amount': annual_amount,
            'agent_type': agent_type,
            'client_email': client_email,
            'payment_id': result.payment_id,
            'status': result.status.value,
            'approval_url': result.approval_url
        }

        self.payment_history.append(subscription_log)
        
        return result
    
    async def get_revenue_metrics(self) -> Dict[str, Any]:
        """Get revenue metrics from payment history"""
        
        completed_payments = [p for p in self.payment_history if p.get('status') == 'completed']
        
        total_revenue = sum(p.get('amount', 0) for p in completed_payments)
        
        revenue_by_service = {}
        for payment in completed_payments:
            service = payment.get('service_type', 'unknown')
            revenue_by_service[service] = revenue_by_service.get(service, 0) + payment.get('amount', 0)
        
        return {
            'total_revenue': total_revenue,
            'completed_payments': len(completed_payments),
            'revenue_by_service': revenue_by_service,
            'average_transaction': total_revenue / len(completed_payments) if completed_payments else 0,
            'last_updated': datetime.now().isoformat()
        }

# Global payment processor instance
payment_processor = SINCORPaymentProcessor()

async def demo_paypal_integration():
    """Demonstrate PayPal integration"""
    
    try:
        processor = SINCORPaymentProcessor()
        
        # Test credentials
        print("Testing PayPal connection...")
        access_token = await processor.paypal.get_access_token()
        print("✅ PayPal connection successful!")
        
        # Demo payment creation
        print("\nCreating demo payment...")
        result = await processor.process_instant_bi_payment(
            amount=2500.00,
            client_email="demo@client.com",
            urgency_level="priority"
        )
        
        if result.success:
            print(f"✅ Payment created: {result.payment_id}")
            print(f"🔗 Approval URL: {result.approval_url}")
        else:
            print(f"❌ Payment failed: {result.error_message}")
        
        # Show revenue metrics
        metrics = await processor.get_revenue_metrics()
        print(f"\n📊 Revenue Metrics: {json.dumps(metrics, indent=2)}")
        
    except Exception as e:
        print(f"❌ PayPal integration test failed: {e}")
        print("Ensure PAYPAL_REST_API_ID and PAYPAL_REST_API_SECRET are set in Railway")

if __name__ == "__main__":
    asyncio.run(demo_paypal_integration())