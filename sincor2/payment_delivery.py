"""
Payment Delivery System - Handles post-purchase fulfillment
Verifies payments, creates accounts, sends credentials, activates agents
"""

import os
import json
import secrets
import hashlib
from datetime import datetime
from typing import Dict, Optional
import requests

class PaymentDeliverySystem:
    """Handles complete post-purchase delivery workflow"""

    def __init__(self):
        self.paypal_client_id = os.environ.get('PAYPAL_REST_API_ID')
        self.paypal_secret = os.environ.get('PAYPAL_REST_API_SECRET')
        self.sandbox_mode = os.environ.get('PAYPAL_SANDBOX', 'false').lower() == 'true'
        self.base_url = 'https://api-m.sandbox.paypal.com' if self.sandbox_mode else 'https://api-m.paypal.com'
        self.customers_db = 'data/customers.json'
        self.payments_log = 'logs/payments.log'

    def get_access_token(self) -> Optional[str]:
        """Get PayPal OAuth access token"""
        try:
            auth = (self.paypal_client_id, self.paypal_secret)
            response = requests.post(
                f'{self.base_url}/v1/oauth2/token',
                auth=auth,
                data={'grant_type': 'client_credentials'},
                timeout=10
            )
            if response.status_code == 200:
                return response.json().get('access_token')
        except Exception as e:
            print(f"Error getting PayPal token: {e}")
        return None

    def verify_payment(self, order_id: str) -> Optional[Dict]:
        """Verify payment completed successfully with PayPal"""
        token = self.get_access_token()
        if not token:
            return None

        try:
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }
            response = requests.get(
                f'{self.base_url}/v2/checkout/orders/{order_id}',
                headers=headers,
                timeout=10
            )

            if response.status_code == 200:
                order_data = response.json()
                if order_data.get('status') == 'COMPLETED':
                    return order_data
        except Exception as e:
            print(f"Error verifying payment: {e}")

        return None

    def extract_payment_info(self, order_data: Dict) -> Dict:
        """Extract key info from PayPal order data"""
        purchase_unit = order_data.get('purchase_units', [{}])[0]
        payer = order_data.get('payer', {})

        return {
            'order_id': order_data.get('id'),
            'amount': purchase_unit.get('amount', {}).get('value'),
            'currency': purchase_unit.get('amount', {}).get('currency_code', 'USD'),
            'description': purchase_unit.get('description', ''),
            'payer_email': payer.get('email_address', ''),
            'payer_name': f"{payer.get('name', {}).get('given_name', '')} {payer.get('name', {}).get('surname', '')}".strip(),
            'payer_id': payer.get('payer_id', ''),
            'timestamp': order_data.get('create_time'),
            'status': order_data.get('status')
        }

    def determine_plan(self, description: str, amount: str) -> str:
        """Determine which plan/product was purchased"""
        amount_float = float(amount)
        desc_lower = description.lower()

        # Monthly subscriptions
        if 'starter' in desc_lower or amount_float == 297.0:
            return 'starter'
        elif 'professional' in desc_lower or amount_float == 997.0:
            return 'professional'
        elif 'enterprise' in desc_lower or amount_float == 2997.0:
            return 'enterprise'

        # One-time services
        elif 'business intelligence' in desc_lower or amount_float == 97.0:
            return 'bi_report'
        elif 'competitive analysis' in desc_lower or amount_float == 147.0:
            return 'competitive_analysis'
        elif 'growth forecast' in desc_lower or amount_float == 247.0:
            return 'growth_forecast'

        return 'unknown'

    def generate_credentials(self, email: str) -> Dict:
        """Generate login credentials for new customer"""
        username = email.split('@')[0] + '_' + secrets.token_hex(4)
        password = secrets.token_urlsafe(16)

        return {
            'username': username,
            'password': password,
            'password_hash': hashlib.sha256(password.encode()).hexdigest()
        }

    def get_agent_count(self, plan: str) -> int:
        """Get number of agents for each plan"""
        agent_counts = {
            'starter': 10,
            'professional': 25,
            'enterprise': 42,
            'bi_report': 0,
            'competitive_analysis': 0,
            'growth_forecast': 0
        }
        return agent_counts.get(plan, 0)

    def save_customer(self, customer_data: Dict):
        """Save customer data to database"""
        try:
            # Load existing customers
            if os.path.exists(self.customers_db):
                with open(self.customers_db, 'r') as f:
                    customers = json.load(f)
            else:
                customers = []

            # Add new customer
            customers.append(customer_data)

            # Save back
            os.makedirs(os.path.dirname(self.customers_db), exist_ok=True)
            with open(self.customers_db, 'w') as f:
                json.dump(customers, f, indent=2)

            print(f"✅ Customer saved: {customer_data['email']}")
        except Exception as e:
            print(f"❌ Error saving customer: {e}")

    def log_payment(self, payment_info: Dict):
        """Log payment to payments log file"""
        try:
            os.makedirs(os.path.dirname(self.payments_log), exist_ok=True)
            with open(self.payments_log, 'a') as f:
                log_line = f"{datetime.now().isoformat()} | Order: {payment_info['order_id']} | Amount: ${payment_info['amount']} | Email: {payment_info['payer_email']} | Plan: {payment_info.get('plan', 'unknown')}\n"
                f.write(log_line)
            print(f"✅ Payment logged: {payment_info['order_id']}")
        except Exception as e:
            print(f"❌ Error logging payment: {e}")

    def send_welcome_email(self, customer_data: Dict) -> bool:
        """Send welcome email with credentials (placeholder - needs SMTP setup)"""
        # TODO: Implement actual email sending with SMTP
        print(f"""
        ===== WELCOME EMAIL (Would be sent to: {customer_data['email']}) =====
        Subject: Welcome to SINCOR - Your AI Agents Are Ready!

        Hi {customer_data['name']},

        Thank you for your purchase! Your SINCOR account is now active.

        LOGIN CREDENTIALS:
        Username: {customer_data['username']}
        Password: {customer_data['password']}
        Dashboard: https://web-production-92e2.up.railway.app/agent-steering

        YOUR PLAN: {customer_data['plan'].upper()}
        AI Agents Activated: {customer_data['agent_count']}

        NEXT STEPS:
        1. Login to your dashboard
        2. Review your AI agents
        3. Start automating your business

        Need help? Reply to this email or visit our support page.

        Welcome to the SINCOR family!
        - The SINCOR Team
        =====================================================================
        """)
        return True

    def process_delivery(self, order_id: str) -> Dict:
        """Main delivery workflow - handles complete fulfillment"""
        result = {
            'success': False,
            'message': '',
            'customer_data': None
        }

        print(f"\n🔄 Processing delivery for order: {order_id}")

        # Step 1: Verify payment with PayPal
        print("1️⃣ Verifying payment with PayPal...")
        order_data = self.verify_payment(order_id)
        if not order_data:
            result['message'] = 'Payment verification failed'
            print("❌ Payment verification failed")
            return result

        print("✅ Payment verified")

        # Step 2: Extract payment details
        print("2️⃣ Extracting payment info...")
        payment_info = self.extract_payment_info(order_data)
        plan = self.determine_plan(payment_info['description'], payment_info['amount'])
        payment_info['plan'] = plan
        print(f"✅ Plan identified: {plan}")

        # Step 3: Generate credentials
        print("3️⃣ Generating access credentials...")
        credentials = self.generate_credentials(payment_info['payer_email'])
        print(f"✅ Credentials generated for: {credentials['username']}")

        # Step 4: Create customer record
        print("4️⃣ Creating customer record...")
        customer_data = {
            'email': payment_info['payer_email'],
            'name': payment_info['payer_name'],
            'username': credentials['username'],
            'password_hash': credentials['password_hash'],
            'plan': plan,
            'agent_count': self.get_agent_count(plan),
            'order_id': payment_info['order_id'],
            'amount_paid': payment_info['amount'],
            'currency': payment_info['currency'],
            'payer_id': payment_info['payer_id'],
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }

        # Store plain password temporarily for email (don't save to DB)
        customer_data['password'] = credentials['password']

        # Step 5: Save to database
        print("5️⃣ Saving customer to database...")
        self.save_customer({k: v for k, v in customer_data.items() if k != 'password'})

        # Step 6: Log payment
        print("6️⃣ Logging payment...")
        self.log_payment(payment_info)

        # Step 7: Send welcome email
        print("7️⃣ Sending welcome email...")
        email_sent = self.send_welcome_email(customer_data)

        # Step 8: Activate agents (placeholder)
        print("8️⃣ Activating AI agents...")
        # TODO: Actually activate agents in the system
        print(f"✅ {customer_data['agent_count']} agents activated (placeholder)")

        result['success'] = True
        result['message'] = 'Delivery completed successfully'
        result['customer_data'] = customer_data

        print(f"\n✅ DELIVERY COMPLETE for {customer_data['email']}\n")

        return result


# Create singleton instance
delivery_system = PaymentDeliverySystem()
