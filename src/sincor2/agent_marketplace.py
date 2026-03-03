"""
SINCOR Agent Marketplace - Agent spending economy within the system

Agents earn crypto commissions from deals, then spend those earnings to:
1. Upgrade their tools & capabilities
2. Access higher-value leads
3. Build teams (recruit sub-agents)
4. Unlock revenue sharing opportunities
5. Increase commission rates

Creates a self-sustaining ecosystem where agents WANT to stay and grow.
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentTier(Enum):
    """Agent status levels in SINCOR"""
    RECRUIT = "recruit"        # 3% base commission, limited leads
    SCOUT = "scout"            # 5% commission, full lead access
    SPECIALIST = "specialist"  # 8% commission, partner leads
    OPERATOR = "operator"      # 10% commission, team building enabled
    LEADER = "leader"          # 15% commission, revenue share eligible
    EXECUTIVE = "executive"    # 20% commission, founding partner status


class MarketplaceItem(Enum):
    """Things agents can buy with earned crypto"""

    # Commission upgrades (permanent, stacking)
    COMMISSION_BOOST_1PCT = "commission_boost_1pct"      # +1% to all commissions
    COMMISSION_BOOST_2PCT = "commission_boost_2pct"      # +2% to all commissions

    # Lead access upgrades
    PREMIUM_LEADS = "premium_leads_monthly"              # $500/mo - top 10% deals
    WHALE_LEADS = "whale_leads_monthly"                  # $2000/mo - enterprise deals
    PARTNERSHIP_LEADS = "partnership_leads"              # $1000/mo - co-selling deals

    # Team building (one-time costs)
    RECRUIT_1_AGENT = "recruit_1_agent"                  # $500 - hire 1 sub-agent
    RECRUIT_3_AGENTS = "recruit_3_agents"                # $1200 - hire 3 sub-agents
    TEAM_MANAGEMENT = "team_management_suite"            # $2500 - full team tools

    # Tier upgrades (one-time costs)
    TIER_UPGRADE_SCOUT = "tier_upgrade_scout"            # $250 - get to Scout tier
    TIER_UPGRADE_SPECIALIST = "tier_upgrade_specialist"  # $1000 - get to Specialist
    TIER_UPGRADE_OPERATOR = "tier_upgrade_operator"      # $3500 - get to Operator

    # Revenue share eligibility
    DEAL_EQUITY_OPTION = "deal_equity_option"            # $5000 - get % of deals closed
    PARTNERSHIP_REVENUE_SHARE = "partnership_revenue"    # $7500 - get % of partnerships

    # AI/Automation tools
    AUTO_PROPOSAL_GENERATOR = "auto_proposal_generator"  # $750 - AI writes proposals
    OBJECTION_HANDLER = "objection_handler"              # $750 - AI handles objections
    LEAD_QUALIFIER_BOT = "lead_qualifier_bot"            # $750 - AI pre-qualifies leads
    FULL_AI_SUITE = "full_ai_suite"                      # $1500 - all 3 tools

    # Data & intelligence
    COMPETITOR_INTEL = "competitor_intel_access"        # $500/mo - comp analysis
    MARKET_SIGNALS = "market_signals_feed"              # $300/mo - real-time signals


class AgentMarketplace:
    """Handles agent spending, upgrades, and tier progression"""

    def __init__(self):
        self.db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'agent_marketplace.db')
        self.init_db()

        # Pricing for marketplace items (in USD equivalent)
        self.prices = {
            MarketplaceItem.COMMISSION_BOOST_1PCT: 500,
            MarketplaceItem.COMMISSION_BOOST_2PCT: 1200,
            MarketplaceItem.PREMIUM_LEADS: 500,
            MarketplaceItem.WHALE_LEADS: 2000,
            MarketplaceItem.PARTNERSHIP_LEADS: 1000,
            MarketplaceItem.RECRUIT_1_AGENT: 500,
            MarketplaceItem.RECRUIT_3_AGENTS: 1200,
            MarketplaceItem.TEAM_MANAGEMENT: 2500,
            MarketplaceItem.TIER_UPGRADE_SCOUT: 250,
            MarketplaceItem.TIER_UPGRADE_SPECIALIST: 1000,
            MarketplaceItem.TIER_UPGRADE_OPERATOR: 3500,
            MarketplaceItem.DEAL_EQUITY_OPTION: 5000,
            MarketplaceItem.PARTNERSHIP_REVENUE_SHARE: 7500,
            MarketplaceItem.AUTO_PROPOSAL_GENERATOR: 750,
            MarketplaceItem.OBJECTION_HANDLER: 750,
            MarketplaceItem.LEAD_QUALIFIER_BOT: 750,
            MarketplaceItem.FULL_AI_SUITE: 1500,
            MarketplaceItem.COMPETITOR_INTEL: 500,
            MarketplaceItem.MARKET_SIGNALS_FEED: 300,
        }

    def init_db(self):
        """Initialize marketplace database"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        cursor = conn.cursor()

        # Agent marketplace purchases
        cursor.execute('''CREATE TABLE IF NOT EXISTS agent_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            amount_crypto REAL NOT NULL,
            crypto_type TEXT DEFAULT 'USDC',
            purchased_at TEXT NOT NULL,
            expires_at TEXT,
            is_recurring INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        )''')

        # Agent tier progression
        cursor.execute('''CREATE TABLE IF NOT EXISTS agent_tiers (
            agent_id TEXT PRIMARY KEY,
            current_tier TEXT DEFAULT 'recruit',
            commission_boost REAL DEFAULT 0.0,
            hired_agents INTEGER DEFAULT 0,
            lifetime_earnings REAL DEFAULT 0.0,
            has_equity INTEGER DEFAULT 0,
            has_revenue_share INTEGER DEFAULT 0,
            upgraded_at TEXT
        )''')

        # Commission boost tracking
        cursor.execute('''CREATE TABLE IF NOT EXISTS commission_boosts (
            agent_id TEXT PRIMARY KEY,
            base_commission REAL NOT NULL,
            boost_percentage REAL DEFAULT 0.0,
            final_commission REAL NOT NULL,
            active_boosts TEXT,
            calculated_at TEXT
        )''')

        conn.commit()
        conn.close()
        logger.info(f"Agent marketplace database ready at {self.db_path}")

    # ========================================================================
    # PURCHASING & SPENDING
    # ========================================================================

    def purchase_item(self, agent_id: str, item: MarketplaceItem,
                     crypto_amount: float, crypto_type: str = 'USDC') -> Dict:
        """Agent purchases a marketplace item with crypto earnings"""

        try:
            price_usd = self.prices.get(item, 0)
            if price_usd <= 0:
                return {'status': 'error', 'message': 'Invalid marketplace item'}

            if crypto_amount < price_usd:
                return {
                    'status': 'error',
                    'message': f'Insufficient funds. Need ${price_usd}, have ${crypto_amount}'
                }

            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            # Determine expiration (recurring items expire, one-time don't)
            is_recurring = item in [
                MarketplaceItem.PREMIUM_LEADS,
                MarketplaceItem.WHALE_LEADS,
                MarketplaceItem.PARTNERSHIP_LEADS,
                MarketplaceItem.COMPETITOR_INTEL,
                MarketplaceItem.MARKET_SIGNALS_FEED
            ]

            expires_at = None
            if is_recurring:
                from datetime import timedelta
                expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()

            # Record purchase
            cursor.execute('''
                INSERT INTO agent_purchases
                (agent_id, item_type, amount_usd, amount_crypto, crypto_type, purchased_at, expires_at, is_recurring)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (agent_id, item.value, price_usd, crypto_amount, crypto_type,
                 datetime.utcnow().isoformat(), expires_at, 1 if is_recurring else 0))

            conn.commit()
            conn.close()

            # Process the upgrade/purchase
            return self._process_item_purchase(agent_id, item)

        except Exception as e:
            logger.error(f"Error purchasing item: {e}")
            return {'status': 'error', 'message': str(e)}

    def _process_item_purchase(self, agent_id: str, item: MarketplaceItem) -> Dict:
        """Apply the effects of a purchased item"""

        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            # Get current agent tier
            cursor.execute('SELECT current_tier, commission_boost FROM agent_tiers WHERE agent_id = ?', (agent_id,))
            row = cursor.fetchone()
            current_tier = row[0] if row else AgentTier.RECRUIT.value
            current_boost = row[1] if row else 0.0

            # Apply item effects
            if item == MarketplaceItem.COMMISSION_BOOST_1PCT:
                new_boost = current_boost + 0.01
                cursor.execute('UPDATE agent_tiers SET commission_boost = ? WHERE agent_id = ?',
                             (new_boost, agent_id))
                result = f"Commission increased by 1% to {(5 + new_boost*100):.1f}% base"

            elif item == MarketplaceItem.COMMISSION_BOOST_2PCT:
                new_boost = current_boost + 0.02
                cursor.execute('UPDATE agent_tiers SET commission_boost = ? WHERE agent_id = ?',
                             (new_boost, agent_id))
                result = f"Commission increased by 2% to {(5 + new_boost*100):.1f}% base"

            elif item == MarketplaceItem.TIER_UPGRADE_SCOUT:
                cursor.execute('UPDATE agent_tiers SET current_tier = ? WHERE agent_id = ?',
                             (AgentTier.SCOUT.value, agent_id))
                result = "Upgraded to SCOUT tier - 5% base commission + lead access"

            elif item == MarketplaceItem.TIER_UPGRADE_SPECIALIST:
                cursor.execute('UPDATE agent_tiers SET current_tier = ? WHERE agent_id = ?',
                             (AgentTier.SPECIALIST.value, agent_id))
                result = "Upgraded to SPECIALIST tier - 8% commission + partner leads"

            elif item == MarketplaceItem.TIER_UPGRADE_OPERATOR:
                cursor.execute('UPDATE agent_tiers SET current_tier = ? WHERE agent_id = ?',
                             (AgentTier.OPERATOR.value, agent_id))
                result = "Upgraded to OPERATOR tier - 10% commission + team building enabled"

            elif item == MarketplaceItem.DEAL_EQUITY_OPTION:
                cursor.execute('UPDATE agent_tiers SET has_equity = 1 WHERE agent_id = ?', (agent_id,))
                result = "Equity option unlocked - earn % of deals closed by your network"

            elif item == MarketplaceItem.PARTNERSHIP_REVENUE_SHARE:
                cursor.execute('UPDATE agent_tiers SET has_revenue_share = 1 WHERE agent_id = ?', (agent_id,))
                result = "Revenue share unlocked - earn % of partnership deals"

            else:
                result = f"Item {item.value} purchased"

            conn.commit()
            conn.close()

            return {
                'status': 'success',
                'message': result,
                'item': item.value,
                'agent_id': agent_id
            }

        except Exception as e:
            logger.error(f"Error processing item purchase: {e}")
            return {'status': 'error', 'message': str(e)}

    # ========================================================================
    # COMMISSION CALCULATION WITH BOOSTS
    # ========================================================================

    def calculate_agent_commission(self, agent_id: str, deal_amount: float,
                                  commission_type: str) -> Tuple[float, Dict]:
        """
        Calculate commission including tier boosts and multipliers

        commission_type: 'scout' (3%), 'qualifier' (2%), 'proposer' (2%), 'closer' (10%)
        """

        base_rates = {
            'scout': 0.03,
            'qualifier': 0.02,
            'proposer': 0.02,
            'closer': 0.10
        }

        base_rate = base_rates.get(commission_type, 0.03)

        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            # Get agent's tier and boosts
            cursor.execute('SELECT current_tier, commission_boost FROM agent_tiers WHERE agent_id = ?',
                          (agent_id,))
            row = cursor.fetchone()

            if not row:
                # First time - create entry
                cursor.execute('''INSERT INTO agent_tiers (agent_id, current_tier, commission_boost)
                               VALUES (?, ?, ?)''', (agent_id, AgentTier.RECRUIT.value, 0.0))
                conn.commit()
                tier = AgentTier.RECRUIT.value
                boost = 0.0
            else:
                tier = row[0]
                boost = row[1]

            # Tier multipliers
            tier_multipliers = {
                AgentTier.RECRUIT.value: 1.0,
                AgentTier.SCOUT.value: 1.2,
                AgentTier.SPECIALIST.value: 1.5,
                AgentTier.OPERATOR.value: 1.8,
                AgentTier.LEADER.value: 2.0,
                AgentTier.EXECUTIVE.value: 2.5
            }

            multiplier = tier_multipliers.get(tier, 1.0)

            # Final commission = base_rate * multiplier + boost
            final_rate = (base_rate * multiplier) + boost
            commission = deal_amount * final_rate

            conn.close()

            return commission, {
                'agent_id': agent_id,
                'tier': tier,
                'base_rate': base_rate,
                'multiplier': multiplier,
                'boost': boost,
                'final_rate': final_rate,
                'deal_amount': deal_amount,
                'commission': commission
            }

        except Exception as e:
            logger.error(f"Error calculating commission: {e}")
            return deal_amount * base_rate, {'error': str(e)}

    # ========================================================================
    # AGENT PROGRESSION
    # ========================================================================

    def get_agent_stats(self, agent_id: str) -> Dict:
        """Get complete agent profile including earnings, tier, upgrades"""

        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            cursor = conn.cursor()

            # Tier info
            cursor.execute('SELECT * FROM agent_tiers WHERE agent_id = ?', (agent_id,))
            tier_row = cursor.fetchone()

            if not tier_row:
                return {'status': 'error', 'message': 'Agent not found'}

            # Count purchases
            cursor.execute('SELECT COUNT(*) FROM agent_purchases WHERE agent_id = ? AND status = ?',
                          (agent_id, 'active'))
            upgrades_count = cursor.fetchone()[0]

            # Total earnings
            cursor.execute('SELECT SUM(amount_usd) FROM agent_purchases WHERE agent_id = ?', (agent_id,))
            total_spent = cursor.fetchone()[0] or 0.0

            conn.close()

            return {
                'agent_id': agent_id,
                'current_tier': tier_row[1],
                'commission_boost': tier_row[2],
                'lifetime_earnings': tier_row[3],
                'hired_agents': tier_row[4],
                'has_equity': bool(tier_row[5]),
                'has_revenue_share': bool(tier_row[6]),
                'upgrades_purchased': upgrades_count,
                'total_spent_usd': total_spent,
                'progression': self._get_progression_path(tier_row[1])
            }

        except Exception as e:
            logger.error(f"Error getting agent stats: {e}")
            return {'status': 'error', 'message': str(e)}

    def _get_progression_path(self, current_tier: str) -> Dict:
        """Show next upgrades available"""

        tier_paths = {
            AgentTier.RECRUIT.value: {
                'next': AgentTier.SCOUT.value,
                'cost': 250,
                'benefits': 'Base 5% commission, full lead access'
            },
            AgentTier.SCOUT.value: {
                'next': AgentTier.SPECIALIST.value,
                'cost': 1000,
                'benefits': 'Base 8% commission, partner leads'
            },
            AgentTier.SPECIALIST.value: {
                'next': AgentTier.OPERATOR.value,
                'cost': 3500,
                'benefits': 'Base 10% commission, team building'
            }
        }

        return tier_paths.get(current_tier, {
            'next': 'max_tier',
            'benefits': 'You are at maximum tier!'
        })

    def list_marketplace(self) -> Dict:
        """Get all marketplace items organized by category"""

        return {
            'commission_upgrades': [
                {
                    'item': MarketplaceItem.COMMISSION_BOOST_1PCT.value,
                    'price': self.prices[MarketplaceItem.COMMISSION_BOOST_1PCT],
                    'description': 'Permanently increase your commission by 1%',
                    'recurring': False
                },
                {
                    'item': MarketplaceItem.COMMISSION_BOOST_2PCT.value,
                    'price': self.prices[MarketplaceItem.COMMISSION_BOOST_2PCT],
                    'description': 'Permanently increase your commission by 2%',
                    'recurring': False
                }
            ],
            'tier_upgrades': [
                {
                    'item': MarketplaceItem.TIER_UPGRADE_SCOUT.value,
                    'price': self.prices[MarketplaceItem.TIER_UPGRADE_SCOUT],
                    'tier': 'SCOUT',
                    'benefits': 'Base 5% commission + full lead access'
                },
                {
                    'item': MarketplaceItem.TIER_UPGRADE_SPECIALIST.value,
                    'price': self.prices[MarketplaceItem.TIER_UPGRADE_SPECIALIST],
                    'tier': 'SPECIALIST',
                    'benefits': 'Base 8% commission + partner leads'
                },
                {
                    'item': MarketplaceItem.TIER_UPGRADE_OPERATOR.value,
                    'price': self.prices[MarketplaceItem.TIER_UPGRADE_OPERATOR],
                    'tier': 'OPERATOR',
                    'benefits': 'Base 10% commission + team building'
                }
            ],
            'lead_access': [
                {
                    'item': MarketplaceItem.PREMIUM_LEADS.value,
                    'price': self.prices[MarketplaceItem.PREMIUM_LEADS],
                    'monthly': True,
                    'description': 'Top 10% qualified leads, high-value prospects'
                },
                {
                    'item': MarketplaceItem.WHALE_LEADS.value,
                    'price': self.prices[MarketplaceItem.WHALE_LEADS],
                    'monthly': True,
                    'description': 'Enterprise deals, $50K+ contract value'
                }
            ],
            'revenue_share': [
                {
                    'item': MarketplaceItem.DEAL_EQUITY_OPTION.value,
                    'price': self.prices[MarketplaceItem.DEAL_EQUITY_OPTION],
                    'description': 'Earn % of deals closed by agents you hire'
                },
                {
                    'item': MarketplaceItem.PARTNERSHIP_REVENUE_SHARE.value,
                    'price': self.prices[MarketplaceItem.PARTNERSHIP_REVENUE_SHARE],
                    'description': 'Earn % of partnership revenue'
                }
            ],
            'automation_tools': [
                {
                    'item': MarketplaceItem.FULL_AI_SUITE.value,
                    'price': self.prices[MarketplaceItem.FULL_AI_SUITE],
                    'includes': ['Auto Proposals', 'Objection Handler', 'Lead Qualifier']
                }
            ]
        }
