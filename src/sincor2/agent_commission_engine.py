"""
SINCOR Agent Commission Engine
Autonomous tracking and payout of agent commissions based on sales contributions
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

try:
    from src.sincor2.commission_payout_engine import UnifiedPayoutEngine
    payout_engine = UnifiedPayoutEngine()
except ImportError:
    payout_engine = None
    logger_temp = logging.getLogger(__name__)
    logger_temp.warning("PayPal payout engine not available")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentCommissionEngine:
    """Manages agent commissions and payouts"""

    def __init__(self, db_path='data/sincor.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Commission structure (can be configured per agent type)
        self.commission_rates = {
            'outreach': 0.03,        # 3% for agent that did initial outreach
            'qualification': 0.02,   # 2% for agent that qualified the lead
            'proposal': 0.02,        # 2% for agent that generated proposal
            'closing': 0.10          # 10% for agent that closed the deal
        }

    def calculate_commission(self, deal_amount, touchpoint_type):
        """Calculate commission amount based on deal and touchpoint"""
        rate = self.commission_rates.get(touchpoint_type, 0)
        return deal_amount * rate

    def get_agent_balance(self, agent_name):
        """Get current commission balance for an agent"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            # Get earned commissions
            cursor.execute('''
                SELECT COALESCE(SUM(commission_amount), 0) FROM agent_commissions
                WHERE agent_name = ? AND earned_at IS NOT NULL
            ''', (agent_name,))
            earned = cursor.fetchone()[0]

            # Get paid commissions
            cursor.execute('''
                SELECT COALESCE(SUM(commission_amount), 0) FROM agent_commissions
                WHERE agent_name = ? AND paid = 1
            ''', (agent_name,))
            paid = cursor.fetchone()[0]

            conn.close()

            return {
                'agent_name': agent_name,
                'total_earned': float(earned),
                'total_paid': float(paid),
                'outstanding': float(earned - paid)
            }
        except Exception as e:
            logger.error(f"Error getting agent balance: {e}")
            return None

    def get_agent_commissions_pending(self, agent_name=None, limit=50):
        """Get pending (unpaid) commissions for an agent"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            if agent_name:
                cursor.execute('''
                    SELECT id, agent_name, lead_id, touchpoint_type, commission_amount,
                           earned_at, paid
                    FROM agent_commissions
                    WHERE agent_name = ? AND paid = 0
                    ORDER BY earned_at DESC
                    LIMIT ?
                ''', (agent_name, limit))
            else:
                cursor.execute('''
                    SELECT id, agent_name, lead_id, touchpoint_type, commission_amount,
                           earned_at, paid
                    FROM agent_commissions
                    WHERE paid = 0
                    ORDER BY earned_at DESC
                    LIMIT ?
                ''', (limit,))

            results = cursor.fetchall()
            conn.close()

            return results
        except Exception as e:
            logger.error(f"Error getting pending commissions: {e}")
            return []

    def get_commission_history(self, agent_name, months=3):
        """Get commission history for the past N months"""
        try:
            from datetime import timedelta
            cutoff_date = (datetime.utcnow() - timedelta(days=30*months)).isoformat()

            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT earned_at, touchpoint_type, commission_amount, paid
                FROM agent_commissions
                WHERE agent_name = ? AND earned_at > ?
                ORDER BY earned_at DESC
            ''', (agent_name, cutoff_date))

            results = cursor.fetchall()
            conn.close()

            # Aggregate by month
            monthly = {}
            for row in results:
                earned_at = row[0][:7]  # YYYY-MM
                touchpoint = row[1]
                amount = row[2]

                if earned_at not in monthly:
                    monthly[earned_at] = {'total': 0, 'by_type': {}}

                monthly[earned_at]['total'] += amount
                if touchpoint not in monthly[earned_at]['by_type']:
                    monthly[earned_at]['by_type'][touchpoint] = 0
                monthly[earned_at]['by_type'][touchpoint] += amount

            return monthly
        except Exception as e:
            logger.error(f"Error getting commission history: {e}")
            return {}

    def get_top_earning_agents(self, limit=10):
        """Get top earning agents by total commission"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT agent_name, COUNT(*) as commissions, SUM(commission_amount) as total
                FROM agent_commissions
                WHERE earned_at IS NOT NULL
                GROUP BY agent_name
                ORDER BY total DESC
                LIMIT ?
            ''', (limit,))

            results = cursor.fetchall()
            conn.close()

            return [
                {
                    'agent_name': r[0],
                    'commission_count': r[1],
                    'total_earned': float(r[2])
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error getting top agents: {e}")
            return []

    def mark_commission_paid(self, commission_id, transaction_id=None):
        """Mark a commission as paid"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE agent_commissions SET
                    paid = 1, paid_at = ?
                WHERE id = ?
            ''', (datetime.utcnow(), commission_id))

            conn.commit()
            conn.close()

            logger.info(f"Marked commission {commission_id} as paid")
            return True
        except Exception as e:
            logger.error(f"Error marking commission paid: {e}")
            return False

    def batch_payout(self, agent_name=None, payment_method='paypal'):
        """
        Process payout for all pending commissions of an agent (or all agents).
        NOW INTEGRATED WITH PAYPAL & CRYPTO PAYOUT ENGINES
        """
        try:
            # Get pending commissions
            if agent_name:
                pending = self.get_agent_commissions_pending(agent_name)
            else:
                pending = self.get_agent_commissions_pending()

            if not pending:
                return {
                    'status': 'no_pending',
                    'agent': agent_name,
                    'total_amount': 0,
                    'commission_count': 0
                }

            # Group by agent
            by_agent = {}
            for commission in pending:
                agent = commission[1]
                amount = commission[4]
                commission_id = commission[0]
                if agent not in by_agent:
                    by_agent[agent] = []
                by_agent[agent].append({
                    'id': commission_id,
                    'amount': amount
                })

            # Get agent payment preferences from database
            payout_results = []
            total_paid = 0

            for agent, commissions in by_agent.items():
                total_amount = sum(c['amount'] for c in commissions)

                # Get agent's payment preferences (stored in database or config)
                agent_info = self._get_agent_payment_info(agent, payment_method)

                if not agent_info or not payout_engine:
                    logger.warning(f"Cannot process payout for {agent}: Missing info or payout engine")
                    payout_results.append({
                        'agent': agent,
                        'commission_count': len(commissions),
                        'total_amount': float(total_amount),
                        'status': 'pending',
                        'reason': 'Agent info incomplete or payout engine unavailable'
                    })
                    continue

                # Send actual payout
                payout_result = payout_engine.payout_to_agent(
                    agent_info=agent_info,
                    amount=total_amount,
                    commission_id=f"{agent}_{int(datetime.utcnow().timestamp())}"
                )

                # Update database based on result
                if payout_result['status'] == 'success':
                    for commission in commissions:
                        self.mark_commission_paid(
                            commission['id'],
                            transaction_id=payout_result.get('payout_id', payout_result.get('tx_hash'))
                        )
                    total_paid += total_amount

                payout_results.append({
                    'agent': agent,
                    'commission_count': len(commissions),
                    'total_amount': float(total_amount),
                    'status': payout_result['status'],
                    'payout_id': payout_result.get('payout_id'),
                    'message': payout_result.get('message')
                })

                logger.info(f"Payout result for {agent}: {payout_result['status']} - ${total_amount:.2f}")

            return {
                'status': 'success',
                'payouts': payout_results,
                'total_amount_paid': float(total_paid),
                'total_attempted': sum(p['total_amount'] for p in payout_results),
                'agent_count': len(payout_results),
                'payment_method': payment_method,
                'timestamp': datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Error processing batch payout: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }

    def _get_agent_payment_info(self, agent_name, payment_method='paypal'):
        """
        Get agent's payment information from database.
        This would normally come from agent profile/settings.
        """
        # In production, query agent preferences table
        # For MVP, return mock data
        agent_info = {
            'name': agent_name,
            'payment_method': payment_method,
        }

        if payment_method.lower() == 'paypal':
            # Would query from database
            agent_info['paypal_email'] = f"{agent_name.lower().replace(' ', '.')}@example.com"
        elif payment_method.lower() == 'crypto':
            agent_info['crypto_address'] = '0x' + 'a' * 40  # Placeholder
            agent_info['crypto_type'] = 'ETH'

        return agent_info

    def record_commission_activity(self, agent_name):
        """Get detailed activity log for an agent's commissions"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT
                    ac.id, ac.lead_id, ac.touchpoint_type, ac.commission_amount,
                    ac.earned_at, ac.paid,
                    l.company_name, so.status, so.final_amount
                FROM agent_commissions ac
                LEFT JOIN leads l ON ac.lead_id = l.id
                LEFT JOIN sales_opportunities so ON ac.sales_opportunity_id = so.id
                WHERE ac.agent_name = ?
                ORDER BY ac.earned_at DESC
                LIMIT 100
            ''', (agent_name,))

            results = cursor.fetchall()
            conn.close()

            return [
                {
                    'commission_id': r[0],
                    'lead_id': r[1],
                    'touchpoint': r[2],
                    'amount': float(r[3]),
                    'earned_at': r[4],
                    'paid': bool(r[5]),
                    'company': r[6],
                    'deal_status': r[7],
                    'deal_value': float(r[8]) / 100 if r[8] else 0  # cents to dollars
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error getting commission activity: {e}")
            return []

    def get_commission_dashboard(self, agent_name=None):
        """Get comprehensive commission dashboard"""
        try:
            balance = self.get_agent_balance(agent_name) if agent_name else None

            if agent_name:
                pending = self.get_agent_commissions_pending(agent_name, limit=100)
                history = self.get_commission_history(agent_name)
                activity = self.record_commission_activity(agent_name)

                return {
                    'agent_name': agent_name,
                    'balance': balance,
                    'pending_count': len(pending),
                    'pending_amount': sum(p[4] for p in pending),
                    'monthly_history': history,
                    'recent_activity': activity[:10]
                }
            else:
                top_agents = self.get_top_earning_agents(limit=10)
                total_paid_out = 0
                total_pending = 0

                for agent_info in top_agents:
                    balance = self.get_agent_balance(agent_info['agent_name'])
                    if balance:
                        total_paid_out += balance['total_paid']
                        total_pending += balance['outstanding']

                return {
                    'top_earning_agents': top_agents,
                    'total_paid_out': float(total_paid_out),
                    'total_pending': float(total_pending),
                    'agent_count': len(top_agents)
                }
        except Exception as e:
            logger.error(f"Error getting commission dashboard: {e}")
            return {}
