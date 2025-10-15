"""
SINCOR Customer Database System
Manages customer accounts, agent provisioning, and revenue tracking
"""

import sqlite3
import hashlib
import secrets
import os
import json
from datetime import datetime, timedelta
from flask import request

class CustomerDatabase:
    def __init__(self, db_path="data/customers.db"):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Initialize customer database with all required tables"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            # Customers table - main customer account information
            conn.execute('''
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT,
                    plan_type TEXT NOT NULL,
                    plan_price REAL NOT NULL,
                    payment_id TEXT NOT NULL,
                    payment_status TEXT DEFAULT 'completed',
                    activation_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    subscription_status TEXT DEFAULT 'active',
                    next_billing_date DATETIME,
                    agent_count INTEGER DEFAULT 0,
                    total_tasks_completed INTEGER DEFAULT 0,
                    total_revenue_generated REAL DEFAULT 0.0,
                    customer_segment TEXT,
                    api_key TEXT UNIQUE,
                    dashboard_url TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_login DATETIME,
                    ip_address TEXT,
                    user_agent TEXT
                )
            ''')

            # Customer agents table - tracks which agents are provisioned for each customer
            conn.execute('''
                CREATE TABLE IF NOT EXISTS customer_agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    agent_archetype TEXT,
                    status TEXT DEFAULT 'provisioning',
                    provisioned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_active DATETIME,
                    tasks_completed INTEGER DEFAULT 0,
                    health_status TEXT DEFAULT 'healthy',
                    configuration JSON,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
                    UNIQUE(customer_id, agent_id)
                )
            ''')

            # Agent tasks table - tracks what agents are doing for customers
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    task_description TEXT,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 5,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME,
                    completed_at DATETIME,
                    result JSON,
                    error_message TEXT,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                )
            ''')

            # Revenue tracking table - integrates with monetization engine
            conn.execute('''
                CREATE TABLE IF NOT EXISTS revenue_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    revenue_stream TEXT NOT NULL,
                    payment_method TEXT,
                    transaction_id TEXT,
                    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    metadata JSON,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                )
            ''')

            # Customer analytics table - real-time metrics
            conn.execute('''
                CREATE TABLE IF NOT EXISTS customer_analytics (
                    customer_id TEXT PRIMARY KEY,
                    total_api_calls INTEGER DEFAULT 0,
                    total_agent_hours REAL DEFAULT 0.0,
                    total_tasks_created INTEGER DEFAULT 0,
                    total_tasks_completed INTEGER DEFAULT 0,
                    avg_task_completion_time REAL DEFAULT 0.0,
                    success_rate REAL DEFAULT 100.0,
                    last_activity DATETIME,
                    engagement_score INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                )
            ''')

            # Plan definitions table - static plan information
            conn.execute('''
                CREATE TABLE IF NOT EXISTS plan_definitions (
                    plan_type TEXT PRIMARY KEY,
                    plan_name TEXT NOT NULL,
                    price REAL NOT NULL,
                    agent_count INTEGER NOT NULL,
                    features JSON,
                    agent_types JSON
                )
            ''')

            # Initialize plan definitions
            plans = [
                {
                    'plan_type': 'starter',
                    'plan_name': 'Starter Plan',
                    'price': 297.0,
                    'agent_count': 10,
                    'features': json.dumps(['10 AI Agents', 'Basic Swarm Coordination', 'Email Support', 'Standard Dashboard']),
                    'agent_types': json.dumps(['scout', 'builder', 'synthesizer', 'caretaker'])
                },
                {
                    'plan_type': 'professional',
                    'plan_name': 'Professional Plan',
                    'price': 597.0,
                    'agent_count': 30,
                    'features': json.dumps(['30 AI Agents', 'Advanced Swarm Intelligence', 'Priority Support', 'Advanced Analytics', 'Custom Integrations']),
                    'agent_types': json.dumps(['scout', 'builder', 'synthesizer', 'negotiator', 'caretaker', 'auditor'])
                },
                {
                    'plan_type': 'enterprise',
                    'plan_name': 'Enterprise Plan',
                    'price': 1497.0,
                    'agent_count': 42,
                    'features': json.dumps(['All 42 AI Agents', 'Full Swarm Access', 'Dedicated Support', 'Real-time Intelligence', 'White-label Options', 'API Access', 'Custom Training']),
                    'agent_types': json.dumps(['scout', 'builder', 'synthesizer', 'negotiator', 'caretaker', 'auditor', 'director'])
                }
            ]

            for plan in plans:
                conn.execute('''
                    INSERT OR REPLACE INTO plan_definitions
                    (plan_type, plan_name, price, agent_count, features, agent_types)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (plan['plan_type'], plan['plan_name'], plan['price'],
                      plan['agent_count'], plan['features'], plan['agent_types']))

            conn.commit()

    def generate_customer_id(self):
        """Generate unique customer ID"""
        return f"CUST-{secrets.token_hex(8).upper()}"

    def generate_api_key(self):
        """Generate secure API key for customer"""
        return f"sk-sincor-{secrets.token_urlsafe(32)}"

    def create_customer(self, email, name, plan_type, payment_id, payment_amount, customer_segment='smb'):
        """Create new customer account after successful payment"""
        try:
            customer_id = self.generate_customer_id()
            api_key = self.generate_api_key()
            dashboard_url = f"/customer/dashboard?customer_id={customer_id}"

            # Get request metadata if available
            ip_address = request.remote_addr if request else 'unknown'
            user_agent = request.headers.get('User-Agent', 'unknown') if request else 'unknown'

            # Calculate next billing date (30 days from now)
            next_billing_date = (datetime.now() + timedelta(days=30)).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                # Check if email already exists
                existing = conn.execute(
                    'SELECT customer_id FROM customers WHERE email = ?',
                    (email.lower(),)
                ).fetchone()

                if existing:
                    return {
                        'success': False,
                        'error': 'Email already has an active account',
                        'existing_customer_id': existing[0]
                    }

                # Get plan details
                plan_details = conn.execute(
                    'SELECT agent_count FROM plan_definitions WHERE plan_type = ?',
                    (plan_type,)
                ).fetchone()

                if not plan_details:
                    return {'success': False, 'error': 'Invalid plan type'}

                agent_count = plan_details[0]

                # Insert customer record
                conn.execute('''
                    INSERT INTO customers (
                        customer_id, email, name, plan_type, plan_price, payment_id,
                        payment_status, next_billing_date, agent_count, customer_segment,
                        api_key, dashboard_url, ip_address, user_agent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    customer_id, email.lower(), name, plan_type, payment_amount,
                    payment_id, 'completed', next_billing_date, agent_count,
                    customer_segment, api_key, dashboard_url, ip_address, user_agent
                ))

                # Initialize customer analytics
                conn.execute('''
                    INSERT INTO customer_analytics (customer_id, last_activity)
                    VALUES (?, CURRENT_TIMESTAMP)
                ''', (customer_id,))

                # Record initial revenue
                conn.execute('''
                    INSERT INTO revenue_tracking (
                        customer_id, transaction_type, amount, revenue_stream,
                        payment_method, transaction_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                ''', (customer_id, 'subscription_purchase', payment_amount,
                      'AGENT_SERVICES', 'PayPal', payment_id))

                conn.commit()

                return {
                    'success': True,
                    'customer_id': customer_id,
                    'api_key': api_key,
                    'dashboard_url': dashboard_url,
                    'agent_count': agent_count,
                    'plan_type': plan_type
                }

        except Exception as e:
            return {'success': False, 'error': f'Customer creation failed: {str(e)}'}

    def link_agent_to_customer(self, customer_id, agent_id, agent_name, agent_type, agent_archetype, configuration=None):
        """Link a provisioned agent to a customer"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO customer_agents (
                        customer_id, agent_id, agent_name, agent_type,
                        agent_archetype, status, configuration
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (customer_id, agent_id, agent_name, agent_type,
                      agent_archetype, 'active', json.dumps(configuration or {})))

                # Update customer agent count
                conn.execute('''
                    UPDATE customers
                    SET agent_count = (
                        SELECT COUNT(*) FROM customer_agents WHERE customer_id = ?
                    )
                    WHERE customer_id = ?
                ''', (customer_id, customer_id))

                conn.commit()

                return {'success': True, 'agent_id': agent_id}

        except Exception as e:
            return {'success': False, 'error': f'Agent linking failed: {str(e)}'}

    def get_customer(self, customer_id):
        """Get customer information"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            customer = conn.execute(
                'SELECT * FROM customers WHERE customer_id = ?',
                (customer_id,)
            ).fetchone()

            if customer:
                return dict(customer)
            return None

    def get_customer_agents(self, customer_id):
        """Get all agents provisioned for a customer"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            agents = conn.execute(
                'SELECT * FROM customer_agents WHERE customer_id = ? ORDER BY provisioned_at DESC',
                (customer_id,)
            ).fetchall()

            return [dict(agent) for agent in agents]

    def create_task(self, customer_id, agent_id, task_type, task_description, priority=5):
        """Create a new task for an agent"""
        try:
            task_id = f"TASK-{secrets.token_hex(8).upper()}"

            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO agent_tasks (
                        task_id, customer_id, agent_id, task_type,
                        task_description, priority, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (task_id, customer_id, agent_id, task_type,
                      task_description, priority, 'pending'))

                # Update analytics
                conn.execute('''
                    UPDATE customer_analytics
                    SET total_tasks_created = total_tasks_created + 1,
                        last_activity = CURRENT_TIMESTAMP
                    WHERE customer_id = ?
                ''', (customer_id,))

                conn.commit()

                return {'success': True, 'task_id': task_id}

        except Exception as e:
            return {'success': False, 'error': f'Task creation failed: {str(e)}'}

    def update_task_status(self, task_id, status, result=None, error_message=None):
        """Update task status and record completion"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                if status == 'running':
                    conn.execute('''
                        UPDATE agent_tasks
                        SET status = ?, started_at = CURRENT_TIMESTAMP
                        WHERE task_id = ?
                    ''', (status, task_id))

                elif status == 'completed':
                    conn.execute('''
                        UPDATE agent_tasks
                        SET status = ?, completed_at = CURRENT_TIMESTAMP, result = ?
                        WHERE task_id = ?
                    ''', (status, json.dumps(result or {}), task_id))

                    # Update customer analytics
                    task_info = conn.execute(
                        'SELECT customer_id, agent_id FROM agent_tasks WHERE task_id = ?',
                        (task_id,)
                    ).fetchone()

                    if task_info:
                        customer_id, agent_id = task_info

                        conn.execute('''
                            UPDATE customer_analytics
                            SET total_tasks_completed = total_tasks_completed + 1
                            WHERE customer_id = ?
                        ''', (customer_id,))

                        conn.execute('''
                            UPDATE customer_agents
                            SET tasks_completed = tasks_completed + 1,
                                last_active = CURRENT_TIMESTAMP
                            WHERE customer_id = ? AND agent_id = ?
                        ''', (customer_id, agent_id))

                        conn.execute('''
                            UPDATE customers
                            SET total_tasks_completed = total_tasks_completed + 1
                            WHERE customer_id = ?
                        ''', (customer_id,))

                elif status == 'failed':
                    conn.execute('''
                        UPDATE agent_tasks
                        SET status = ?, completed_at = CURRENT_TIMESTAMP, error_message = ?
                        WHERE task_id = ?
                    ''', (status, error_message, task_id))

                conn.commit()
                return {'success': True}

        except Exception as e:
            return {'success': False, 'error': f'Task update failed: {str(e)}'}

    def record_revenue(self, customer_id, transaction_type, amount, revenue_stream, payment_method='PayPal', transaction_id=None, metadata=None):
        """Record revenue transaction"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO revenue_tracking (
                        customer_id, transaction_type, amount, revenue_stream,
                        payment_method, transaction_id, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (customer_id, transaction_type, amount, revenue_stream,
                      payment_method, transaction_id, json.dumps(metadata or {})))

                # Update customer total revenue
                conn.execute('''
                    UPDATE customers
                    SET total_revenue_generated = total_revenue_generated + ?
                    WHERE customer_id = ?
                ''', (amount, customer_id))

                conn.commit()
                return {'success': True}

        except Exception as e:
            return {'success': False, 'error': f'Revenue recording failed: {str(e)}'}

    def get_customer_analytics(self, customer_id):
        """Get comprehensive customer analytics"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get customer info
            customer = conn.execute(
                'SELECT * FROM customers WHERE customer_id = ?',
                (customer_id,)
            ).fetchone()

            # Get analytics
            analytics = conn.execute(
                'SELECT * FROM customer_analytics WHERE customer_id = ?',
                (customer_id,)
            ).fetchone()

            # Get agent status
            agents = conn.execute('''
                SELECT status, COUNT(*) as count
                FROM customer_agents
                WHERE customer_id = ?
                GROUP BY status
            ''', (customer_id,)).fetchall()

            # Get task status
            tasks = conn.execute('''
                SELECT status, COUNT(*) as count
                FROM agent_tasks
                WHERE customer_id = ?
                GROUP BY status
            ''', (customer_id,)).fetchall()

            # Get total revenue
            total_revenue = conn.execute('''
                SELECT SUM(amount) as total
                FROM revenue_tracking
                WHERE customer_id = ?
            ''', (customer_id,)).fetchone()

            return {
                'customer': dict(customer) if customer else None,
                'analytics': dict(analytics) if analytics else None,
                'agents_by_status': {row[0]: row[1] for row in agents},
                'tasks_by_status': {row[0]: row[1] for row in tasks},
                'total_revenue': total_revenue[0] if total_revenue and total_revenue[0] else 0.0
            }

    def update_agent_health(self, customer_id, agent_id, health_status):
        """Update agent health status"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    UPDATE customer_agents
                    SET health_status = ?, last_active = CURRENT_TIMESTAMP
                    WHERE customer_id = ? AND agent_id = ?
                ''', (health_status, customer_id, agent_id))

                conn.commit()
                return {'success': True}

        except Exception as e:
            return {'success': False, 'error': f'Health update failed: {str(e)}'}

    def get_all_active_customers(self):
        """Get all active customers for monitoring"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            customers = conn.execute('''
                SELECT customer_id, email, plan_type, agent_count,
                       subscription_status, activation_date
                FROM customers
                WHERE subscription_status = 'active'
                ORDER BY activation_date DESC
            ''').fetchall()

            return [dict(customer) for customer in customers]

    def get_revenue_summary(self, days=30):
        """Get revenue summary for specified time period"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            summary = conn.execute(f'''
                SELECT
                    COUNT(DISTINCT customer_id) as customer_count,
                    SUM(amount) as total_revenue,
                    AVG(amount) as avg_transaction,
                    revenue_stream,
                    transaction_type
                FROM revenue_tracking
                WHERE recorded_at >= datetime('now', '-{days} days')
                GROUP BY revenue_stream, transaction_type
            ''').fetchall()

            return [dict(row) for row in summary]

# Initialize customer database
customer_db = CustomerDatabase()
