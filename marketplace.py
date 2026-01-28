#!/usr/bin/env python3
"""
SINC Agent Marketplace Backend
Handles AI agent rentals and marketplace operations
"""

import os
import time
import json
from datetime import datetime, timedelta
from flask import request, jsonify
from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SINCMarketplace:
    def __init__(self):
        # Agent catalog
        self.agents = {
            'content-writer': {
                'id': 'content-writer',
                'name': 'Content Writer Agent',
                'description': 'AI-powered content creation for blogs, articles, and marketing materials',
                'price_per_hour': 50,
                'category': 'Content',
                'rating': 4.8,
                'users': 1250,
                'features': ['SEO Optimization', 'Multi-language', 'Brand Voice']
            },
            'data-analyzer': {
                'id': 'data-analyzer',
                'name': 'Data Analysis Agent',
                'description': 'Advanced data processing, visualization, and business intelligence',
                'price_per_hour': 75,
                'category': 'Analytics',
                'rating': 4.9,
                'users': 890,
                'features': ['Real-time Processing', 'Custom Dashboards', 'Predictive Models']
            },
            'social-manager': {
                'id': 'social-manager',
                'name': 'Social Media Manager',
                'description': 'Automated social media posting, engagement, and growth optimization',
                'price_per_hour': 40,
                'category': 'Social',
                'rating': 4.7,
                'users': 2100,
                'features': ['Multi-platform', 'Content Scheduling', 'Analytics']
            },
            'email-marketer': {
                'id': 'email-marketer',
                'name': 'Email Marketing Agent',
                'description': 'Intelligent email campaigns, automation, and lead nurturing',
                'price_per_hour': 60,
                'category': 'Marketing',
                'rating': 4.6,
                'users': 750,
                'features': ['A/B Testing', 'Segmentation', 'Automation']
            },
            'customer-support': {
                'id': 'customer-support',
                'name': 'Customer Support Agent',
                'description': '24/7 AI customer service with natural language processing',
                'price_per_hour': 45,
                'category': 'Support',
                'rating': 4.8,
                'users': 1800,
                'features': ['Multi-language', 'Ticket Routing', 'Knowledge Base']
            },
            'code-assistant': {
                'id': 'code-assistant',
                'name': 'Code Assistant Agent',
                'description': 'AI programming assistant for code review, debugging, and development',
                'price_per_hour': 80,
                'category': 'Development',
                'rating': 4.9,
                'users': 650,
                'features': ['Multi-language', 'Code Review', 'Documentation']
            }
        }

        # Blockchain setup for payments
        self.rpc_url = os.getenv('BASE_RPC_URL', 'https://mainnet.base.org')
        self.private_key = os.getenv('PRIVATE_KEY')
        self.sinc_contract_address = '0xd10D86D09ee4316CdD3585fd6486537b7119A073'

        # Allow forcing demo mode via environment for tests and local dev
        force_demo = os.getenv('MARKETPLACE_FORCE_DEMO', '').lower() in ('1', 'true', 'yes')
        self.force_demo = force_demo

        if force_demo:
            logger.info("MARKETPLACE_FORCE_DEMO set - forcing demo mode")
            self.blockchain_enabled = False
        elif not self.private_key:
            logger.warning("PRIVATE_KEY not set - marketplace will work in demo mode")
            self.blockchain_enabled = False
        else:
            # Initialize Web3
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

            if not self.w3.is_connected():
                logger.warning("Cannot connect to Base network - marketplace in demo mode")
                self.blockchain_enabled = False
            else:
                self.account = Account.from_key(self.private_key)
                self.blockchain_enabled = True
                logger.info(f"Marketplace wallet: {self.account.address}")

                # SINC Token ABI
                self.sinc_abi = [
                    {
                        "constant": True,
                        "inputs": [{"name": "owner", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "type": "function"
                    },
                    {
                        "constant": False,
                        "inputs": [
                            {"name": "to", "type": "address"},
                            {"name": "value", "type": "uint256"}
                        ],
                        "name": "transfer",
                        "outputs": [{"name": "", "type": "bool"}],
                        "type": "function"
                    }
                ]

                self.sinc_contract = self.w3.eth.contract(
                    address=self.sinc_contract_address,
                    abi=self.sinc_abi
                )

        # Load rental history
        self.rental_history_file = 'marketplace_rentals.json'
        self.rental_history = self._load_rental_history()

        # Load reviews
        self.reviews_file = 'marketplace_reviews.json'
        self.reviews = self._load_reviews()

        # Load subscriptions
        self.subscriptions_file = 'marketplace_subscriptions.json'
        self.subscriptions = self._load_subscriptions()

        # Load pending on-chain payments
        self.payments_file = 'marketplace_payments.json'
        self.pending_payments = self._load_pending_payments()

        # If demo mode is forced, reset pending payments to avoid stale/settled records
        if getattr(self, 'force_demo', False):
            self.pending_payments = []
            try:
                self._save_pending_payments()
            except Exception:
                pass

    def _load_rental_history(self):
        """Load rental history from file"""
        try:
            if os.path.exists(self.rental_history_file):
                with open(self.rental_history_file, 'r') as f:
                    data = json.load(f)
                    # Convert timestamps
                    for rental in data:
                        rental['start_time'] = datetime.fromisoformat(rental['start_time'])
                        rental['end_time'] = datetime.fromisoformat(rental['end_time'])
                    return data
        except Exception as e:
            logger.error(f"Error loading rental history: {e}")

        return []

    def _save_rental_history(self):
        """Save rental history to file"""
        try:
            data = []
            for rental in self.rental_history:
                data.append({
                    'agent_id': rental['agent_id'],
                    'user_address': rental['user_address'],
                    'hours': rental['hours'],
                    'cost': rental['cost'],
                    'start_time': rental['start_time'].isoformat() if isinstance(rental.get('start_time'), datetime) else str(rental.get('start_time')),
                    'end_time': rental['end_time'].isoformat() if isinstance(rental.get('end_time'), datetime) else str(rental.get('end_time')),
                    'status': rental.get('status', 'active'),
                    'payment_id': rental.get('payment_id')
                })

            with open(self.rental_history_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving rental history: {e}")

    def _load_reviews(self):
        """Load reviews from file"""
        try:
            if os.path.exists(self.reviews_file):
                with open(self.reviews_file, 'r') as f:
                    data = json.load(f)
                    # Convert timestamps
                    for review in data:
                        review['created_at'] = datetime.fromisoformat(review['created_at'])
                        review['updated_at'] = datetime.fromisoformat(review['updated_at'])
                    return data
        except Exception as e:
            logger.error(f"Error loading reviews: {e}")

        return []

    def _save_reviews(self):
        """Save reviews to file"""
        try:
            data = []
            for review in self.reviews:
                data.append({
                    'id': review['id'],
                    'agent_id': review['agent_id'],
                    'user_address': review['user_address'],
                    'rating': review['rating'],
                    'comment': review['comment'],
                    'created_at': review['created_at'].isoformat(),
                    'updated_at': review['updated_at'].isoformat(),
                    'helpful_votes': review['helpful_votes']
                })

            with open(self.reviews_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving reviews: {e}")

    def _load_subscriptions(self):
        """Load subscriptions from file"""
        try:
            if os.path.exists(self.subscriptions_file):
                with open(self.subscriptions_file, 'r') as f:
                    data = json.load(f)
                    # Convert timestamps
                    for sub in data:
                        sub['start_date'] = datetime.fromisoformat(sub['start_date'])
                        sub['end_date'] = datetime.fromisoformat(sub['end_date'])
                        sub['last_renewal'] = datetime.fromisoformat(sub['last_renewal'])
                    return data
        except Exception as e:
            logger.error(f"Error loading subscriptions: {e}")

        return []

    def _save_subscriptions(self):
        """Save subscriptions to file"""
        try:
            data = []
            for sub in self.subscriptions:
                data.append({
                    'id': sub['id'],
                    'user_address': sub['user_address'],
                    'plan_type': sub['plan_type'],
                    'start_date': sub['start_date'].isoformat(),
                    'end_date': sub['end_date'].isoformat(),
                    'last_renewal': sub['last_renewal'].isoformat(),
                    'auto_renew': sub['auto_renew'],
                    'status': sub['status']
                })

            with open(self.subscriptions_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving subscriptions: {e}")

    def _load_pending_payments(self):
        """Load pending on-chain payments from file"""
        try:
            if os.path.exists(self.payments_file):
                with open(self.payments_file, 'r') as f:
                    data = json.load(f)
                    return data
        except Exception as e:
            logger.error(f"Error loading pending payments: {e}")
        return []

    def _save_pending_payments(self):
        """Save pending payments to file"""
        try:
            with open(self.payments_file, 'w') as f:
                json.dump(self.pending_payments, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving pending payments: {e}")

    def get_agents(self, category=None):
        """Get available agents, optionally filtered by category"""
        agents = list(self.agents.values())

        if category and category != 'All':
            agents = [a for a in agents if a['category'] == category]

        return agents

    def is_valid_address(self, addr: str) -> bool:
        """Validate an Ethereum-style address.
        Uses web3 if available, otherwise fall back to a simple heuristic.
        """
        if not addr or not isinstance(addr, str):
            return False

        if getattr(self, 'blockchain_enabled', False) and hasattr(self, 'w3'):
            try:
                return self.w3.is_address(addr)
            except Exception:
                # Fallback heuristic
                pass

        # Simple heuristic for demo: must start with 0x and be 42 characters
        return addr.startswith('0x') and len(addr) == 42

    def get_agent_categories(self):
        """Get unique agent categories"""
        categories = ['All'] + list(set(a['category'] for a in self.agents.values()))
        return categories

    def get_marketplace_stats(self):
        """Get marketplace statistics"""
        total_rentals = len(self.rental_history)
        active_rentals = len([r for r in self.rental_history if r['status'] == 'active'])
        total_earned = sum(r['cost'] for r in self.rental_history)

        return {
            'total_agents': len(self.agents),
            'total_rentals': total_rentals,
            'active_rentals': active_rentals,
            'total_earned': total_earned
        }

    def rent_agent(self, agent_id, user_address, hours):
        """Process agent rental request"""
        try:
            # Validate inputs
            if agent_id not in self.agents:
                return {
                    'success': False,
                    'error': 'Agent not found'
                }

            if not self.is_valid_address(user_address):
                return {
                    'success': False,
                    'error': 'Invalid wallet address'
                }

            try:
                hours = int(hours)
                if hours < 1 or hours > 168:  # Max 1 week
                    return {
                        'success': False,
                        'error': 'Hours must be between 1 and 168'
                    }
            except ValueError:
                return {
                    'success': False,
                    'error': 'Invalid hours'
                }

            agent = self.agents[agent_id]
            base_cost = agent['price_per_hour'] * hours
            cost = self.get_discounted_price(user_address, base_cost)

            # In demo mode, skip blockchain transaction
            if not self.blockchain_enabled:
                logger.info(f"Demo rental: {agent_id} for {user_address} ({hours}h, {cost} SINC)")

                # Record rental
                rental = {
                    'agent_id': agent_id,
                    'user_address': user_address,
                    'hours': hours,
                    'cost': cost,
                    'start_time': datetime.now(),
                    'end_time': datetime.now() + timedelta(hours=hours),
                    'status': 'active'
                }

                self.rental_history.append(rental)
                self._save_rental_history()

                return {
                    'success': True,
                    'agent': agent,
                    'hours': hours,
                    'cost': cost,
                    'message': f'Successfully rented {agent["name"]} for {hours} hours (Demo Mode)'
                }

            # Production mode: Process blockchain payment
            # Check user's balance
            user_balance = self.sinc_contract.functions.balanceOf(user_address).call()
            cost_wei = self.w3.to_wei(cost, 'ether')

            if user_balance < cost_wei:
                return {
                    'success': False,
                    'error': f'Insufficient SINC balance. Need {cost} SINC, have {self.w3.from_wei(user_balance, "ether")}'
                }

            # Note: In a real implementation, the user would need to approve the contract first
            # For now, we'll simulate the rental

            logger.info(f"Production rental: {agent_id} for {user_address} ({hours}h, {cost} SINC)")

            # Record rental
            rental = {
                'agent_id': agent_id,
                'user_address': user_address,
                'hours': hours,
                'cost': cost,
                'start_time': datetime.now(),
                'end_time': datetime.now() + timedelta(hours=hours),
                'status': 'active'
            }

            self.rental_history.append(rental)
            self._save_rental_history()

            return {
                'success': True,
                'agent': agent,
                'hours': hours,
                'cost': cost,
                'message': f'Successfully rented {agent["name"]} for {hours} hours'
            }

        except Exception as e:
            logger.error(f"Error processing rental: {e}")
            return {
                'success': False,
                'error': 'Internal error. Please try again later.'
            }

    def get_agent_reviews(self, agent_id, limit=10, offset=0):
        """Get reviews for a specific agent"""
        agent_reviews = [r for r in self.reviews if r['agent_id'] == agent_id]
        # Sort by creation date (newest first)
        agent_reviews.sort(key=lambda x: x['created_at'], reverse=True)

        # Calculate average rating
        if agent_reviews:
            avg_rating = sum(r['rating'] for r in agent_reviews) / len(agent_reviews)
            total_reviews = len(agent_reviews)
        else:
            avg_rating = 0
            total_reviews = 0

        # Paginate
        paginated_reviews = agent_reviews[offset:offset + limit]

        return {
            'reviews': paginated_reviews,
            'average_rating': round(avg_rating, 1),
            'total_reviews': total_reviews,
            'has_more': len(agent_reviews) > offset + limit
        }

    def add_review(self, agent_id, user_address, rating, comment):
        """Add a new review"""
        try:
            # Validate inputs
            if agent_id not in self.agents:
                return {
                    'success': False,
                    'error': 'Agent not found'
                }

            if not self.is_valid_address(user_address):
                return {
                    'success': False,
                    'error': 'Invalid wallet address'
                }

            if not (1 <= rating <= 5):
                return {
                    'success': False,
                    'error': 'Rating must be between 1 and 5'
                }

            if not comment or len(comment.strip()) < 10:
                return {
                    'success': False,
                    'error': 'Comment must be at least 10 characters'
                }

            # Check if user has rented this agent (basic validation)
            user_rentals = [r for r in self.rental_history
                          if r['user_address'].lower() == user_address.lower()
                          and r['agent_id'] == agent_id]

            if not user_rentals:
                return {
                    'success': False,
                    'error': 'You must rent this agent before reviewing it'
                }

            # Check if user already reviewed this agent
            existing_review = next((r for r in self.reviews
                                  if r['agent_id'] == agent_id
                                  and r['user_address'].lower() == user_address.lower()), None)

            now = datetime.now()

            if existing_review:
                # Update existing review
                existing_review['rating'] = rating
                existing_review['comment'] = comment.strip()
                existing_review['updated_at'] = now
            else:
                # Create new review
                review = {
                    'id': f"{agent_id}_{user_address}_{int(now.timestamp())}",
                    'agent_id': agent_id,
                    'user_address': user_address,
                    'rating': rating,
                    'comment': comment.strip(),
                    'created_at': now,
                    'updated_at': now,
                    'helpful_votes': 0
                }
                self.reviews.append(review)

            self._save_reviews()

            # Update agent's cached rating
            self._update_agent_rating(agent_id)

            return {
                'success': True,
                'message': 'Review submitted successfully'
            }

        except Exception as e:
            logger.error(f"Error adding review: {e}")
            return {
                'success': False,
                'error': 'Internal error. Please try again later.'
            }

    def _update_agent_rating(self, agent_id):
        """Update the cached rating for an agent"""
        if agent_id in self.agents:
            agent_reviews = [r for r in self.reviews if r['agent_id'] == agent_id]
            if agent_reviews:
                avg_rating = sum(r['rating'] for r in agent_reviews) / len(agent_reviews)
                self.agents[agent_id]['rating'] = round(avg_rating, 1)
                self.agents[agent_id]['users'] = len(agent_reviews)  # Update review count as users

    def vote_helpful(self, review_id, user_address):
        """Vote a review as helpful"""
        try:
            review = next((r for r in self.reviews if r['id'] == review_id), None)
            if not review:
                return {
                    'success': False,
                    'error': 'Review not found'
                }

            # Simple vote tracking (in production, track per user)
            review['helpful_votes'] += 1
            self._save_reviews()

            return {
                'success': True,
                'helpful_votes': review['helpful_votes']
            }

        except Exception as e:
            logger.error(f"Error voting helpful: {e}")
            return {
                'success': False,
                'error': 'Internal error'
            }

    def get_subscription_plans(self):
        """Get available subscription plans"""
        return {
            'basic': {
                'name': 'Basic Plan',
                'price_monthly': 500,  # SINC tokens
                'discount_percent': 10,
                'features': ['10% discount on rentals', 'Priority support', 'Usage analytics']
            },
            'pro': {
                'name': 'Pro Plan',
                'price_monthly': 1000,  # SINC tokens
                'discount_percent': 20,
                'features': ['20% discount on rentals', 'Priority support', 'Advanced analytics', 'Custom agent requests']
            },
            'enterprise': {
                'name': 'Enterprise Plan',
                'price_monthly': 2500,  # SINC tokens
                'discount_percent': 30,
                'features': ['30% discount on rentals', 'Dedicated support', 'Custom integrations', 'White-label options']
            }
        }

    def subscribe_user(self, user_address, plan_type):
        """Subscribe a user to a plan"""
        try:
            if not self.is_valid_address(user_address):
                return {
                    'success': False,
                    'error': 'Invalid wallet address'
                }

            plans = self.get_subscription_plans()
            if plan_type not in plans:
                return {
                    'success': False,
                    'error': 'Invalid plan type'
                }

            plan = plans[plan_type]
            monthly_cost = plan['price_monthly']

            # Check for existing active subscription
            existing_sub = next((s for s in self.subscriptions
                               if s['user_address'].lower() == user_address.lower()
                               and s['status'] == 'active'), None)

            if existing_sub:
                return {
                    'success': False,
                    'error': 'You already have an active subscription'
                }

            now = datetime.now()
            end_date = now + timedelta(days=30)  # 30 days

            subscription = {
                'id': f"sub_{user_address}_{int(now.timestamp())}",
                'user_address': user_address,
                'plan_type': plan_type,
                'start_date': now,
                'end_date': end_date,
                'last_renewal': now,
                'auto_renew': True,
                'status': 'active'
            }

            self.subscriptions.append(subscription)
            self._save_subscriptions()

            return {
                'success': True,
                'subscription': {
                    'id': subscription['id'],
                    'plan_type': plan_type,
                    'end_date': end_date.isoformat(),
                    'monthly_cost': monthly_cost,
                    'discount_percent': plan['discount_percent']
                },
                'message': f'Successfully subscribed to {plan["name"]}'
            }

        except Exception as e:
            logger.error(f"Error creating subscription: {e}")
            return {
                'success': False,
                'error': 'Internal error. Please try again later.'
            }

    def get_user_subscription(self, user_address):
        """Get user's active subscription"""
        if not self.is_valid_address(user_address):
            return None

        subscription = next((s for s in self.subscriptions
                           if s['user_address'].lower() == user_address.lower()
                           and s['status'] == 'active'), None)

        if subscription:
            # Check if expired
            now = datetime.now()
            if now > subscription['end_date']:
                subscription['status'] = 'expired'
                self._save_subscriptions()
                return None

            plans = self.get_subscription_plans()
            plan = plans.get(subscription['plan_type'], {})

            return {
                'id': subscription['id'],
                'plan_type': subscription['plan_type'],
                'plan_name': plan.get('name', subscription['plan_type']),
                'start_date': subscription['start_date'].isoformat(),
                'end_date': subscription['end_date'].isoformat(),
                'auto_renew': subscription['auto_renew'],
                'discount_percent': plan.get('discount_percent', 0),
                'features': plan.get('features', [])
            }

        return None

    def cancel_subscription(self, user_address):
        """Cancel user's subscription"""
        try:
            subscription = next((s for s in self.subscriptions
                               if s['user_address'].lower() == user_address.lower()
                               and s['status'] == 'active'), None)

            if not subscription:
                return {
                    'success': False,
                    'error': 'No active subscription found'
                }

            subscription['auto_renew'] = False
            subscription['status'] = 'cancelled'
            self._save_subscriptions()

            return {
                'success': True,
                'message': 'Subscription cancelled successfully'
            }

        except Exception as e:
            logger.error(f"Error cancelling subscription: {e}")
            return {
                'success': False,
                'error': 'Internal error'
            }

    def get_discounted_price(self, user_address, original_price):
        """Get discounted price for subscribed user"""
        subscription = self.get_user_subscription(user_address)
        if subscription:
            discount = subscription['discount_percent'] / 100
            return original_price * (1 - discount)
        return original_price

    def generate_payment_request(self, agent_id, user_address, hours):
        """Generate an on-chain payment request for renting an agent.
        Returns payment instruction (marketplace address and amount) and records pending payment.
        """
        try:
            if agent_id not in self.agents:
                return {'success': False, 'error': 'Agent not found'}

            agent = self.agents[agent_id]
            base_cost = agent['price_per_hour'] * int(hours)
            cost = self.get_discounted_price(user_address, base_cost)

            payment_id = f"pay_{agent_id}_{user_address}_{int(time.time())}"

            # Determine marketplace address where user should send tokens
            mkt_address = getattr(self, 'account', None).address if getattr(self, 'blockchain_enabled', False) else 'DEMO-MARKETPLACE-ADDRESS'

            # Record the payment request with initial snapshot balance
            initial_balance = 0
            if getattr(self, 'blockchain_enabled', False):
                try:
                    initial_balance = self.sinc_contract.functions.balanceOf(mkt_address).call()
                except Exception as e:
                    logger.warning(f"Could not get initial marketplace balance: {e}")
                    initial_balance = 0

            cost_wei = None
            if getattr(self, 'blockchain_enabled', False) and hasattr(self, 'w3'):
                try:
                    cost_wei = self.w3.to_wei(cost, 'ether')
                except Exception as e:
                    logger.warning(f"Could not convert cost to wei: {e}")
                    cost_wei = None

            payment_record = {
                'payment_id': payment_id,
                'agent_id': agent_id,
                'user_address': user_address,
                'hours': int(hours),
                'cost': cost,
                'cost_wei': cost_wei,
                'marketplace_address': mkt_address,
                'initial_balance': initial_balance,
                'status': 'pending',
                'created_at': datetime.now().isoformat()
            }

            self.pending_payments.append(payment_record)
            self._save_pending_payments()

            return {
                'success': True,
                'payment_id': payment_id,
                'marketplace_address': mkt_address,
                'amount': cost,
                'currency': 'SINC',
                'message': f"Send {cost} SINC to {mkt_address} with reference {payment_id}"
            }

        except Exception as e:
            logger.error(f"Error generating payment request: {e}")
            return {'success': False, 'error': 'Internal error'}

    def check_payment_received(self, payment_id, confirmations: int = 0):
        """Check whether a pending payment has been received on-chain (demo-safe).
        If received, mark as completed and create the rental record.
        """
        try:
            payment = next((p for p in self.pending_payments if p['payment_id'] == payment_id), None)
            if not payment:
                return {'success': False, 'error': 'Payment not found'}

            if payment['status'] != 'pending':
                return {'success': True, 'status': payment['status']}

            if not getattr(self, 'blockchain_enabled', False):
                # In demo mode, we simulate immediate reception for testing
                payment['status'] = 'received'
                payment['received_at'] = datetime.now().isoformat()
                self._save_pending_payments()

                # Create the rental now
                return self._finalize_rental_after_payment(payment)

            # Production check: see if marketplace balance grew by cost_wei
            mkt_addr = payment['marketplace_address']
            try:
                current_balance = self.sinc_contract.functions.balanceOf(mkt_addr).call()
            except Exception as e:
                logger.error(f"Error checking balance for {mkt_addr}: {e}")
                return {'success': False, 'error': 'Chain query failed'}

            if current_balance - payment.get('initial_balance', 0) >= payment['cost_wei']:
                payment['status'] = 'received'
                payment['received_at'] = datetime.now().isoformat()
                self._save_pending_payments()
                return self._finalize_rental_after_payment(payment)

            return {'success': False, 'error': 'Payment not yet received'}

        except Exception as e:
            logger.error(f"Error checking payment: {e}")
            return {'success': False, 'error': 'Internal error'}

    def _finalize_rental_after_payment(self, payment):
        """Create rental and mark payment completed"""
        try:
            agent_id = payment['agent_id']
            user_address = payment['user_address']
            hours = payment['hours']
            cost = payment['cost']

            rental = {
                'agent_id': agent_id,
                'user_address': user_address,
                'hours': hours,
                'cost': cost,
                'start_time': datetime.now(),
                'end_time': datetime.now() + timedelta(hours=hours),
                'status': 'active',
                'payment_id': payment['payment_id']
            }

            self.rental_history.append(rental)
            self._save_rental_history()

            payment['status'] = 'completed'
            payment['rental_created_at'] = datetime.now().isoformat()
            self._save_pending_payments()

            return {'success': True, 'message': 'Payment confirmed and rental created', 'rental': rental}

        except Exception as e:
            logger.error(f"Error finalizing rental after payment: {e}")
            return {'success': False, 'error': 'Internal error'}

    def check_payments_onchain(self, payment_id: str | None = None, lookback_blocks: int = 5000):
        """Scan on-chain balances to confirm incoming SINC payments for outstanding pending payments.
        If payment_id is provided, only check that payment.
        This uses balanceOf checks and is safe in demo/production modes. Future improvement: use get_logs for Transfer events.
        """
        try:
            if not getattr(self, 'blockchain_enabled', False):
                return {'success': False, 'error': 'Blockchain disabled'}

            to_check = [p for p in self.pending_payments if p['status'] == 'pending']
            if payment_id:
                to_check = [p for p in to_check if p['payment_id'] == payment_id]

            results = []
            for p in to_check:
                mkt_addr = p['marketplace_address']
                try:
                    current_balance = self.sinc_contract.functions.balanceOf(mkt_addr).call()
                except Exception as e:
                    logger.error(f"Balance check failed for {mkt_addr}: {e}")
                    results.append({'payment_id': p['payment_id'], 'success': False, 'error': 'chain_query_failed'})
                    continue

                initial = p.get('initial_balance', 0) or 0
                if p.get('cost_wei') and (current_balance - initial) >= p['cost_wei']:
                    # mark received and finalize
                    p['status'] = 'received'
                    p['received_at'] = datetime.now().isoformat()
                    self._save_pending_payments()
                    finalize = self._finalize_rental_after_payment(p)
                    results.append({'payment_id': p['payment_id'], 'success': finalize.get('success', False)})
                else:
                    results.append({'payment_id': p['payment_id'], 'success': False, 'error': 'not_received'})

            return {'success': True, 'results': results}

        except Exception as e:
            logger.error(f"Error scanning on-chain payments: {e}")
            return {'success': False, 'error': 'Internal error'}

    def settle_payment(self, payment_id: str, agent_address: str, agent_share_percent: float = 0.8):
        """Settle a received payment by transferring SINC to the agent owner according to share percentage.
        In demo mode this will simulate a transfer. In production it builds, signs, and broadcasts a token transfer.
        """
        try:
            payment = next((p for p in self.pending_payments if p['payment_id'] == payment_id), None)
            if not payment:
                return {'success': False, 'error': 'Payment not found'}

            if payment['status'] != 'received' and payment['status'] != 'completed':
                return {'success': False, 'error': 'Payment not ready for settlement'}

            amount = float(payment['cost']) * float(agent_share_percent)

            if not getattr(self, 'blockchain_enabled', False):
                # simulate settlement
                tx = f"DEMO_SETTLE_{payment_id}_{int(time.time())}"
                payment['settlement_tx'] = tx
                payment['settled_at'] = datetime.now().isoformat()
                payment['status'] = 'settled'
                self._save_pending_payments()
                return {'success': True, 'tx': tx}

            # Production token transfer
            try:
                amount_wei = self.w3.to_wei(amount, 'ether')
            except Exception as e:
                logger.error(f"Failed to convert amount to wei: {e}")
                return {'success': False, 'error': 'conversion_failed'}

            try:
                tx = self.sinc_contract.functions.transfer(agent_address, amount_wei).buildTransaction({
                    'from': self.account.address,
                    'nonce': self.w3.eth.get_transaction_count(self.account.address),
                    'gas': 100000,
                    'gasPrice': self.w3.eth.gas_price
                })

                signed = self.account.sign_transaction(tx)
                tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
                payment['settlement_tx'] = tx_hash.hex()
                payment['settled_at'] = datetime.now().isoformat()
                payment['status'] = 'settled'
                self._save_pending_payments()

                return {'success': True, 'tx_hash': tx_hash.hex()}

            except Exception as e:
                logger.error(f"Error executing settlement tx: {e}")
                return {'success': False, 'error': 'settlement_failed'}

        except Exception as e:
            logger.error(f"Error settling payment: {e}")
            return {'success': False, 'error': 'Internal error'}

# Global marketplace instance
marketplace = None

def init_marketplace():
    """Initialize the marketplace instance"""
    global marketplace
    try:
        marketplace = SINCMarketplace()
        logger.info("Marketplace system initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize marketplace: {e}")
        return False

def get_agents():
    """Get agents, optionally filtered by category"""
    category = request.args.get('category', 'All')

    if not marketplace:
        return jsonify({
            'success': False,
            'error': 'Marketplace service unavailable'
        }), 503

    agents = marketplace.get_agents(category)
    return jsonify({
        'success': True,
        'agents': agents
    })

def get_categories():
    """Get agent categories"""
    if not marketplace:
        return jsonify(['All']), 503

    categories = marketplace.get_agent_categories()
    return jsonify(categories)

def get_marketplace_stats():
    """Get marketplace statistics"""
    if not marketplace:
        return jsonify({
            'total_agents': 6,
            'total_rentals': 0,
            'active_rentals': 0,
            'total_earned': 0
        }), 503

    return jsonify(marketplace.get_marketplace_stats())

def process_rental_request():
    """Process an agent rental request"""
    if not marketplace:
        return jsonify({
            'success': False,
            'error': 'Marketplace service unavailable'
        }), 503

    data = request.get_json()
    if not data or 'agent_id' not in data or 'address' not in data or 'hours' not in data:
        return jsonify({
            'success': False,
            'error': 'Missing agent_id, address, or hours'
        }), 400

    agent_id = data['agent_id']
    user_address = data['address']
    hours = data['hours']

    result = marketplace.rent_agent(agent_id, user_address, hours)

    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

def get_user_rentals():
    """Get user's rental history"""
    data = request.get_json()
    if not data or 'address' not in data:
        return jsonify({
            'success': False,
            'error': 'Missing address'
        }), 400

    user_address = data['address']

    if not marketplace:
        return jsonify({
            'success': False,
            'error': 'Marketplace service unavailable'
        }), 503

    rentals = marketplace.get_user_rentals(user_address)

    return jsonify({
        'success': True,
        'rentals': rentals
    })