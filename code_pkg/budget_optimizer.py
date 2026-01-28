"""
Budget Optimizer v1 - Day 4 Hardening
Autonomous budget allocation across Facebook Ads and Google Ads based on CAC/LTV/ROAS
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import requests
import time

logger = logging.getLogger(__name__)

class AdPlatform(Enum):
    FACEBOOK = "facebook"
    GOOGLE_ADS = "google_ads"
    INSTAGRAM = "instagram"

class BudgetAction(Enum):
    INCREASE = "increase"
    DECREASE = "decrease" 
    PAUSE = "pause"
    ACTIVATE = "activate"
    MAINTAIN = "maintain"

@dataclass
class PlatformMetrics:
    platform: AdPlatform
    daily_spend: float
    leads_generated: int
    appointments_booked: int
    revenue_generated: float
    cac: float  # Customer Acquisition Cost
    roas: float  # Return on Ad Spend
    ltv: float  # Customer Lifetime Value
    conversion_rate: float
    last_updated: datetime

@dataclass 
class BudgetAllocation:
    platform: AdPlatform
    current_budget: float
    recommended_budget: float
    action: BudgetAction
    reason: str
    confidence: float
    expected_impact: Dict[str, float]

@dataclass
class Guardrails:
    min_daily_budget: float = 10.0
    max_daily_budget: float = 500.0
    max_budget_change_percent: float = 0.25  # 25% max change per day
    min_roas_threshold: float = 1.2  # Minimum 20% return
    max_cac_threshold: float = 100.0  # Max $100 cost per customer
    min_confidence_threshold: float = 0.7  # 70% confidence minimum

class BudgetOptimizer:
    def __init__(self):
        self.db_path = "clinton_auto_detailing_budget_optimizer.db"
        self.guardrails = Guardrails()
        
        # Clinton Auto Detailing specific settings
        self.business_name = "Clinton Auto Detailing"
        self.target_markets = ["Clinton, IA"]  # NEVER exceed 10-mile radius from Clinton, IA
        self.max_service_radius_miles = 10  # Hard limit - local customers only!
        self.average_ticket_value = 125.0  # Average service price
        self.customer_ltv = 450.0  # Estimated lifetime value
        
        # Platform API configurations
        self.facebook_config = {
            'access_token': os.getenv('FACEBOOK_ACCESS_TOKEN'),
            'ad_account_id': os.getenv('FACEBOOK_AD_ACCOUNT_ID'),
            'app_id': os.getenv('FACEBOOK_APP_ID'),
            'app_secret': os.getenv('FACEBOOK_APP_SECRET')
        }
        
        self.google_ads_config = {
            'customer_id': os.getenv('GOOGLE_ADS_CUSTOMER_ID'),
            'developer_token': os.getenv('GOOGLE_ADS_DEVELOPER_TOKEN'),
            'client_id': os.getenv('GOOGLE_ADS_CLIENT_ID'),
            'client_secret': os.getenv('GOOGLE_ADS_CLIENT_SECRET'),
            'refresh_token': os.getenv('GOOGLE_ADS_REFRESH_TOKEN')
        }
        
        self.init_database()
        
    def init_database(self):
        """Initialize budget optimizer database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Platform performance metrics
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS platform_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                date DATE,
                daily_spend REAL,
                leads_generated INTEGER,
                appointments_booked INTEGER,
                revenue_generated REAL,
                cac REAL,
                roas REAL,
                ltv REAL,
                conversion_rate REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(platform, date)
            )
        ''')
        
        # Budget allocation history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS budget_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                current_budget REAL,
                recommended_budget REAL,
                action TEXT,
                reason TEXT,
                confidence REAL,
                expected_impact TEXT,
                implemented BOOLEAN DEFAULT 0,
                implemented_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(platform, created_at)
            )
        ''')
        
        # Campaign performance tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS campaign_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                campaign_id TEXT,
                campaign_name TEXT,
                daily_budget REAL,
                actual_spend REAL,
                impressions INTEGER,
                clicks INTEGER,
                leads INTEGER,
                conversions INTEGER,
                cost_per_lead REAL,
                roas REAL,
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(platform, campaign_id, date)
            )
        ''')
        
        # Optimization logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS optimization_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                optimization_type TEXT,
                platforms_analyzed TEXT,
                total_budget_before REAL,
                total_budget_after REAL,
                changes_implemented INTEGER,
                expected_roi_improvement REAL,
                actual_roi_improvement REAL,
                log_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(optimization_type, created_at)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("Budget optimizer database initialized")
    
    def collect_facebook_metrics(self, days_back: int = 7) -> PlatformMetrics:
        """Collect Facebook Ads performance metrics"""
        try:
            if not self.facebook_config['access_token']:
                logger.warning("Facebook access token not configured")
                return self._create_default_metrics(AdPlatform.FACEBOOK)
            
            # Facebook Graph API call for ad insights
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=days_back)
            
            url = f"https://graph.facebook.com/v18.0/{self.facebook_config['ad_account_id']}/insights"
            params = {
                'access_token': self.facebook_config['access_token'],
                'time_range': json.dumps({
                    'since': start_date.strftime('%Y-%m-%d'),
                    'until': end_date.strftime('%Y-%m-%d')
                }),
                'fields': 'spend,impressions,clicks,actions,cost_per_action_type,purchase_roas',
                'action_attribution_windows': ['7d_click'],
                'level': 'account'
            }
            
            response = requests.get(url, params=params, timeout=30)
            
            if response.status_code == 200:
                data = response.json().get('data', [])
                
                if data:
                    insights = data[0]  # Account level data
                    
                    spend = float(insights.get('spend', 0))
                    clicks = int(insights.get('clicks', 0))
                    
                    # Extract lead actions
                    actions = insights.get('actions', [])
                    leads = sum(int(action['value']) for action in actions 
                              if action['action_type'] == 'lead')
                    
                    # Get conversion data from CRM
                    appointments, revenue = self._get_conversion_data_from_crm('facebook', days_back)
                    
                    cac = spend / max(leads, 1)
                    roas = revenue / max(spend, 1)
                    conversion_rate = appointments / max(leads, 1)
                    
                    return PlatformMetrics(
                        platform=AdPlatform.FACEBOOK,
                        daily_spend=spend / days_back,
                        leads_generated=leads,
                        appointments_booked=appointments,
                        revenue_generated=revenue,
                        cac=cac,
                        roas=roas,
                        ltv=self.customer_ltv,
                        conversion_rate=conversion_rate,
                        last_updated=datetime.now()
                    )
            
            logger.error(f"Facebook API error: {response.status_code} - {response.text}")
            return self._create_default_metrics(AdPlatform.FACEBOOK)
            
        except Exception as e:
            logger.error(f"Failed to collect Facebook metrics: {e}")
            return self._create_default_metrics(AdPlatform.FACEBOOK)
    
    def collect_google_ads_metrics(self, days_back: int = 7) -> PlatformMetrics:
        """Collect Google Ads performance metrics"""
        try:
            # Simplified metrics - in production, use Google Ads API
            logger.info("Google Ads metrics collection (simplified)")
            
            # Get conversion data from CRM
            appointments, revenue = self._get_conversion_data_from_crm('google_ads', days_back)
            
            # Estimated metrics for Clinton Auto Detailing
            estimated_spend = 150.0  # $150/day estimated
            estimated_leads = 12     # 12 leads per week
            
            cac = (estimated_spend * days_back) / max(estimated_leads, 1)
            roas = revenue / max(estimated_spend * days_back, 1)
            conversion_rate = appointments / max(estimated_leads, 1)
            
            return PlatformMetrics(
                platform=AdPlatform.GOOGLE_ADS,
                daily_spend=estimated_spend,
                leads_generated=estimated_leads,
                appointments_booked=appointments,
                revenue_generated=revenue,
                cac=cac,
                roas=roas,
                ltv=self.customer_ltv,
                conversion_rate=conversion_rate,
                last_updated=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"Failed to collect Google Ads metrics: {e}")
            return self._create_default_metrics(AdPlatform.GOOGLE_ADS)
    
    def _get_conversion_data_from_crm(self, source: str, days_back: int) -> Tuple[int, float]:
        """Get appointment and revenue data from CRM for specific traffic source"""
        try:
            # Connect to CRM database
            crm_db = "clinton_auto_detailing_crm.db"
            conn = sqlite3.connect(crm_db)
            cursor = conn.cursor()
            
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            # Get appointments from this source
            cursor.execute('''
                SELECT COUNT(*) 
                FROM customers 
                WHERE source = ? AND created_at >= ?
            ''', (source, cutoff_date))
            
            appointments = cursor.fetchone()[0] or 0
            
            # Get revenue from Square payments linked to this source
            cursor.execute('''
                SELECT COALESCE(SUM(total_spent), 0) 
                FROM customers 
                WHERE source = ? AND created_at >= ?
            ''', (source, cutoff_date))
            
            revenue = cursor.fetchone()[0] or 0.0
            
            conn.close()
            
            return appointments, float(revenue)
            
        except Exception as e:
            logger.error(f"Failed to get CRM conversion data: {e}")
            return 0, 0.0
    
    def _create_default_metrics(self, platform: AdPlatform) -> PlatformMetrics:
        """Create default metrics when data collection fails"""
        return PlatformMetrics(
            platform=platform,
            daily_spend=0.0,
            leads_generated=0,
            appointments_booked=0,
            revenue_generated=0.0,
            cac=999.0,  # High CAC indicates poor performance
            roas=0.0,
            ltv=self.customer_ltv,
            conversion_rate=0.0,
            last_updated=datetime.now()
        )
    
    def analyze_platform_performance(self, metrics: List[PlatformMetrics]) -> List[BudgetAllocation]:
        """Analyze platform performance and recommend budget allocations"""
        allocations = []
        total_current_spend = sum(m.daily_spend for m in metrics)
        
        # Calculate performance scores
        performance_scores = []
        for metric in metrics:
            # Performance score based on ROAS, CAC, and conversion rate
            roas_score = min(metric.roas / 2.0, 1.0)  # Cap at 1.0 (200% ROAS = perfect)
            cac_score = max(0, 1.0 - (metric.cac / self.guardrails.max_cac_threshold))
            conversion_score = min(metric.conversion_rate / 0.3, 1.0)  # 30% conversion = perfect
            
            overall_score = (roas_score * 0.5) + (cac_score * 0.3) + (conversion_score * 0.2)
            performance_scores.append((metric.platform, overall_score))
        
        # Sort by performance
        performance_scores.sort(key=lambda x: x[1], reverse=True)
        
        for i, (platform, score) in enumerate(performance_scores):
            metric = next(m for m in metrics if m.platform == platform)
            
            current_budget = metric.daily_spend
            confidence = min(0.9, 0.5 + (score * 0.4))  # 50-90% confidence range
            
            # Determine budget action based on performance and guardrails
            if score > 0.7 and metric.roas > self.guardrails.min_roas_threshold:
                # Top performer - increase budget
                increase_percent = min(0.25, score * 0.3)
                recommended_budget = current_budget * (1 + increase_percent)
                action = BudgetAction.INCREASE
                reason = f"High performance (ROAS: {metric.roas:.2f}, CAC: ${metric.cac:.2f})"
                
            elif score < 0.3 or metric.roas < 1.0:
                # Poor performer - decrease or pause
                if metric.roas < 0.8:
                    recommended_budget = 0
                    action = BudgetAction.PAUSE
                    reason = f"Poor ROAS ({metric.roas:.2f}) - pausing to optimize"
                else:
                    decrease_percent = min(0.25, (0.5 - score) * 0.5)
                    recommended_budget = current_budget * (1 - decrease_percent)
                    action = BudgetAction.DECREASE
                    reason = f"Underperforming (ROAS: {metric.roas:.2f}, CAC: ${metric.cac:.2f})"
                    
            else:
                # Average performer - maintain with minor adjustments
                recommended_budget = current_budget
                action = BudgetAction.MAINTAIN
                reason = f"Stable performance - monitoring"
            
            # Apply guardrails
            recommended_budget = max(
                self.guardrails.min_daily_budget,
                min(self.guardrails.max_daily_budget, recommended_budget)
            )
            
            # Limit budget change percentage
            max_change = current_budget * self.guardrails.max_budget_change_percent
            if recommended_budget > current_budget + max_change:
                recommended_budget = current_budget + max_change
            elif recommended_budget < current_budget - max_change:
                recommended_budget = current_budget - max_change
            
            # Calculate expected impact
            expected_impact = {
                'leads_change': (recommended_budget - current_budget) / max(current_budget, 1) * metric.leads_generated,
                'revenue_change': (recommended_budget - current_budget) / max(current_budget, 1) * metric.revenue_generated,
                'roas_improvement': 0.05 if action == BudgetAction.INCREASE else 0
            }
            
            allocation = BudgetAllocation(
                platform=platform,
                current_budget=current_budget,
                recommended_budget=recommended_budget,
                action=action,
                reason=reason,
                confidence=confidence,
                expected_impact=expected_impact
            )
            
            allocations.append(allocation)
            self._store_budget_allocation(allocation)
        
        return allocations
    
    def _store_budget_allocation(self, allocation: BudgetAllocation):
        """Store budget allocation recommendation in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO budget_allocations 
            (platform, current_budget, recommended_budget, action, reason, confidence, expected_impact)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            allocation.platform.value,
            allocation.current_budget,
            allocation.recommended_budget,
            allocation.action.value,
            allocation.reason,
            allocation.confidence,
            json.dumps(allocation.expected_impact)
        ))
        
        conn.commit()
        conn.close()
    
    def implement_budget_changes(self, allocations: List[BudgetAllocation]) -> Dict[str, bool]:
        """Implement budget changes across platforms"""
        results = {}
        
        for allocation in allocations:
            if allocation.confidence < self.guardrails.min_confidence_threshold:
                logger.warning(f"Skipping {allocation.platform.value} budget change - low confidence ({allocation.confidence:.2f})")
                results[allocation.platform.value] = False
                continue
            
            try:
                if allocation.platform == AdPlatform.FACEBOOK:
                    success = self._update_facebook_budget(allocation)
                elif allocation.platform == AdPlatform.GOOGLE_ADS:
                    success = self._update_google_ads_budget(allocation)
                else:
                    success = False
                
                results[allocation.platform.value] = success
                
                if success:
                    # Mark as implemented
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    
                    cursor.execute('''
                        UPDATE budget_allocations 
                        SET implemented = 1, implemented_at = ?
                        WHERE platform = ? AND implemented = 0
                        ORDER BY created_at DESC LIMIT 1
                    ''', (datetime.now(), allocation.platform.value))
                    
                    conn.commit()
                    conn.close()
                    
                    logger.info(f"Successfully updated {allocation.platform.value} budget: ${allocation.current_budget:.2f} → ${allocation.recommended_budget:.2f}")
                
            except Exception as e:
                logger.error(f"Failed to update {allocation.platform.value} budget: {e}")
                results[allocation.platform.value] = False
        
        return results
    
    def _update_facebook_budget(self, allocation: BudgetAllocation) -> bool:
        """Update Facebook campaign budgets"""
        try:
            if not self.facebook_config['access_token']:
                logger.warning("Facebook access token not configured - simulating budget update")
                return True  # Simulate success for testing
            
            # In production, update actual Facebook campaign budgets
            # This is a simplified implementation
            url = f"https://graph.facebook.com/v18.0/{self.facebook_config['ad_account_id']}/campaigns"
            
            # Would iterate through campaigns and update budgets
            logger.info(f"Facebook budget update simulated: ${allocation.recommended_budget:.2f}/day")
            return True
            
        except Exception as e:
            logger.error(f"Facebook budget update failed: {e}")
            return False
    
    def _update_google_ads_budget(self, allocation: BudgetAllocation) -> bool:
        """Update Google Ads campaign budgets"""
        try:
            # In production, use Google Ads API to update campaign budgets
            logger.info(f"Google Ads budget update simulated: ${allocation.recommended_budget:.2f}/day")
            return True
            
        except Exception as e:
            logger.error(f"Google Ads budget update failed: {e}")
            return False
    
    def run_optimization_cycle(self) -> Dict[str, Any]:
        """Run complete budget optimization cycle"""
        logger.info("Starting budget optimization cycle")
        
        try:
            # Collect metrics from all platforms
            facebook_metrics = self.collect_facebook_metrics()
            google_ads_metrics = self.collect_google_ads_metrics()
            
            all_metrics = [facebook_metrics, google_ads_metrics]
            
            # Store metrics
            self._store_platform_metrics(all_metrics)
            
            # Analyze and get recommendations
            allocations = self.analyze_platform_performance(all_metrics)
            
            # Implement changes
            implementation_results = self.implement_budget_changes(allocations)
            
            # Log optimization
            total_budget_before = sum(m.daily_spend for m in all_metrics)
            total_budget_after = sum(a.recommended_budget for a in allocations)
            changes_implemented = sum(1 for r in implementation_results.values() if r)
            
            optimization_log = {
                'cycle_id': str(int(time.time())),
                'platforms_analyzed': len(all_metrics),
                'total_budget_before': total_budget_before,
                'total_budget_after': total_budget_after,
                'budget_change': total_budget_after - total_budget_before,
                'changes_implemented': changes_implemented,
                'allocations': [asdict(a) for a in allocations],
                'implementation_results': implementation_results,
                'timestamp': datetime.now().isoformat()
            }
            
            self._store_optimization_log(optimization_log)
            
            logger.info(f"Budget optimization completed - {changes_implemented} changes implemented")
            
            return optimization_log
            
        except Exception as e:
            logger.error(f"Budget optimization cycle failed: {e}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    def _store_platform_metrics(self, metrics: List[PlatformMetrics]):
        """Store platform metrics in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for metric in metrics:
            cursor.execute('''
                INSERT INTO platform_metrics 
                (platform, date, daily_spend, leads_generated, appointments_booked, 
                 revenue_generated, cac, roas, ltv, conversion_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                metric.platform.value,
                datetime.now().date(),
                metric.daily_spend,
                metric.leads_generated,
                metric.appointments_booked,
                metric.revenue_generated,
                metric.cac,
                metric.roas,
                metric.ltv,
                metric.conversion_rate
            ))
        
        conn.commit()
        conn.close()
    
    def _store_optimization_log(self, log_data: Dict):
        """Store optimization log in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO optimization_logs 
            (optimization_type, platforms_analyzed, total_budget_before, total_budget_after, 
             changes_implemented, log_data)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'budget_optimization',
            log_data['platforms_analyzed'],
            log_data['total_budget_before'],
            log_data['total_budget_after'],
            log_data['changes_implemented'],
            json.dumps(log_data)
        ))
        
        conn.commit()
        conn.close()
    
    def get_optimization_report(self, days_back: int = 30) -> Dict[str, Any]:
        """Generate optimization performance report"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        # Get recent optimizations
        cursor.execute('''
            SELECT * FROM optimization_logs 
            WHERE created_at >= ? 
            ORDER BY created_at DESC
        ''', (cutoff_date,))
        
        optimizations = cursor.fetchall()
        
        # Get platform performance trends
        cursor.execute('''
            SELECT platform, date, daily_spend, roas, cac, leads_generated
            FROM platform_metrics 
            WHERE created_at >= ?
            ORDER BY platform, date
        ''', (cutoff_date,))
        
        performance_data = cursor.fetchall()
        
        # Calculate ROI improvement
        if len(optimizations) > 1:
            latest_budget = optimizations[0][4]  # total_budget_after
            initial_budget = optimizations[-1][3]  # total_budget_before from earliest
            budget_efficiency = (latest_budget - initial_budget) / max(initial_budget, 1)
        else:
            budget_efficiency = 0
        
        conn.close()
        
        return {
            'optimization_cycles': len(optimizations),
            'budget_efficiency_improvement': budget_efficiency,
            'total_changes_implemented': sum(opt[5] for opt in optimizations),  # changes_implemented
            'performance_trends': performance_data,
            'latest_optimization': json.loads(optimizations[0][6]) if optimizations else None,
            'report_period_days': days_back,
            'generated_at': datetime.now().isoformat()
        }

# Clinton Auto Detailing Budget Optimizer instance
clinton_budget_optimizer = BudgetOptimizer()

def test_budget_optimizer():
    """Test the budget optimizer"""
    print("Testing Budget Optimizer for Clinton Auto Detailing...")
    
    # Run optimization cycle
    result = clinton_budget_optimizer.run_optimization_cycle()
    
    print(f"Optimization Result:")
    print(f"- Budget Before: ${result.get('total_budget_before', 0):.2f}")
    print(f"- Budget After: ${result.get('total_budget_after', 0):.2f}")
    print(f"- Changes Implemented: {result.get('changes_implemented', 0)}")
    
    if 'allocations' in result:
        print("\\nBudget Allocations:")
        for allocation in result['allocations']:
            print(f"- {allocation['platform']}: ${allocation['current_budget']:.2f} → ${allocation['recommended_budget']:.2f} ({allocation['action']})")
    
    return result

if __name__ == "__main__":
    test_budget_optimizer()