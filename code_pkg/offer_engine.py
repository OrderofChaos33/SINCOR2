"""
Offer Engine v1 - Day 4 Hardening
Autonomous offer testing and optimization - kills losers, scales winners
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import random

logger = logging.getLogger(__name__)

class OfferType(Enum):
    DISCOUNT_PERCENTAGE = "discount_percentage"
    DISCOUNT_FIXED = "discount_fixed"
    BOGO = "buy_one_get_one"
    PACKAGE_DEAL = "package_deal"
    SEASONAL = "seasonal"
    FIRST_TIME = "first_time_customer"

class OfferStatus(Enum):
    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"
    KILLED = "killed"
    WINNER = "winner"

class OfferChannel(Enum):
    FACEBOOK_ADS = "facebook_ads"
    GOOGLE_ADS = "google_ads"
    EMAIL = "email"
    SMS = "sms"
    WEBSITE = "website"
    INSTAGRAM = "instagram"

@dataclass
class Offer:
    id: str
    name: str
    type: OfferType
    description: str
    discount_value: float  # Percentage or dollar amount
    original_price: float
    discounted_price: float
    channels: List[OfferChannel]
    target_audience: Dict[str, Any]
    status: OfferStatus
    created_at: datetime
    expires_at: Optional[datetime] = None
    max_redemptions: Optional[int] = None
    current_redemptions: int = 0

@dataclass 
class OfferPerformance:
    offer_id: str
    channel: OfferChannel
    impressions: int
    clicks: int
    conversions: int
    revenue: float
    cost: float
    ctr: float  # Click-through rate
    cvr: float  # Conversion rate
    cac: float  # Customer acquisition cost
    roas: float  # Return on ad spend
    date: datetime

@dataclass
class OfferDecision:
    offer_id: str
    action: str  # kill, scale, modify, maintain
    reason: str
    confidence: float
    expected_impact: Dict[str, float]
    new_parameters: Dict[str, Any] = None

class OfferEngine:
    def __init__(self):
        self.db_path = "clinton_auto_detailing_offers.db"
        
        # Clinton Auto Detailing specific offer settings
        self.business_name = "Clinton Auto Detailing"
        self.services = {
            'basic_wash': {'name': 'Basic Wash & Vacuum', 'price': 35.0},
            'full_detail': {'name': 'Full Interior/Exterior Detail', 'price': 125.0},
            'premium_detail': {'name': 'Premium Detail Package', 'price': 185.0},
            'ceramic_coating': {'name': 'Ceramic Coating Protection', 'price': 450.0},
            'headlight_restoration': {'name': 'Headlight Restoration', 'price': 75.0},
            'engine_bay': {'name': 'Engine Bay Cleaning', 'price': 65.0}
        }
        
        # Performance thresholds
        self.kill_threshold_cac = 150.0  # Kill if CAC > $150
        self.kill_threshold_roas = 1.2   # Kill if ROAS < 1.2x
        self.scale_threshold_roas = 2.0  # Scale if ROAS > 2.0x
        self.min_conversions_for_decision = 5  # Need at least 5 conversions to make decisions
        
        self.init_database()
        self.seed_initial_offers()
        
    def init_database(self):
        """Initialize offer engine database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Offers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS offers (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                description TEXT,
                discount_value REAL,
                original_price REAL,
                discounted_price REAL,
                channels TEXT,
                target_audience TEXT,
                status TEXT,
                max_redemptions INTEGER,
                current_redemptions INTEGER DEFAULT 0,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(status),
                INDEX(expires_at)
            )
        ''')
        
        # Offer performance tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS offer_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id TEXT,
                channel TEXT,
                date DATE,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                conversions INTEGER DEFAULT 0,
                revenue REAL DEFAULT 0,
                cost REAL DEFAULT 0,
                ctr REAL DEFAULT 0,
                cvr REAL DEFAULT 0,
                cac REAL DEFAULT 0,
                roas REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers (id),
                INDEX(offer_id, channel, date)
            )
        ''')
        
        # Offer decisions log
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS offer_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id TEXT,
                action TEXT,
                reason TEXT,
                confidence REAL,
                expected_impact TEXT,
                new_parameters TEXT,
                implemented BOOLEAN DEFAULT 0,
                implemented_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (offer_id) REFERENCES offers (id),
                INDEX(offer_id, action, created_at)
            )
        ''')
        
        # A/B test results
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS offer_ab_tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name TEXT,
                offer_a_id TEXT,
                offer_b_id TEXT,
                traffic_split REAL DEFAULT 0.5,
                start_date DATE,
                end_date DATE,
                winner_id TEXT,
                confidence_level REAL,
                lift_percentage REAL,
                test_status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(test_name, test_status)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("Offer engine database initialized")
    
    def seed_initial_offers(self):
        """Seed initial offers for Clinton Auto Detailing"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if offers already exist
        cursor.execute('SELECT COUNT(*) FROM offers')
        if cursor.fetchone()[0] > 0:
            conn.close()
            return
        
        # Initial offer templates for auto detailing business
        initial_offers = [
            {
                'name': 'First-Time Customer Special',
                'type': OfferType.DISCOUNT_PERCENTAGE,
                'description': '20% off your first detail service - new customers only',
                'discount_value': 20.0,
                'original_price': 125.0,
                'discounted_price': 100.0,
                'channels': [OfferChannel.FACEBOOK_ADS, OfferChannel.GOOGLE_ADS, OfferChannel.WEBSITE],
                'target_audience': {'customer_type': 'new', 'vehicle_age': 'any', 'location': 'Clinton_IA_10mile_MAX'},
                'max_redemptions': 50
            },
            {
                'name': 'Full Detail + Ceramic Package',
                'type': OfferType.PACKAGE_DEAL,
                'description': 'Full Detail + Ceramic Coating - Save $75 when bundled',
                'discount_value': 75.0,
                'original_price': 570.0,  # 125 + 450 - 75 savings
                'discounted_price': 495.0,
                'channels': [OfferChannel.EMAIL, OfferChannel.FACEBOOK_ADS],
                'target_audience': {'customer_type': 'existing', 'last_service': '90_days', 'service_value': 'high'},
                'max_redemptions': 25
            },
            {
                'name': 'Spring Clean Special',
                'type': OfferType.SEASONAL,
                'description': 'Spring into action - $25 off any detail service',
                'discount_value': 25.0,
                'original_price': 125.0,
                'discounted_price': 100.0,
                'channels': [OfferChannel.SMS, OfferChannel.EMAIL, OfferChannel.INSTAGRAM],
                'target_audience': {'season': 'spring', 'location': 'Clinton_MS_15mile'},
                'max_redemptions': 100,
                'expires_at': datetime.now() + timedelta(days=30)
            }
        ]
        
        for offer_data in initial_offers:
            offer = Offer(
                id=str(uuid.uuid4()),
                name=offer_data['name'],
                type=offer_data['type'],
                description=offer_data['description'],
                discount_value=offer_data['discount_value'],
                original_price=offer_data['original_price'],
                discounted_price=offer_data['discounted_price'],
                channels=offer_data['channels'],
                target_audience=offer_data['target_audience'],
                status=OfferStatus.LIVE,
                created_at=datetime.now(),
                expires_at=offer_data.get('expires_at'),
                max_redemptions=offer_data.get('max_redemptions'),
                current_redemptions=0
            )
            
            self._store_offer(offer)
        
        conn.close()
        logger.info(f"Seeded {len(initial_offers)} initial offers")
    
    def _store_offer(self, offer: Offer):
        """Store offer in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO offers 
            (id, name, type, description, discount_value, original_price, discounted_price,
             channels, target_audience, status, max_redemptions, current_redemptions, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            offer.id,
            offer.name,
            offer.type.value,
            offer.description,
            offer.discount_value,
            offer.original_price,
            offer.discounted_price,
            json.dumps([c.value for c in offer.channels]),
            json.dumps(offer.target_audience),
            offer.status.value,
            offer.max_redemptions,
            offer.current_redemptions,
            offer.expires_at
        ))
        
        conn.commit()
        conn.close()
    
    def collect_offer_performance(self, days_back: int = 7) -> List[OfferPerformance]:
        """Collect performance data for all active offers"""
        performances = []
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get live offers
        cursor.execute('''
            SELECT id, name FROM offers 
            WHERE status = 'live'
        ''')
        live_offers = cursor.fetchall()
        
        for offer_id, offer_name in live_offers:
            # Simulate performance data collection from various sources
            for channel in [OfferChannel.FACEBOOK_ADS, OfferChannel.GOOGLE_ADS, OfferChannel.EMAIL]:
                performance = self._collect_channel_performance(offer_id, channel, days_back)
                if performance:
                    performances.append(performance)
                    self._store_offer_performance(performance)
        
        conn.close()
        return performances
    
    def _collect_channel_performance(self, offer_id: str, channel: OfferChannel, 
                                   days_back: int) -> Optional[OfferPerformance]:
        """Collect performance data for specific offer and channel"""
        try:
            # This would integrate with actual ad platforms, email systems, etc.
            # For now, simulate realistic data for Clinton Auto Detailing
            
            # Simulate performance based on channel effectiveness
            channel_multipliers = {
                OfferChannel.FACEBOOK_ADS: {'impressions': 1000, 'ctr': 0.025, 'cvr': 0.08},
                OfferChannel.GOOGLE_ADS: {'impressions': 750, 'ctr': 0.035, 'cvr': 0.12},
                OfferChannel.EMAIL: {'impressions': 500, 'ctr': 0.15, 'cvr': 0.06}
            }
            
            if channel not in channel_multipliers:
                return None
            
            multiplier = channel_multipliers[channel]
            
            # Add some randomness to simulate real performance variance
            impressions = int(multiplier['impressions'] * days_back * random.uniform(0.7, 1.3))
            ctr = multiplier['ctr'] * random.uniform(0.8, 1.2)
            cvr = multiplier['cvr'] * random.uniform(0.6, 1.4)
            
            clicks = int(impressions * ctr)
            conversions = int(clicks * cvr)
            
            # Estimate revenue and costs
            avg_order_value = random.uniform(75, 150)
            revenue = conversions * avg_order_value
            
            if channel == OfferChannel.FACEBOOK_ADS:
                cost = impressions * 0.02  # $20 CPM
            elif channel == OfferChannel.GOOGLE_ADS:
                cost = clicks * 2.5  # $2.50 CPC
            else:  # Email
                cost = impressions * 0.001  # Very low cost
            
            cac = cost / max(conversions, 1)
            roas = revenue / max(cost, 1)
            
            return OfferPerformance(
                offer_id=offer_id,
                channel=channel,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                revenue=revenue,
                cost=cost,
                ctr=ctr,
                cvr=cvr,
                cac=cac,
                roas=roas,
                date=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"Failed to collect performance for offer {offer_id} on {channel.value}: {e}")
            return None
    
    def _store_offer_performance(self, performance: OfferPerformance):
        """Store offer performance data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO offer_performance 
            (offer_id, channel, date, impressions, clicks, conversions, revenue, 
             cost, ctr, cvr, cac, roas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            performance.offer_id,
            performance.channel.value,
            performance.date.date(),
            performance.impressions,
            performance.clicks,
            performance.conversions,
            performance.revenue,
            performance.cost,
            performance.ctr,
            performance.cvr,
            performance.cac,
            performance.roas
        ))
        
        conn.commit()
        conn.close()
    
    def analyze_offer_performance(self, performances: List[OfferPerformance]) -> List[OfferDecision]:
        """Analyze offer performance and make decisions"""
        decisions = []
        
        # Group performances by offer
        offer_performances = {}
        for perf in performances:
            if perf.offer_id not in offer_performances:
                offer_performances[perf.offer_id] = []
            offer_performances[perf.offer_id].append(perf)
        
        for offer_id, perfs in offer_performances.items():
            # Aggregate performance across channels
            total_conversions = sum(p.conversions for p in perfs)
            total_revenue = sum(p.revenue for p in perfs)
            total_cost = sum(p.cost for p in perfs)
            
            if total_conversions < self.min_conversions_for_decision:
                # Not enough data to make decision
                decision = OfferDecision(
                    offer_id=offer_id,
                    action='monitor',
                    reason=f'Insufficient data ({total_conversions} conversions)',
                    confidence=0.3,
                    expected_impact={'status': 'monitoring'}
                )
                decisions.append(decision)
                continue
            
            avg_roas = total_revenue / max(total_cost, 1)
            avg_cac = total_cost / max(total_conversions, 1)
            
            # Decision logic
            if avg_roas < self.kill_threshold_roas or avg_cac > self.kill_threshold_cac:
                # Kill underperforming offer
                decision = OfferDecision(
                    offer_id=offer_id,
                    action='kill',
                    reason=f'Poor performance (ROAS: {avg_roas:.2f}, CAC: ${avg_cac:.2f})',
                    confidence=0.8,
                    expected_impact={'cost_savings': total_cost * 0.7},
                    new_parameters={'status': 'killed'}
                )
            
            elif avg_roas > self.scale_threshold_roas and avg_cac < 100:
                # Scale winning offer
                decision = OfferDecision(
                    offer_id=offer_id,
                    action='scale',
                    reason=f'High performance (ROAS: {avg_roas:.2f}, CAC: ${avg_cac:.2f})',
                    confidence=0.9,
                    expected_impact={'revenue_increase': total_revenue * 0.5},
                    new_parameters={'budget_multiplier': 1.5, 'max_redemptions': '+50'}
                )
            
            elif avg_roas >= 1.5:
                # Modify to optimize further
                decision = OfferDecision(
                    offer_id=offer_id,
                    action='modify',
                    reason=f'Good performance with optimization potential',
                    confidence=0.7,
                    expected_impact={'roas_improvement': 0.2},
                    new_parameters=self._suggest_modifications(offer_id, perfs)
                )
            
            else:
                # Maintain current offer
                decision = OfferDecision(
                    offer_id=offer_id,
                    action='maintain',
                    reason=f'Stable performance (ROAS: {avg_roas:.2f})',
                    confidence=0.6,
                    expected_impact={'status': 'maintained'}
                )
            
            decisions.append(decision)
            self._store_offer_decision(decision)
        
        return decisions
    
    def _suggest_modifications(self, offer_id: str, performances: List[OfferPerformance]) -> Dict[str, Any]:
        """Suggest modifications to improve offer performance"""
        modifications = {}
        
        # Analyze channel performance
        best_channel = max(performances, key=lambda p: p.roas)
        worst_channel = min(performances, key=lambda p: p.roas)
        
        if best_channel.roas / max(worst_channel.roas, 0.1) > 2:
            modifications['reallocate_budget'] = {
                'increase': best_channel.channel.value,
                'decrease': worst_channel.channel.value
            }
        
        # Suggest discount optimization
        avg_cvr = sum(p.cvr for p in performances) / len(performances)
        if avg_cvr < 0.05:  # Low conversion rate
            modifications['increase_discount'] = '5_percentage_points'
        elif avg_cvr > 0.15:  # High conversion rate
            modifications['decrease_discount'] = '2_percentage_points'
        
        return modifications
    
    def _store_offer_decision(self, decision: OfferDecision):
        """Store offer decision in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO offer_decisions 
            (offer_id, action, reason, confidence, expected_impact, new_parameters)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            decision.offer_id,
            decision.action,
            decision.reason,
            decision.confidence,
            json.dumps(decision.expected_impact),
            json.dumps(decision.new_parameters) if decision.new_parameters else None
        ))
        
        conn.commit()
        conn.close()
    
    def implement_offer_decisions(self, decisions: List[OfferDecision]) -> Dict[str, bool]:
        """Implement offer optimization decisions"""
        results = {}
        
        for decision in decisions:
            try:
                if decision.confidence < 0.6:
                    logger.info(f"Skipping {decision.offer_id} - low confidence ({decision.confidence:.2f})")
                    results[decision.offer_id] = False
                    continue
                
                success = False
                
                if decision.action == 'kill':
                    success = self._kill_offer(decision.offer_id)
                elif decision.action == 'scale':
                    success = self._scale_offer(decision.offer_id, decision.new_parameters)
                elif decision.action == 'modify':
                    success = self._modify_offer(decision.offer_id, decision.new_parameters)
                else:  # maintain or monitor
                    success = True  # No action needed
                
                results[decision.offer_id] = success
                
                if success and decision.action != 'maintain':
                    # Mark decision as implemented
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    
                    cursor.execute('''
                        UPDATE offer_decisions 
                        SET implemented = 1, implemented_at = ?
                        WHERE offer_id = ? AND implemented = 0
                        ORDER BY created_at DESC LIMIT 1
                    ''', (datetime.now(), decision.offer_id))
                    
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Implemented decision for offer {decision.offer_id}: {decision.action}")
                
            except Exception as e:
                logger.error(f"Failed to implement decision for offer {decision.offer_id}: {e}")
                results[decision.offer_id] = False
        
        return results
    
    def _kill_offer(self, offer_id: str) -> bool:
        """Kill underperforming offer"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE offers SET status = 'killed' WHERE id = ?
        ''', (offer_id,))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Killed offer {offer_id}")
        return True
    
    def _scale_offer(self, offer_id: str, parameters: Dict[str, Any]) -> bool:
        """Scale winning offer"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        updates = []
        values = []
        
        if 'max_redemptions' in parameters:
            if parameters['max_redemptions'].startswith('+'):
                # Increase by amount
                increase = int(parameters['max_redemptions'][1:])
                cursor.execute('SELECT max_redemptions FROM offers WHERE id = ?', (offer_id,))
                current = cursor.fetchone()[0] or 0
                updates.append('max_redemptions = ?')
                values.append(current + increase)
        
        if updates:
            query = f"UPDATE offers SET {', '.join(updates)} WHERE id = ?"
            values.append(offer_id)
            cursor.execute(query, values)
        
        # Mark as winner
        cursor.execute('UPDATE offers SET status = \'winner\' WHERE id = ?', (offer_id,))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Scaled offer {offer_id}")
        return True
    
    def _modify_offer(self, offer_id: str, parameters: Dict[str, Any]) -> bool:
        """Modify offer based on optimization suggestions"""
        # In production, this would update ad campaigns, email templates, etc.
        logger.info(f"Modified offer {offer_id} with parameters: {parameters}")
        return True
    
    def create_new_offer_variant(self, base_offer_id: str, modifications: Dict[str, Any]) -> Optional[str]:
        """Create new offer variant based on successful offer"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get base offer
            cursor.execute('SELECT * FROM offers WHERE id = ?', (base_offer_id,))
            base_data = cursor.fetchone()
            
            if not base_data:
                return None
            
            # Create variant
            new_offer = Offer(
                id=str(uuid.uuid4()),
                name=f"{base_data[1]} - Variant",
                type=OfferType(base_data[2]),
                description=modifications.get('description', base_data[3]),
                discount_value=modifications.get('discount_value', base_data[4]),
                original_price=base_data[5],
                discounted_price=base_data[5] * (1 - modifications.get('discount_value', base_data[4])/100),
                channels=[OfferChannel(c) for c in json.loads(base_data[6])],
                target_audience=json.loads(base_data[7]),
                status=OfferStatus.LIVE,
                created_at=datetime.now(),
                max_redemptions=modifications.get('max_redemptions', base_data[9])
            )
            
            self._store_offer(new_offer)
            conn.close()
            
            logger.info(f"Created new offer variant {new_offer.id} from {base_offer_id}")
            return new_offer.id
            
        except Exception as e:
            logger.error(f"Failed to create offer variant: {e}")
            return None
    
    def run_optimization_cycle(self) -> Dict[str, Any]:
        """Run complete offer optimization cycle"""
        logger.info("Starting offer optimization cycle")
        
        try:
            # Collect performance data
            performances = self.collect_offer_performance()
            
            # Analyze and make decisions
            decisions = self.analyze_offer_performance(performances)
            
            # Implement decisions
            implementation_results = self.implement_offer_decisions(decisions)
            
            # Create new variants from winners
            new_variants = []
            for decision in decisions:
                if decision.action == 'scale' and implementation_results.get(decision.offer_id):
                    # Create variant with slight modification
                    variant_id = self.create_new_offer_variant(
                        decision.offer_id,
                        {'discount_value': decision.new_parameters.get('discount_adjust', 0)}
                    )
                    if variant_id:
                        new_variants.append(variant_id)
            
            optimization_result = {
                'cycle_id': str(int(time.time())),
                'offers_analyzed': len(set(p.offer_id for p in performances)),
                'decisions_made': len(decisions),
                'decisions_implemented': sum(1 for r in implementation_results.values() if r),
                'new_variants_created': len(new_variants),
                'performance_summary': self._summarize_performance(performances),
                'decisions': [asdict(d) for d in decisions],
                'implementation_results': implementation_results,
                'new_variants': new_variants,
                'timestamp': datetime.now().isoformat()
            }
            
            logger.info(f"Offer optimization completed - {optimization_result['decisions_implemented']} decisions implemented")
            
            return optimization_result
            
        except Exception as e:
            logger.error(f"Offer optimization cycle failed: {e}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    def _summarize_performance(self, performances: List[OfferPerformance]) -> Dict[str, Any]:
        """Summarize overall performance metrics"""
        if not performances:
            return {}
        
        total_revenue = sum(p.revenue for p in performances)
        total_cost = sum(p.cost for p in performances)
        total_conversions = sum(p.conversions for p in performances)
        
        return {
            'total_revenue': total_revenue,
            'total_cost': total_cost,
            'total_conversions': total_conversions,
            'overall_roas': total_revenue / max(total_cost, 1),
            'average_cac': total_cost / max(total_conversions, 1),
            'best_performing_channel': max(performances, key=lambda p: p.roas).channel.value,
            'worst_performing_channel': min(performances, key=lambda p: p.roas).channel.value
        }

# Clinton Auto Detailing Offer Engine instance
clinton_offer_engine = OfferEngine()

def test_offer_engine():
    """Test the offer engine"""
    print("Testing Offer Engine for Clinton Auto Detailing...")
    
    # Run optimization cycle
    result = clinton_offer_engine.run_optimization_cycle()
    
    print(f"Optimization Result:")
    print(f"- Offers Analyzed: {result.get('offers_analyzed', 0)}")
    print(f"- Decisions Made: {result.get('decisions_made', 0)}")
    print(f"- Decisions Implemented: {result.get('decisions_implemented', 0)}")
    print(f"- New Variants Created: {result.get('new_variants_created', 0)}")
    
    if 'performance_summary' in result:
        perf = result['performance_summary']
        print(f"\\nPerformance Summary:")
        print(f"- Total Revenue: ${perf.get('total_revenue', 0):.2f}")
        print(f"- Overall ROAS: {perf.get('overall_roas', 0):.2f}x")
        print(f"- Average CAC: ${perf.get('average_cac', 0):.2f}")
    
    if 'decisions' in result:
        print(f"\\nDecisions Made:")
        for decision in result['decisions'][:3]:  # Show first 3
            print(f"- {decision['action'].title()}: {decision['reason']}")
    
    return result

if __name__ == "__main__":
    test_offer_engine()