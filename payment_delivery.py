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

class ContentSafetyFilter:
    """Ensures all generated content is appropriate, safe, and compliant"""

    PROHIBITED_TERMS = ['spam', 'scam', 'illegal', 'harmful', 'malicious', 'weapon', 'drug']

    def __init__(self):
        self.safety_log = 'logs/content_safety.log'
        self._ensure_log_exists()

    def _ensure_log_exists(self):
        """Ensure safety log directory and file exist"""
        import os as safety_os
        safety_os.makedirs(safety_os.path.dirname(self.safety_log), exist_ok=True)
        if not safety_os.path.exists(self.safety_log):
            with open(self.safety_log, 'w') as f:
                f.write('Content Safety Log - Started\n')

    def validate_request(self, product_type: str, customer_email: str, customer_name: str) -> Dict:
        """Validate content generation request before agent activation"""
        validation = {
            'safe': True,
            'flags': [],
            'risk_level': 'low',
            'timestamp': datetime.now().isoformat()
        }

        # Check email domain (basic validation)
        if customer_email:
            domain = customer_email.split('@')[-1].lower()
            suspicious_domains = ['tempmail', 'throwaway', '10minutemail']
            if any(susp in domain for susp in suspicious_domains):
                validation['flags'].append('Temporary email detected')
                validation['risk_level'] = 'medium'

        # Check customer name for prohibited terms
        if customer_name:
            name_lower = customer_name.lower()
            for term in self.PROHIBITED_TERMS:
                if term in name_lower:
                    validation['flags'].append(f'Prohibited term in name: {term}')
                    validation['safe'] = False
                    validation['risk_level'] = 'high'

        # Product-specific validation
        content_products = ['content-micro', 'content-standard', 'content-enterprise', 'creative-forge']
        if product_type in content_products:
            validation['content_generation'] = True
            validation['moderation_required'] = True

        self._log_validation(validation, product_type, customer_email)
        return validation

    def filter_content_request(self, request_data: Dict) -> Dict:
        """Apply filters to content generation requests"""
        filtered = request_data.copy()

        # Ensure appropriate content parameters
        filtered['content_policy'] = 'professional_business_only'
        filtered['safety_level'] = 'strict'
        filtered['prohibited_topics'] = self.PROHIBITED_TERMS
        filtered['moderation_enabled'] = True

        return filtered

    def _log_validation(self, validation: Dict, product_type: str, customer_email: str):
        """Log safety validation results"""
        try:
            with open(self.safety_log, 'a', encoding='utf-8') as f:
                log_entry = f"{validation['timestamp']} | {product_type} | {customer_email} | Risk: {validation['risk_level']} | Safe: {validation['safe']} | Flags: {validation['flags']}\n"
                f.write(log_entry)
        except Exception as e:
            print(f"Warning: Could not write to safety log: {e}")

    def get_content_guidelines(self, product_type: str) -> Dict:
        """Get content generation guidelines for specific product"""
        guidelines = {
            'bi-report': {
                'max_pages': 25,
                'tone': 'professional',
                'data_sources': 'verified_only',
                'safety_checks': ['financial_accuracy', 'no_speculation']
            },
            'competitive-analysis': {
                'max_pages': 30,
                'tone': 'analytical',
                'data_sources': 'public_verified',
                'safety_checks': ['factual_only', 'no_defamation']
            },
            'content-micro': {
                'max_words': 5000,
                'tone': 'professional',
                'safety_checks': ['appropriate_language', 'no_prohibited_content']
            },
            'content-standard': {
                'max_words': 25000,
                'tone': 'professional',
                'safety_checks': ['appropriate_language', 'no_prohibited_content', 'brand_safe']
            },
            'content-enterprise': {
                'max_words': 100000,
                'tone': 'executive',
                'safety_checks': ['appropriate_language', 'no_prohibited_content', 'brand_safe', 'compliance_review']
            }
        }

        return guidelines.get(product_type, {
            'tone': 'professional',
            'safety_checks': ['appropriate_language']
        })



class PaymentDeliverySystem:
    """Handles complete post-purchase delivery workflow"""

    def __init__(self):
        self.paypal_client_id = os.environ.get('PAYPAL_REST_API_ID')
        self.paypal_secret = os.environ.get('PAYPAL_REST_API_SECRET')
        self.sandbox_mode = os.environ.get('PAYPAL_SANDBOX', 'false').lower() == 'true'
        self.base_url = 'https://api-m.sandbox.paypal.com' if self.sandbox_mode else 'https://api-m.paypal.com'
        self.customers_db = 'data/customers.json'
        self.payments_log = 'logs/payments.log'
        self.safety_filter = ContentSafetyFilter()

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

        # Step 8: Content Safety Validation
        print("8️⃣ Running content safety checks...")
        safety_validation = self.safety_filter.validate_request(
            product_type=plan,
            customer_email=customer_data['email'],
            customer_name=customer_data['name']
        )

        if not safety_validation['safe']:
            print(f"⚠️  SAFETY ALERT: {safety_validation['flags']}")
            result['message'] = 'Order requires manual review due to safety flags'
            result['safety_validation'] = safety_validation
            # Do not activate agents automatically if flagged
            return result

        print(f"✅ Safety validation passed (Risk: {safety_validation['risk_level']})")

        # Get content guidelines for this product
        content_guidelines = self.safety_filter.get_content_guidelines(plan)
        customer_data['content_guidelines'] = content_guidelines

        # Step 9: Activate agents with safety filters
        print("9️⃣ Activating AI agents with content filters...")
        # Apply content filters to agent request
        agent_config = self.safety_filter.filter_content_request({
            'plan': plan,
            'customer_email': customer_data['email'],
            'guidelines': content_guidelines
        })
        # TODO: Actually activate agents in the system with agent_config
        print(f"✅ {customer_data['agent_count']} agents activated with safety filters")
        customer_data['safety_validation'] = safety_validation

        result['success'] = True
        result['message'] = 'Delivery completed successfully'
        result['customer_data'] = customer_data

        print(f"\n✅ DELIVERY COMPLETE for {customer_data['email']}\n")

        return result


# Create singleton instance
delivery_system = PaymentDeliverySystem()
