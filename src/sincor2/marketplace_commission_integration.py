"""
SINCOR Agent Commission Integration with Marketplace

Ties agent purchases to commission calculation - agents who invest get higher rates.
This is where the magic happens: spending = earning more.
"""

import sqlite3
import logging
from datetime import datetime
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class MarketplaceEnabledCommissionEngine:
    """Commission engine that respects agent marketplace upgrades"""

    def __init__(self, commission_db_path='data/sincor.db',
                 marketplace_db_path='data/agent_marketplace.db'):
        self.commission_db = commission_db_path
        self.marketplace_db = marketplace_db_path

    def calculate_agent_commission(self, agent_id: str, deal_amount: float,
                                  touch_type: str) -> Tuple[float, Dict]:
        """
        Calculate commission with marketplace boosts applied

        touch_type: 'outreach' (3%), 'qualification' (2%), 'proposal' (2%), 'closing' (10%)

        Returns:
            (commission_amount, details_dict)
        """

        # Base rates
        base_rates = {
            'outreach': 0.03,
            'qualification': 0.02,
            'proposal': 0.02,
            'closing': 0.10
        }

        base_rate = base_rates.get(touch_type, 0.03)

        try:
            # Get agent tier and boosts from marketplace
            conn = sqlite3.connect(self.marketplace_db, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''SELECT current_tier, commission_boost
                            FROM agent_tiers WHERE agent_id = ?''', (agent_id,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                # Agent hasn't visited marketplace - base rate only
                tier = 'recruit'
                boost = 0.0
            else:
                tier = row[0]
                boost = row[1]

            # Tier multipliers (more valuable tiers get better deals)
            tier_multipliers = {
                'recruit': 1.0,       # No multiplier
                'scout': 1.2,         # 20% bonus
                'specialist': 1.5,    # 50% bonus
                'operator': 1.8,      # 80% bonus
                'leader': 2.0,        # 100% bonus (2x)
                'executive': 2.5      # 150% bonus (2.5x)
            }

            multiplier = tier_multipliers.get(tier, 1.0)

            # Calculate final commission
            # Final = (base_rate * tier_multiplier) + direct_boost
            base_with_tier = base_rate * multiplier
            final_rate = base_with_tier + boost

            commission = deal_amount * final_rate

            return commission, {
                'agent_id': agent_id,
                'deal_amount': deal_amount,
                'touch_type': touch_type,
                'base_rate': base_rate,
                'tier': tier,
                'tier_multiplier': multiplier,
                'commission_boost': boost,
                'final_rate': final_rate,
                'commission_amount': commission,
                'calculated_at': datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Error calculating commission: {e}")
            # Fallback to base rate on error
            return deal_amount * base_rate, {
                'error': str(e),
                'commission_amount': deal_amount * base_rate
            }

    def record_commission(self, agent_id: str, deal_amount: float,
                         touch_type: str, deal_id: str = None) -> Dict:
        """
        Record a commission earning and check if agent should auto-unlock marketplace items
        """

        # Calculate with boosts
        commission, details = self.calculate_agent_commission(agent_id, deal_amount, touch_type)

        try:
            conn = sqlite3.connect(self.commission_db, timeout=10.0)
            cursor = conn.cursor()

            # Record commission
            cursor.execute('''
                INSERT INTO agent_commissions
                (agent_id, deal_id, amount, touch_type, calculated_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (agent_id, deal_id or f"{agent_id}-{int(datetime.utcnow().timestamp())}",
                 commission, touch_type, datetime.utcnow().isoformat()))

            # Update agent balance
            cursor.execute('''
                UPDATE agent_balances
                SET earned_total = earned_total + ?,
                    last_earning_at = ?
                WHERE agent_id = ?
            ''', (commission, datetime.utcnow().isoformat(), agent_id))

            # Check if already exists
            cursor.execute('SELECT earned_total FROM agent_balances WHERE agent_id = ?', (agent_id,))
            if not cursor.fetchone():
                cursor.execute('''
                    INSERT INTO agent_balances (agent_id, earned_total, last_earning_at)
                    VALUES (?, ?, ?)
                ''', (agent_id, commission, datetime.utcnow().isoformat()))

            conn.commit()
            conn.close()

            details['recorded'] = True
            details['total_commission'] = commission

            # Check for milestone rewards (free upgrades!)
            milestone = self._check_earnings_milestone(agent_id, commission)
            if milestone:
                details['milestone_reward'] = milestone

            return details

        except Exception as e:
            logger.error(f"Error recording commission: {e}")
            return {'status': 'error', 'message': str(e)}

    def _check_earnings_milestone(self, agent_id: str, new_earnings: float) -> Dict or None:
        """
        Automatically reward agents who hit earning milestones with free upgrades

        This incentivizes high productivity - best agents get free tier upgrades
        """

        try:
            conn = sqlite3.connect(self.commission_db, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('SELECT SUM(amount) FROM agent_commissions WHERE agent_id = ?', (agent_id,))
            total = cursor.fetchone()[0] or 0
            conn.close()

            # Milestone rewards
            milestones = {
                500: {
                    'reward': 'Congratulations! You earned $500. Commission boost available!',
                    'unlock': 'commission_boost_eligible'
                },
                1500: {
                    'reward': 'Amazing! $1,500 earned. Scout tier upgrade available!',
                    'unlock': 'scout_tier_free'
                },
                4000: {
                    'reward': 'Exceptional! $4,000 earned. Specialist tier unlock!',
                    'unlock': 'specialist_tier_free'
                },
                10000: {
                    'reward': 'Legendary! $10,000+ earned. Operator tier available!',
                    'unlock': 'operator_tier_free'
                }
            }

            for milestone_amount, reward_info in sorted(milestones.items()):
                if total >= milestone_amount:
                    logger.info(f"Agent {agent_id} hit ${milestone_amount} milestone: {reward_info['reward']}")
                    return reward_info

            return None

        except Exception as e:
            logger.error(f"Error checking milestone: {e}")
            return None

    def get_agent_earnings_with_projections(self, agent_id: str) -> Dict:
        """
        Show agent their earnings and what happens if they upgrade
        """

        try:
            # Current earnings
            conn = sqlite3.connect(self.commission_db, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('SELECT SUM(amount) FROM agent_commissions WHERE agent_id = ?', (agent_id,))
            total_earned = cursor.fetchone()[0] or 0.0

            conn.close()

            # Get marketplace status
            conn = sqlite3.connect(self.marketplace_db, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''SELECT current_tier, commission_boost FROM agent_tiers
                            WHERE agent_id = ?''', (agent_id,))
            tier_row = cursor.fetchone()
            conn.close()

            current_tier = tier_row[0] if tier_row else 'recruit'
            current_boost = tier_row[1] if tier_row else 0.0

            # Tier progression mapping
            tier_progression = {
                'recruit': {
                    'current_multiplier': 1.0,
                    'next_tier': 'scout',
                    'next_cost': 250,
                    'next_multiplier': 1.2,
                    'improvement': '+20% on all commissions'
                },
                'scout': {
                    'current_multiplier': 1.2,
                    'next_tier': 'specialist',
                    'next_cost': 1000,
                    'next_multiplier': 1.5,
                    'improvement': '+25% extra (1.2 → 1.5)'
                },
                'specialist': {
                    'current_multiplier': 1.5,
                    'next_tier': 'operator',
                    'next_cost': 3500,
                    'next_multiplier': 1.8,
                    'improvement': '+20% extra (1.5 → 1.8)'
                }
            }

            prog = tier_progression.get(current_tier, {})

            # Project monthly earnings
            avg_deal_size = 7500  # Conservative estimate
            avg_deals_per_month = 4  # Conservative
            avg_deal_value = avg_deal_size * avg_deals_per_month

            # Current annual projection
            current_annual = avg_deal_value * 12 * (0.03 * prog.get('current_multiplier', 1.0) + current_boost)

            # If upgraded
            next_multiplier = prog.get('next_multiplier', 1.0)
            upgraded_annual = avg_deal_value * 12 * (0.03 * next_multiplier)

            return {
                'agent_id': agent_id,
                'total_earned': total_earned,
                'current_tier': current_tier,
                'current_commission_boost': current_boost,
                'current_multiplier': prog.get('current_multiplier', 1.0),
                'current_annual_projection': current_annual,
                'next_tier': prog.get('next_tier'),
                'next_tier_cost': prog.get('next_cost'),
                'next_multiplier': next_multiplier,
                'upgraded_annual_projection': upgraded_annual,
                'additional_annual_revenue': upgraded_annual - current_annual,
                'payback_period_days': (prog.get('next_cost', 0) / (upgraded_annual / 365)) if upgraded_annual > current_annual else 0,
                'recommendation': f"Invest ${prog.get('next_cost')} now, earn it back in {(prog.get('next_cost', 0) / (upgraded_annual / 365)):.0f} days"
            }

        except Exception as e:
            logger.error(f"Error getting earnings projections: {e}")
            return {'status': 'error', 'message': str(e)}


def initialize_commission_tables(db_path='data/sincor.db'):
    """Initialize commission tracking tables"""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Commission earnings log
    cursor.execute('''CREATE TABLE IF NOT EXISTS agent_commissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        deal_id TEXT,
        amount REAL NOT NULL,
        touch_type TEXT,
        calculated_at TEXT,
        FOREIGN KEY(agent_id) REFERENCES agent_tiers(agent_id)
    )''')

    # Agent balance tracking
    cursor.execute('''CREATE TABLE IF NOT EXISTS agent_balances (
        agent_id TEXT PRIMARY KEY,
        earned_total REAL DEFAULT 0.0,
        withdrawn_total REAL DEFAULT 0.0,
        pending_payout REAL DEFAULT 0.0,
        last_earning_at TEXT,
        last_payout_at TEXT
    )''')

    conn.commit()
    conn.close()
    logger.info("Commission tables initialized")


# Example Usage:
if __name__ == '__main__':
    engine = MarketplaceEnabledCommissionEngine()

    # Record a commission with marketplace boosts
    result = engine.record_commission(
        agent_id='agent-alice-001',
        deal_amount=12500,
        touch_type='outreach',
        deal_id='deal-12345'
    )
    print(f"Commission recorded: ${result['commission_amount']:.2f}")

    # Get earnings projection
    projection = engine.get_agent_earnings_with_projections('agent-alice-001')
    print(f"Current annual: ${projection['current_annual_projection']:.2f}")
    print(f"If upgraded: ${projection['upgraded_annual_projection']:.2f}")
    print(f"ROI: Payback in {projection['payback_period_days']:.0f} days")
