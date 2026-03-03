"""
SINCOR Sustainable Commission Model - Profitability Analysis & Guardrails

Problem: Protect company margin while incentivizing quality agent work
without diminishing returns or burnout-driven quality drops.
"""

import sqlite3
import logging
from datetime import datetime
from typing import Dict, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class CommissionSustainability:
    """
    Ensures commissions stay within profitable range while maintaining quality.

    Key Principle:
      Company needs 60%+ gross margin on deals after agent payouts
      Agents need enough upside that quality stays high (no rushing)
      Any deal structure should result in 15-25% agent payouts (before upgrades)
    """

    def __init__(self):
        self.db_path = 'data/commission_sustainability.db'
        self.init_db()

        # Key thresholds
        self.max_agent_payout_percent = 0.25  # Never pay more than 25% to agents per deal
        self.min_company_margin_percent = 0.60  # Company must keep 60%+
        self.ideal_quality_threshold = 85  # Quality score for bonus eligibility (0-100)
        self.diminishing_return_cap = 0.08  # Cap individual multipliers at 8x (so no infinite scaling)

    def init_db(self):
        """Initialize sustainability tracking"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        # Deal profitability tracking
        cursor.execute('''CREATE TABLE IF NOT EXISTS deal_profitability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE NOT NULL,
            deal_amount REAL NOT NULL,
            agent_payout_total REAL NOT NULL,
            agent_payout_percent REAL NOT NULL,
            company_margin_amount REAL NOT NULL,
            company_margin_percent REAL NOT NULL,
            quality_score REAL,
            is_sustainable INTEGER,
            recorded_at TEXT
        )''')

        # Agent performance tracking (quality vs earnings)
        cursor.execute('''CREATE TABLE IF NOT EXISTS agent_performance (
            agent_id TEXT PRIMARY KEY,
            total_deals INTEGER DEFAULT 0,
            avg_quality_score REAL DEFAULT 0.0,
            total_earned REAL DEFAULT 0.0,
            earned_per_deal REAL DEFAULT 0.0,
            quality_decline_rate REAL DEFAULT 0.0,
            burnout_risk_level TEXT DEFAULT 'healthy'
        )''')

        # Quality metrics (prevent rushing)
        cursor.execute('''CREATE TABLE IF NOT EXISTS quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            deal_id TEXT NOT NULL,
            proposal_quality REAL,
            close_consistency REAL,
            customer_satisfaction REAL,
            avg_quality REAL,
            recorded_at TEXT
        )''')

        conn.commit()
        conn.close()
        logger.info("Sustainability database initialized")

    # ========================================================================
    # COMMISSION CALCULATION WITH SUSTAINABILITY CHECKS
    # ========================================================================

    def calculate_sustainable_commission(self, agent_id: str, deal_amount: float,
                                        touch_type: str, quality_score: float = None) -> Tuple[float, Dict]:
        """
        Calculate commission with sustainability guardrails:
        1. Never let total payouts exceed 25% of deal
        2. Ensure company margin stays >60%
        3. Apply quality multipliers (reward good work, not just volume)
        4. Cap multipliers to prevent runaway payouts
        """

        base_rates = {
            'scout': 0.03,
            'qualifier': 0.02,
            'proposer': 0.02,
            'closing': 0.10
        }

        base_rate = base_rates.get(touch_type, 0.03)

        try:
            # Get agent tier and boosts
            conn = sqlite3.connect('data/agent_marketplace.db', timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''SELECT current_tier, commission_boost
                            FROM agent_tiers WHERE agent_id = ?''', (agent_id,))
            row = cursor.fetchone()
            conn.close()

            tier_multipliers = {
                'recruit': 1.0,
                'scout': 1.2,
                'specialist': 1.5,
                'operator': 1.8,
                'leader': 2.0,
                'executive': 2.5
            }

            if row:
                tier = row[0]
                multiplier = min(tier_multipliers.get(tier, 1.0), self.diminishing_return_cap)
                boost = row[1]
            else:
                tier = 'recruit'
                multiplier = 1.0
                boost = 0.0

            # Base commission with tier
            base_with_tier = base_rate * multiplier

            # QUALITY MULTIPLIER (Key: reward good work, not rushing)
            quality_multi = 1.0
            if quality_score is not None:
                if quality_score >= self.ideal_quality_threshold:
                    # Excellent quality: +10% commission bonus
                    quality_multi = 1.10
                elif quality_score >= 75:
                    # Good quality: +5% bonus
                    quality_multi = 1.05
                elif quality_score < 60:
                    # Poor quality: -10% penalty (prevent rushing)
                    quality_multi = 0.90

            # Calculate final with all factors
            final_rate = (base_with_tier + boost) * quality_multi

            # CRITICAL: Apply sustainability cap
            # Total individual agent take should never exceed 15% per deal
            # (other agents will take 5-10%, so total stays under 25%)
            max_individual_rate = 0.15

            if final_rate > max_individual_rate:
                final_rate = max_individual_rate
                capped = True
            else:
                capped = False

            commission = deal_amount * final_rate

            # Calculate company margin
            deal_margin_before_agents = 0.85  # Assume 15% cost-to-deliver
            company_margin = deal_margin_before_agents - final_rate

            return commission, {
                'agent_id': agent_id,
                'deal_amount': deal_amount,
                'base_rate': base_rate,
                'tier': tier,
                'tier_multiplier': multiplier,
                'boost': boost,
                'quality_score': quality_score,
                'quality_multiplier': quality_multi,
                'final_rate': round(final_rate, 4),
                'commission_amount': round(commission, 2),
                'company_margin_percent': round(company_margin * 100, 1),
                'is_sustainable': company_margin >= 0.60,
                'was_capped': capped,
                'reason_capped': 'Max 15% per agent' if capped else None
            }

        except Exception as e:
            logger.error(f"Error calculating sustainable commission: {e}")
            return deal_amount * base_rate, {'error': str(e)}

    # ========================================================================
    # QUALITY TRACKING (Prevents rushing & burnout)
    # ========================================================================

    def record_quality_score(self, agent_id: str, deal_id: str,
                            proposal_quality: float, close_consistency: float,
                            customer_satisfaction: float) -> Dict:
        """
        Track quality metrics. Commission bonuses only apply if quality stays high.

        proposal_quality: 0-100 (does proposal match prospect needs?)
        close_consistency: 0-100 (is agent closing similar deal types well?)
        customer_satisfaction: 0-100 (post-deal satisfaction)
        """

        try:
            avg_quality = (proposal_quality + close_consistency + customer_satisfaction) / 3

            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO quality_metrics
                (agent_id, deal_id, proposal_quality, close_consistency,
                 customer_satisfaction, avg_quality, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (agent_id, deal_id, proposal_quality, close_consistency,
                 customer_satisfaction, avg_quality, datetime.utcnow().isoformat()))

            # Update agent performance
            cursor.execute('''
                SELECT total_deals, avg_quality_score FROM agent_performance
                WHERE agent_id = ?
            ''', (agent_id,))

            row = cursor.fetchone()
            if row:
                total_deals = row[0] + 1
                old_avg = row[1] or 0
                new_avg = ((old_avg * row[0]) + avg_quality) / total_deals

                cursor.execute('''
                    UPDATE agent_performance
                    SET total_deals = ?, avg_quality_score = ?
                    WHERE agent_id = ?
                ''', (total_deals, new_avg, agent_id))
            else:
                cursor.execute('''
                    INSERT INTO agent_performance (agent_id, total_deals, avg_quality_score, avg_quality_score)
                    VALUES (?, 1, ?, ?)
                ''', (agent_id, avg_quality, avg_quality))

            conn.commit()
            conn.close()

            return {
                'status': 'recorded',
                'agent_id': agent_id,
                'avg_quality': round(avg_quality, 1),
                'eligible_for_bonus': avg_quality >= self.ideal_quality_threshold,
                'quality_warning': 'Quality below 60% - no bonus eligible' if avg_quality < 60 else None
            }

        except Exception as e:
            logger.error(f"Error recording quality: {e}")
            return {'status': 'error', 'message': str(e)}

    # ========================================================================
    # DIMINISHING RETURNS ANALYSIS (Show agent the cap)
    # ========================================================================

    def analyze_agent_earnings_cap(self, agent_id: str) -> Dict:
        """
        Show agent: "Here's the math on why upgrades stop paying after tier X"

        This is important for transparency. Agents need to understand
        that unlimited upgrades DON'T mean unlimited earnings.
        """

        try:
            conn = sqlite3.connect('data/agent_marketplace.db', timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''SELECT current_tier, commission_boost
                            FROM agent_tiers WHERE agent_id = ?''', (agent_id,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                return {'status': 'error', 'message': 'Agent not found'}

            current_tier = row[0]
            current_boost = row[1]

            # Simulate earnings at different tier levels
            deal_amount = 12500  # Standard deal size for examples
            touch_type = 'scout'
            base_rate = 0.03

            tier_progression = {
                'recruit': {
                    'multiplier': 1.0,
                    'cost': 0,
                    'time_to_afford': 0,
                    'example_commission': deal_amount * 0.03,
                    'annual_potential': deal_amount * 0.03 * 48
                },
                'scout': {
                    'multiplier': 1.2,
                    'cost': 250,
                    'time_to_afford': '4-5 deals',
                    'example_commission': deal_amount * (0.03 * 1.2),
                    'annual_potential': deal_amount * (0.03 * 1.2) * 48
                },
                'specialist': {
                    'multiplier': 1.5,
                    'cost': 1000,
                    'time_to_afford': '8-10 deals at scout rate',
                    'example_commission': deal_amount * (0.03 * 1.5),
                    'annual_potential': deal_amount * (0.03 * 1.5) * 48
                },
                'operator': {
                    'multiplier': 1.8,
                    'cost': 3500,
                    'time_to_afford': '20+ deals',
                    'example_commission': deal_amount * (0.03 * 1.8),
                    'annual_potential': deal_amount * (0.03 * 1.8) * 48
                },
                'leader': {
                    'multiplier': 2.0,
                    'cost': 10000,
                    'time_to_afford': '50+ deals (or build team)',
                    'example_commission': min(deal_amount * (0.03 * 2.0), deal_amount * 0.15),  # Capped at 15%
                    'annual_potential': min(deal_amount * (0.03 * 2.0) * 48, deal_amount * 0.15 * 48)
                },
                'executive': {
                    'multiplier': 2.5,
                    'cost': 25000,
                    'time_to_afford': 'With team/equity',
                    'example_commission': min(deal_amount * (0.03 * 2.5), deal_amount * 0.15),  # Capped at 15%
                    'annual_potential': min(deal_amount * (0.03 * 2.5) * 48, deal_amount * 0.15 * 48)
                }
            }

            # Show current and next tier
            current_info = tier_progression.get(current_tier, {})

            next_tiers = {
                'recruit': 'scout',
                'scout': 'specialist',
                'specialist': 'operator',
                'operator': 'leader',
                'leader': 'executive',
                'executive': None
            }

            next_tier = next_tiers.get(current_tier)
            next_info = tier_progression.get(next_tier, {}) if next_tier else None

            return {
                'agent_id': agent_id,
                'current_tier': current_tier,
                'current_annual_potential': current_info.get('annual_potential', 0),
                'next_tier': next_tier,
                'next_upgrade_cost': next_info.get('cost', 0) if next_info else 0,
                'next_annual_potential': next_info.get('annual_potential', 0) if next_info else 0,
                'improvement': f"${next_info.get('annual_potential', 0) - current_info.get('annual_potential', 0):,.0f} more/year" if next_info else "You're at max tier",
                'roi_analysis': {
                    'cost': next_info.get('cost', 0) if next_info else 0,
                    'time_to_payback': f"{next_info.get('time_to_afford', 'N/A')} of dealmaking" if next_info else "N/A",
                    'marginal_return': "Diminishing returns kick in after SPECIALIST. Consider team building instead."
                },
                'sustainability_note': f"All tiers capped at 15% individual take per deal + quality multiplier. Beyond SPECIALIST, focus on team/equity, not tier upgrades."
            }

        except Exception as e:
            logger.error(f"Error analyzing cap: {e}")
            return {'status': 'error', 'message': str(e)}

    # ========================================================================
    # BURNOUT DETECTION (Prevent agents from overworking)
    # ========================================================================

    def assess_burnout_risk(self, agent_id: str) -> Dict:
        """
        Monitor if agent is taking on too many deals too fast.
        Signs of burnout/rushing:
          - Deal volume increasing 50%+ month-over-month
          - Quality scores declining
          - Commission per deal staying flat (they're rushing = lower quality)
        """

        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT total_deals, avg_quality_score, earned_per_deal
                FROM agent_performance WHERE agent_id = ?
            ''', (agent_id,))

            row = cursor.fetchone()
            if not row:
                return {'status': 'no_data', 'burnout_risk': 'unknown'}

            total_deals = row[0]
            avg_quality = row[1] or 0
            earned_per_deal = row[2] or 0

            # Risk assessment
            risk_level = 'healthy'
            warnings = []

            if total_deals > 50 and avg_quality < 70:
                risk_level = 'high'
                warnings.append(f"Completed {total_deals} deals but quality score only {avg_quality:.0f}% - possible speed-over-quality issue")

            if total_deals > 100 and avg_quality < 75:
                risk_level = 'high'
                warnings.append("Extended volume (100+ deals) with moderate-low quality - burnout risk")

            if earned_per_deal > 5000 and total_deals > 30:
                # High earnings + high volume = potential overwork
                risk_level = 'medium'
                warnings.append("High earnings AND high volume - sustainable? Consider workload")

            # Update risk level in DB
            cursor.execute('''
                UPDATE agent_performance
                SET burnout_risk_level = ?
                WHERE agent_id = ?
            ''', (risk_level, agent_id))

            conn.commit()
            conn.close()

            return {
                'agent_id': agent_id,
                'total_deals': total_deals,
                'avg_quality': round(avg_quality, 1),
                'earned_per_deal': round(earned_per_deal, 2),
                'burnout_risk_level': risk_level,
                'warnings': warnings if warnings else ['No burnout warning - agent is healthy'],
                'action_items': [
                    'If high risk: encourage team building (get leverage)',
                    'If high risk: mentor lower agents to reduce their own load',
                    'If high risk: consider equity/passive income to reduce deal pressure'
                ]
            }

        except Exception as e:
            logger.error(f"Error assessing burnout: {e}")
            return {'status': 'error', 'message': str(e)}

    # ========================================================================
    # DEAL SUSTAINABILITY REPORT (Show company the margin)
    # ========================================================================

    def generate_profitability_report(self) -> Dict:
        """
        Show all deals and which ones are sustainable.

        Goal: Ensure company never loses money on a deal due to agent payouts.
        """

        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            # Get all recent deals
            cursor.execute('''
                SELECT deal_id, deal_amount, agent_payout_total, company_margin_percent,
                       is_sustainable, quality_score
                FROM deal_profitability
                ORDER BY recorded_at DESC LIMIT 100
            ''')

            deals = cursor.fetchall()
            conn.close()

            sustainable = sum(1 for d in deals if d[4])
            unsustainable = len(deals) - sustainable

            total_value = sum(d[1] for d in deals)
            total_paid_agents = sum(d[2] for d in deals)
            avg_margin = sum(d[3] for d in deals) / len(deals) if deals else 0

            return {
                'summary': {
                    'total_deals': len(deals),
                    'sustainable_deals': sustainable,
                    'unsustainable_deals': unsustainable,
                    'sustainability_rate': f"{(sustainable / len(deals) * 100):.1f}%" if deals else "N/A",
                    'total_deal_value': f"${total_value:,.2f}",
                    'total_agent_payouts': f"${total_paid_agents:,.2f}",
                    'avg_agent_payout_percent': f"{(total_paid_agents / total_value * 100):.1f}%",
                    'avg_company_margin': f"{avg_margin:.1f}%"
                },
                'concerns': [
                    f"WARNING: {unsustainable} deals below 60% margin" if unsustainable > 0 else "All deals profitable"
                ],
                'recommendations': [
                    'Reduce tier multipliers if >10% of deals dip below 60% margin',
                    'Tie commission upgrades to quality, not just volume',
                    'Consider capping team builder commissions',
                    'Review deals with quality_score <70 for possible clawback role'
                ]
            }

        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {'status': 'error', 'message': str(e)}


def analyze_payout_scenarios() -> Dict:
    """
    Show the diminishing returns points.

    This answers: "At what tier do upgrades stop making sense?"
    """

    deal_size = 12500
    base_scout_rate = 0.03

    scenarios = {
        'RECRUIT (No upgrades)': {
            'commission_per_deal': deal_size * 0.03,
            'annual_annual_48_deals': deal_size * 0.03 * 48,
            'cost': 0,
            'tier_multiplier': 1.0
        },
        'SCOUT (1.2x multiplier)': {
            'commission_per_deal': deal_size * (0.03 * 1.2),
            'annual_at_48_deals': deal_size * (0.03 * 1.2) * 48,
            'cost': 250,
            'tier_multiplier': 1.2,
            'payback_deals': 3
        },
        'SPECIALIST (1.5x multiplier)': {
            'commission_per_deal': deal_size * (0.03 * 1.5),
            'annual_at_48_deals': deal_size * (0.03 * 1.5) * 48,
            'cost': 1000,
            'tier_multiplier': 1.5,
            'payback_deals': 10
        },
        'OPERATOR (1.8x multiplier) [WITH TEAM PASSIVE]': {
            'active_income_48_deals': deal_size * (0.03 * 1.8) * 48,
            'passive_income_from_team': 2000,  # Conservative with 3 agents
            'total_potential': (deal_size * (0.03 * 1.8) * 48) + 2000,
            'cost': 3500,
            'tier_multiplier': 1.8,
            'payback_deals': 25,
            'note': 'Payback includes team building, not pure tier upgrade'
        },
        'LEADER (2.0x, but capped at 15% per deal)': {
            'commission_per_deal': min(deal_size * (0.03 * 2.0), deal_size * 0.15),
            'annual_at_48_deals': min(deal_size * (0.03 * 2.0) * 48, deal_size * 0.15 * 48),
            'cost': 10000,
            'tier_multiplier': 2.0,
            'note': 'Hits diminishing returns - multiplier capped at 15% max per deal',
            'payback_deals': 'Never (upgrade costs more than it provides)'
        }
    }

    return {
        'analysis': scenarios,
        'key_insight': 'SPECIALIST (1.5x multiplier) is the sweet spot. Beyond that, ROI drops significantly.',
        'recommendation': 'After SPECIALIST, agents should focus on: (1) team building, (2) quality/revenue share, not tier upgrades',
        'diminishing_return_point': 'SPECIALIST tier',
        'why': 'Tier multipliers are capped at 15% individual take per deal (to protect company margin). This makes higher tiers pay back slower than lower tiers.'
    }


if __name__ == '__main__':
    engine = CommissionSustainability()

    # Show scenarios
    print("Diminishing Returns Analysis:")
    print(analyze_payout_scenarios())
