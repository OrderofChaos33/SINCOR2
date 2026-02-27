"""
SINCOR Checkout & Payment Processing
File: src/sincor2/checkout_engine.py
"""

import stripe
import json
import uuid
from datetime import datetime
from pathlib import Path
import sqlite3

# Initialize Stripe
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

class CheckoutEngine:
    def __init__(self, db_path='data/sincor.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_database()

    def init_database(self):
        """Create orders and invoices tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Orders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                customer_email TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                company_name TEXT NOT NULL,

                -- Service details
                service_type TEXT NOT NULL,  -- intelligence, predictive, agents, automation, market
                service_name TEXT NOT NULL,
                complexity_tier TEXT NOT NULL,  -- Simple, Standard, Complex, Enterprise
                delivery_speed TEXT NOT NULL,  -- expedited, standard, extended
                quality_tier TEXT NOT NULL,  -- standard, premium, enterprise

                -- Pricing
                base_price INTEGER NOT NULL,  -- cents
                final_price INTEGER NOT NULL,  -- cents (after multipliers)
                currency TEXT DEFAULT 'USD',

                -- Payment
                stripe_payment_id TEXT,
                payment_status TEXT DEFAULT 'pending',  -- pending, paid, failed, refunded
                paid_at TIMESTAMP,

                -- Service tracking
                project_status TEXT DEFAULT 'not_started',  -- not_started, active, completed, archived
                assigned_agents TEXT,  -- JSON array of agent IDs
                kickoff_date DATE,
                estimated_completion DATE,

                -- Metadata
                billing_address TEXT,  -- JSON
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Invoices table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES orders(id),
                invoice_number TEXT UNIQUE NOT NULL,
                amount INTEGER NOT NULL,  -- cents
                issued_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                due_date TIMESTAMP,
                paid_date TIMESTAMP,
                status TEXT DEFAULT 'unpaid',  -- unpaid, paid, overdue
                pdf_url TEXT
            )
        ''')

        # Projects table (service delivery tracking)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES orders(id),
                project_name TEXT NOT NULL,

                -- Timeline
                start_date DATE,
                end_date DATE,
                phase INT DEFAULT 0,  -- 0-5 for 5 phases

                -- Status
                status TEXT DEFAULT 'planning',  -- planning, phase1, phase2, phase3, phase4, phase5, completed
                completion_percent INT DEFAULT 0,

                -- Quality
                quality_score INT,
                deliverables TEXT,  -- JSON array

                -- Notes
                internal_notes TEXT,
                customer_notes TEXT,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()

    def process_payment(self, payment_method_id, amount_cents, order_data, billing_data):
        """Process Stripe payment and create order"""

        try:
            # Create Stripe charge
            charge = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency='usd',
                payment_method=payment_method_id,
                confirm=True,
                return_url='https://app.getsincor.com/checkout/success'
            )

            if charge.status != 'succeeded':
                return {
                    'success': False,
                    'error': f'Payment failed: {charge.status}'
                }

            # Payment successful - create order
            order_id = str(uuid.uuid4())

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO orders (
                    id, customer_email, customer_name, company_name,
                    service_type, service_name, complexity_tier, delivery_speed, quality_tier,
                    base_price, final_price, stripe_payment_id, payment_status, paid_at,
                    billing_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                order_id,
                billing_data['email'],
                f"{billing_data['firstName']} {billing_data['lastName']}",
                billing_data['company'],
                order_data['service'],
                order_data['serviceName'],
                order_data['tier'],
                order_data['speed'],
                order_data['quality'],
                0,  # base_price (would calculate from order history)
                int(order_data['price']),
                charge.id,
                'paid',
                datetime.utcnow(),
                json.dumps(billing_data)
            ))

            conn.commit()

            # Create invoice
            invoice_number = f"INV-{order_id[:8].upper()}-{datetime.now().strftime('%Y%m%d')}"
            invoice_id = str(uuid.uuid4())

            cursor.execute('''
                INSERT INTO invoices (id, order_id, invoice_number, amount, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (invoice_id, order_id, invoice_number, int(order_data['price']), 'paid'))

            conn.commit()

            # Create project
            project_id = str(uuid.uuid4())
            cursor.execute('''
                INSERT INTO projects (id, order_id, project_name, status)
                VALUES (?, ?, ?, ?)
            ''', (
                project_id,
                order_id,
                f"{order_data['serviceName']} - {billing_data['company']}",
                'planning'
            ))

            conn.commit()
            conn.close()

            # Send confirmation email (async)
            self.send_confirmation_email(order_id, billing_data, order_data)

            # Assign agents (async)
            self.assign_agents_to_project(project_id, order_data)

            return {
                'success': True,
                'orderId': order_id,
                'invoiceNumber': invoice_number,
                'projectId': project_id
            }

        except stripe.error.CardError as e:
            return {
                'success': False,
                'error': f'Card declined: {e.user_message}'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def send_confirmation_email(self, order_id, billing_data, order_data):
        """Send order confirmation email to customer"""

        email_body = f"""
Hello {billing_data['firstName']},

Your order has been confirmed! 🎉

Order Details:
- Service: {order_data['serviceName']}
- Complexity: {order_data['tier']}
- Investment: ${int(order_data['price']):,}
- Order ID: {order_id}

What happens next:
1. Our team will review your requirements within 24 hours
2. You'll receive a kickoff meeting invitation
3. We'll begin your project immediately

Dashboard: https://app.getsincor.com/projects/{order_id}

Questions? Email support@getsincor.com

Thank you for choosing SINCOR!
        """

        # Send via Mailgun/SendGrid (placeholder)
        print(f"[EMAIL] Confirmation sent to {billing_data['email']}")

    def assign_agents_to_project(self, project_id, order_data):
        """Automatically assign AI agents based on service type"""

        agent_mapping = {
            'intelligence': ['Scout', 'Synthesizer', 'Auditor'],
            'predictive': ['Synthesizer', 'Builder', 'Auditor', 'Optimizer'],
            'agents': ['Scout', 'Synthesizer', 'Builder', 'Builder', 'Auditor'],
            'automation': ['Builder', 'Builder', 'Auditor', 'Caretaker'],
            'market': ['Scout', 'Synthesizer', 'Auditor', 'Director']
        }

        agents = agent_mapping.get(order_data['service'], [])
        print(f"[AGENTS] Assigning {agents} to project {project_id}")

        # Update project with assigned agents
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE projects SET assigned_agents = ? WHERE id = ?
        ''', (json.dumps(agents), project_id))
        conn.commit()
        conn.close()

    def get_order(self, order_id):
        """Retrieve order details"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        result = cursor.fetchone()
        conn.close()
        return result

    def list_orders(self, status='paid', limit=50):
        """List all orders with given payment status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM orders WHERE payment_status = ? ORDER BY created_at DESC LIMIT ?',
            (status, limit)
        )
        results = cursor.fetchall()
        conn.close()
        return results

    def get_revenue_summary(self):
        """Get revenue metrics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Total revenue
        cursor.execute('SELECT SUM(final_price) FROM orders WHERE payment_status = ?', ('paid',))
        total_revenue = cursor.fetchone()[0] or 0

        # Orders count
        cursor.execute('SELECT COUNT(*) FROM orders WHERE payment_status = ?', ('paid',))
        order_count = cursor.fetchone()[0]

        # Average order value
        avg_order = total_revenue / order_count if order_count > 0 else 0

        conn.close()

        return {
            'total_revenue': total_revenue / 100,  # Convert cents to dollars
            'order_count': order_count,
            'average_order_value': avg_order / 100,
            'monthly_rate': (total_revenue / 100) * 12 if order_count > 0 else 0
        }
