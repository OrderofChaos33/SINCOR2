"""
Churn/Winback Engine - Day 5 Hardening
Detects inactive customers and triggers automated winback sequences
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

logger = logging.getLogger(__name__)

class CustomerStatus(Enum):
    ACTIVE = "active"
    AT_RISK = "at_risk"          # 45-60 days since last service
    CHURNED = "churned"          # 90+ days since last service
    RECOVERED = "recovered"       # Came back after churn
    LOST = "lost"                # 180+ days, multiple failed attempts

class WinbackChannel(Enum):
    EMAIL = "email"
    SMS = "sms"
    PHONE_CALL = "phone_call"
    DIRECT_MAIL = "direct_mail"
    FACEBOOK_RETARGETING = "facebook_retargeting"

class WinbackSequenceType(Enum):
    GENTLE_REMINDER = "gentle_reminder"     # 45-60 days
    MISS_YOU = "miss_you"                   # 60-90 days
    LAST_CHANCE = "last_chance"             # 90-120 days
    HAIL_MARY = "hail_mary"                 # 120+ days

@dataclass
class CustomerChurnRisk:
    customer_id: str
    customer_name: str
    email: str
    phone: str
    last_service_date: datetime
    days_since_service: int
    total_spent: float
    service_frequency: float  # Average days between services
    predicted_ltv: float
    churn_probability: float
    status: CustomerStatus
    risk_factors: List[str]

@dataclass
class WinbackCampaign:
    id: str
    customer_id: str
    sequence_type: WinbackSequenceType
    channels: List[WinbackChannel]
    trigger_date: datetime
    steps_completed: int
    total_steps: int
    response_received: bool
    converted: bool
    revenue_recovered: float
    cost: float
    created_at: datetime

class ChurnWinbackEngine:
    def __init__(self):
        self.db_path = "clinton_auto_detailing_churn_winback.db"
        
        # Clinton Auto Detailing specific settings
        self.business_name = "Clinton Auto Detailing"
        self.location = "Clinton, IA"
        self.service_area_radius = 10  # miles
        
        # Churn definitions (days since last service)
        self.at_risk_threshold = 45
        self.churned_threshold = 90
        self.lost_threshold = 180
        
        # Winback sequence settings
        self.sequences = {
            WinbackSequenceType.GENTLE_REMINDER: {
                'delay_days': [0, 7, 14],  # Send immediately, then 7 days, then 14 days
                'channels': [WinbackChannel.EMAIL, WinbackChannel.SMS],
                'discount_percent': 10
            },
            WinbackSequenceType.MISS_YOU: {
                'delay_days': [0, 5, 12, 21],
                'channels': [WinbackChannel.EMAIL, WinbackChannel.SMS, WinbackChannel.PHONE_CALL],
                'discount_percent': 20
            },
            WinbackSequenceType.LAST_CHANCE: {
                'delay_days': [0, 7, 14, 28],
                'channels': [WinbackChannel.EMAIL, WinbackChannel.SMS, WinbackChannel.FACEBOOK_RETARGETING],
                'discount_percent': 25
            },
            WinbackSequenceType.HAIL_MARY: {
                'delay_days': [0, 14, 30],
                'channels': [WinbackChannel.DIRECT_MAIL, WinbackChannel.PHONE_CALL],
                'discount_percent': 30
            }
        }
        
        self.init_database()
        
    def init_database(self):
        """Initialize churn/winback database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Customer churn risk tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS customer_churn_risk (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT,
                customer_name TEXT,
                email TEXT,
                phone TEXT,
                last_service_date DATE,
                days_since_service INTEGER,
                total_spent REAL,
                service_frequency REAL,
                predicted_ltv REAL,
                churn_probability REAL,
                status TEXT,
                risk_factors TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(customer_id),
                INDEX(status, days_since_service)
            )
        ''')
        
        # Winback campaigns
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS winback_campaigns (
                id TEXT PRIMARY KEY,
                customer_id TEXT,
                sequence_type TEXT,
                channels TEXT,
                trigger_date TIMESTAMP,
                steps_completed INTEGER DEFAULT 0,
                total_steps INTEGER,
                response_received BOOLEAN DEFAULT 0,
                converted BOOLEAN DEFAULT 0,
                revenue_recovered REAL DEFAULT 0,
                cost REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(customer_id, sequence_type),
                INDEX(trigger_date)
            )
        ''')
        
        # Winback sequence steps
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS winback_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT,
                step_number INTEGER,
                channel TEXT,
                message_template TEXT,
                scheduled_date TIMESTAMP,
                sent_date TIMESTAMP,
                opened BOOLEAN DEFAULT 0,
                clicked BOOLEAN DEFAULT 0,
                responded BOOLEAN DEFAULT 0,
                cost REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id) REFERENCES winback_campaigns (id),
                INDEX(campaign_id, step_number)
            )
        ''')
        
        # Winback performance metrics
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS winback_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                sequence_type TEXT,
                customers_targeted INTEGER,
                campaigns_sent INTEGER,
                responses_received INTEGER,
                customers_recovered INTEGER,
                revenue_recovered REAL,
                total_cost REAL,
                recovery_rate REAL,
                roi REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(date, sequence_type)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("Churn/winback database initialized")
    
    def analyze_customer_churn_risk(self) -> List[CustomerChurnRisk]:
        """Analyze all customers for churn risk"""
        try:
            # Connect to CRM database to get customer data
            crm_db = "clinton_auto_detailing_crm.db"
            conn = sqlite3.connect(crm_db)
            cursor = conn.cursor()
            
            # Get customer data with last service info
            cursor.execute('''
                SELECT 
                    id, first_name, last_name, email, phone,
                    last_service_date, total_spent, service_count,
                    created_at
                FROM customers 
                WHERE last_service_date IS NOT NULL
                ORDER BY last_service_date DESC
            ''')
            
            customers = cursor.fetchall()
            conn.close()
            
            churn_risks = []
            
            for customer in customers:
                (customer_id, first_name, last_name, email, phone, 
                 last_service_date, total_spent, service_count, created_at) = customer
                
                # Calculate churn risk metrics
                last_service = datetime.fromisoformat(last_service_date)
                days_since_service = (datetime.now() - last_service).days
                
                # Calculate service frequency
                customer_age_days = (datetime.now() - datetime.fromisoformat(created_at)).days
                service_frequency = customer_age_days / max(service_count, 1)
                
                # Predict LTV based on past behavior
                predicted_ltv = self._predict_customer_ltv(total_spent, service_count, service_frequency)
                
                # Calculate churn probability
                churn_probability = self._calculate_churn_probability(
                    days_since_service, service_frequency, total_spent
                )
                
                # Determine status
                if days_since_service >= self.lost_threshold:
                    status = CustomerStatus.LOST
                elif days_since_service >= self.churned_threshold:
                    status = CustomerStatus.CHURNED
                elif days_since_service >= self.at_risk_threshold:
                    status = CustomerStatus.AT_RISK
                else:
                    status = CustomerStatus.ACTIVE
                
                # Identify risk factors
                risk_factors = self._identify_risk_factors(
                    days_since_service, service_frequency, total_spent, service_count
                )
                
                churn_risk = CustomerChurnRisk(
                    customer_id=customer_id,
                    customer_name=f"{first_name} {last_name}",
                    email=email or "",
                    phone=phone or "",
                    last_service_date=last_service,
                    days_since_service=days_since_service,
                    total_spent=float(total_spent),
                    service_frequency=service_frequency,
                    predicted_ltv=predicted_ltv,
                    churn_probability=churn_probability,
                    status=status,
                    risk_factors=risk_factors
                )
                
                churn_risks.append(churn_risk)
                
                # Store in database
                self._store_churn_risk(churn_risk)
            
            logger.info(f"Analyzed churn risk for {len(churn_risks)} customers")
            return churn_risks
            
        except Exception as e:
            logger.error(f"Failed to analyze customer churn risk: {e}")
            return []
    
    def _predict_customer_ltv(self, total_spent: float, service_count: int, 
                            service_frequency: float) -> float:
        """Predict customer lifetime value"""
        # Simple LTV prediction for Clinton Auto Detailing
        avg_service_value = total_spent / max(service_count, 1)
        
        # Estimate future services based on frequency
        if service_frequency < 60:  # High frequency (every 2 months)
            estimated_annual_services = 6
        elif service_frequency < 120:  # Medium frequency (every 4 months)
            estimated_annual_services = 3
        else:  # Low frequency
            estimated_annual_services = 1
        
        # Assume 2-year customer lifespan for auto detailing
        predicted_ltv = avg_service_value * estimated_annual_services * 2
        
        return max(predicted_ltv, total_spent)  # At least what they've already spent
    
    def _calculate_churn_probability(self, days_since_service: int, 
                                   service_frequency: float, total_spent: float) -> float:
        """Calculate probability of customer churn (0-1)"""
        # Base probability on days since service vs. their usual frequency
        if service_frequency > 0:
            frequency_ratio = days_since_service / service_frequency
        else:
            frequency_ratio = days_since_service / 90  # Default 3-month frequency
        
        # Higher spending customers are less likely to churn
        spending_factor = max(0.5, min(1.0, total_spent / 500))  # $500 = loyal customer
        
        # Calculate probability
        base_probability = min(0.95, frequency_ratio / 2)  # Cap at 95%
        adjusted_probability = base_probability * spending_factor
        
        return max(0.0, min(1.0, adjusted_probability))
    
    def _identify_risk_factors(self, days_since_service: int, service_frequency: float,
                             total_spent: float, service_count: int) -> List[str]:
        """Identify specific risk factors for churn"""
        risk_factors = []
        
        if days_since_service > service_frequency * 1.5:
            risk_factors.append("Overdue for regular service")
        
        if service_count == 1:
            risk_factors.append("Single service customer")
        
        if total_spent < 100:
            risk_factors.append("Low total spend")
        
        if service_frequency > 180:
            risk_factors.append("Infrequent service pattern")
        
        # Seasonal factors for Iowa
        current_month = datetime.now().month
        if current_month in [11, 12, 1, 2, 3] and days_since_service > 60:
            risk_factors.append("Winter service gap - salt damage risk")
        
        return risk_factors
    
    def _store_churn_risk(self, churn_risk: CustomerChurnRisk):
        """Store churn risk analysis in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO customer_churn_risk
            (customer_id, customer_name, email, phone, last_service_date,
             days_since_service, total_spent, service_frequency, predicted_ltv,
             churn_probability, status, risk_factors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            churn_risk.customer_id,
            churn_risk.customer_name,
            churn_risk.email,
            churn_risk.phone,
            churn_risk.last_service_date.date(),
            churn_risk.days_since_service,
            churn_risk.total_spent,
            churn_risk.service_frequency,
            churn_risk.predicted_ltv,
            churn_risk.churn_probability,
            churn_risk.status.value,
            json.dumps(churn_risk.risk_factors)
        ))
        
        conn.commit()
        conn.close()
    
    def trigger_winback_campaigns(self, churn_risks: List[CustomerChurnRisk]) -> List[WinbackCampaign]:
        """Trigger appropriate winback campaigns for at-risk customers"""
        campaigns = []
        
        for risk in churn_risks:
            # Skip if already active or lost
            if risk.status in [CustomerStatus.ACTIVE, CustomerStatus.LOST]:
                continue
            
            # Check if already has active campaign
            if self._has_active_campaign(risk.customer_id):
                continue
            
            # Determine sequence type based on status and days
            sequence_type = self._determine_sequence_type(risk)
            
            if sequence_type:
                campaign = self._create_winback_campaign(risk, sequence_type)
                if campaign:
                    campaigns.append(campaign)
        
        logger.info(f"Triggered {len(campaigns)} winback campaigns")
        return campaigns
    
    def _has_active_campaign(self, customer_id: str) -> bool:
        """Check if customer has active winback campaign"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM winback_campaigns
            WHERE customer_id = ? AND converted = 0
            AND created_at > datetime('now', '-30 days')
        ''', (customer_id,))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def _determine_sequence_type(self, risk: CustomerChurnRisk) -> Optional[WinbackSequenceType]:
        """Determine appropriate winback sequence type"""
        if risk.status == CustomerStatus.AT_RISK:
            return WinbackSequenceType.GENTLE_REMINDER
        elif risk.status == CustomerStatus.CHURNED and risk.days_since_service < 120:
            return WinbackSequenceType.MISS_YOU
        elif risk.status == CustomerStatus.CHURNED and risk.days_since_service < 150:
            return WinbackSequenceType.LAST_CHANCE
        elif risk.days_since_service >= 120:
            return WinbackSequenceType.HAIL_MARY
        
        return None
    
    def _create_winback_campaign(self, risk: CustomerChurnRisk, 
                               sequence_type: WinbackSequenceType) -> Optional[WinbackCampaign]:
        """Create winback campaign for customer"""
        try:
            sequence_config = self.sequences[sequence_type]
            
            campaign = WinbackCampaign(
                id=str(uuid.uuid4()),
                customer_id=risk.customer_id,
                sequence_type=sequence_type,
                channels=sequence_config['channels'],
                trigger_date=datetime.now(),
                steps_completed=0,
                total_steps=len(sequence_config['delay_days']),
                response_received=False,
                converted=False,
                revenue_recovered=0.0,
                cost=0.0,
                created_at=datetime.now()
            )
            
            # Store campaign
            self._store_winback_campaign(campaign)
            
            # Create sequence steps
            self._create_campaign_steps(campaign, sequence_config, risk)
            
            logger.info(f"Created {sequence_type.value} campaign for {risk.customer_name}")
            return campaign
            
        except Exception as e:
            logger.error(f"Failed to create winback campaign: {e}")
            return None
    
    def _store_winback_campaign(self, campaign: WinbackCampaign):
        """Store winback campaign in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO winback_campaigns
            (id, customer_id, sequence_type, channels, trigger_date,
             steps_completed, total_steps, response_received, converted,
             revenue_recovered, cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            campaign.id,
            campaign.customer_id,
            campaign.sequence_type.value,
            json.dumps([c.value for c in campaign.channels]),
            campaign.trigger_date,
            campaign.steps_completed,
            campaign.total_steps,
            campaign.response_received,
            campaign.converted,
            campaign.revenue_recovered,
            campaign.cost
        ))
        
        conn.commit()
        conn.close()
    
    def _create_campaign_steps(self, campaign: WinbackCampaign, 
                             sequence_config: Dict, risk: CustomerChurnRisk):
        """Create individual steps for winback campaign"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        delay_days = sequence_config['delay_days']
        channels = sequence_config['channels']
        discount_percent = sequence_config['discount_percent']
        
        for i, delay in enumerate(delay_days):
            channel = channels[i % len(channels)]  # Cycle through channels
            
            # Generate message template
            message_template = self._generate_message_template(
                campaign.sequence_type, channel, risk, discount_percent, i + 1
            )
            
            scheduled_date = campaign.trigger_date + timedelta(days=delay)
            
            cursor.execute('''
                INSERT INTO winback_steps
                (campaign_id, step_number, channel, message_template, scheduled_date, cost)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                campaign.id,
                i + 1,
                channel.value,
                message_template,
                scheduled_date,
                self._estimate_step_cost(channel)
            ))
        
        conn.commit()
        conn.close()
    
    def _generate_message_template(self, sequence_type: WinbackSequenceType,
                                 channel: WinbackChannel, risk: CustomerChurnRisk,
                                 discount_percent: int, step_number: int) -> str:
        """Generate personalized message template"""
        
        # Clinton Auto Detailing specific message templates
        templates = {
            WinbackSequenceType.GENTLE_REMINDER: {
                WinbackChannel.EMAIL: [
                    f"Hi {risk.customer_name.split()[0]},\\n\\nIt's been a while since we've seen you at Clinton Auto Detailing! Your car deserves the best care, especially with Iowa's tough winter conditions.\\n\\nBook your next detail and save {discount_percent}% - just mention this email.\\n\\nBest,\\nClinton Auto Detailing Team",
                    f"Hey {risk.customer_name.split()[0]},\\n\\nMissing your fresh, clean ride? Winter salt can be brutal on your car's finish.\\n\\nSchedule your next service and get {discount_percent}% off. We're right here in Clinton, IA!\\n\\nCall us today!"
                ],
                WinbackChannel.SMS: [
                    f"Hi {risk.customer_name.split()[0]}! Clinton Auto Detailing here. Ready to get your car looking fresh again? {discount_percent}% off your next service - text back BOOK to schedule!",
                    f"Hey {risk.customer_name.split()[0]}! Winter's tough on cars in Iowa. Let's get yours protected! {discount_percent}% off this week. Reply YES to book."
                ]
            },
            WinbackSequenceType.MISS_YOU: {
                WinbackChannel.EMAIL: [
                    f"We Miss You, {risk.customer_name.split()[0]}!\\n\\nYour car was always one of our favorites to work on. Iowa winters can be harsh - don't let salt damage build up!\\n\\n{discount_percent}% OFF your return visit. Because we genuinely miss seeing you and your ride!",
                    f"{risk.customer_name.split()[0]}, come back to Clinton Auto Detailing!\\n\\nWe haven't forgotten about you or your car. Winter's been tough, but we can get your vehicle back to showroom condition.\\n\\nSpecial {discount_percent}% welcome back discount just for you!"
                ],
                WinbackChannel.SMS: [
                    f"We miss you at Clinton Auto Detailing, {risk.customer_name.split()[0]}! Your car needs winter protection. {discount_percent}% off welcome back special. Text BACK to return!",
                ],
                WinbackChannel.PHONE_CALL: [
                    f"Phone script: Hi {risk.customer_name.split()[0]}, this is [Name] from Clinton Auto Detailing. We noticed it's been {risk.days_since_service} days since your last service. We'd love to have you back with a {discount_percent}% welcome back discount..."
                ]
            },
            WinbackSequenceType.LAST_CHANCE: {
                WinbackChannel.EMAIL: [
                    f"Last Chance, {risk.customer_name.split()[0]}\\n\\nWe're about to remove you from our customer list, but we wanted to give you one final opportunity.\\n\\n{discount_percent}% OFF - our biggest discount ever - because we believe your car deserves the best.\\n\\nThis is our final outreach. We hope to see you again!",
                ],
                WinbackChannel.SMS: [
                    f"FINAL OFFER {risk.customer_name.split()[0]} - {discount_percent}% off at Clinton Auto Detailing. After this, we'll stop reaching out. Text FINAL to claim.",
                ],
                WinbackChannel.FACEBOOK_RETARGETING: [
                    f"Facebook ad copy: {risk.customer_name.split()[0]}, Clinton Auto Detailing misses you! {discount_percent}% off your return - our biggest discount ever. Click to book!"
                ]
            },
            WinbackSequenceType.HAIL_MARY: {
                WinbackChannel.DIRECT_MAIL: [
                    f"Direct mail postcard: {risk.customer_name}\\n{risk.customer_name.split()[0]}, we're sending this postcard because email and texts weren't reaching you. Your car needs protection from Iowa's harsh elements. {discount_percent}% off - our final offer. Call us at [PHONE] or visit Clinton Auto Detailing."
                ],
                WinbackChannel.PHONE_CALL: [
                    f"Personal call script: Hi {risk.customer_name.split()[0]}, this is a personal call from Clinton Auto Detailing. It's been {risk.days_since_service} days since we've seen you. We're offering {discount_percent}% off as our final attempt to earn back your business..."
                ]
            }
        }
        
        if sequence_type in templates and channel in templates[sequence_type]:
            messages = templates[sequence_type][channel]
            return messages[(step_number - 1) % len(messages)]
        
        return f"Default message for {risk.customer_name} - {discount_percent}% off your next service!"
    
    def _estimate_step_cost(self, channel: WinbackChannel) -> float:
        """Estimate cost per step by channel"""
        costs = {
            WinbackChannel.EMAIL: 0.10,
            WinbackChannel.SMS: 0.05,
            WinbackChannel.PHONE_CALL: 2.00,
            WinbackChannel.DIRECT_MAIL: 1.50,
            WinbackChannel.FACEBOOK_RETARGETING: 3.00
        }
        
        return costs.get(channel, 0.50)
    
    def execute_scheduled_steps(self) -> int:
        """Execute winback steps scheduled for today"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get steps scheduled for today or overdue
        cursor.execute('''
            SELECT ws.*, wc.customer_id, cr.customer_name, cr.email, cr.phone
            FROM winback_steps ws
            JOIN winback_campaigns wc ON ws.campaign_id = wc.id
            JOIN customer_churn_risk cr ON wc.customer_id = cr.customer_id
            WHERE ws.sent_date IS NULL
            AND ws.scheduled_date <= datetime('now')
            ORDER BY ws.scheduled_date
        ''')
        
        steps = cursor.fetchall()
        executed_count = 0
        
        for step in steps:
            try:
                step_id = step[0]
                campaign_id = step[1]
                channel = WinbackChannel(step[3])
                message = step[4]
                customer_email = step[7]
                customer_phone = step[8]
                
                # Execute the step based on channel
                success = self._execute_step(channel, message, customer_email, customer_phone)
                
                if success:
                    # Mark as sent
                    cursor.execute('''
                        UPDATE winback_steps 
                        SET sent_date = ?, responded = ?
                        WHERE id = ?
                    ''', (datetime.now(), False, step_id))
                    
                    executed_count += 1
                    
                    logger.info(f"Executed winback step {step_id} via {channel.value}")
                
            except Exception as e:
                logger.error(f"Failed to execute winback step {step[0]}: {e}")
        
        conn.commit()
        conn.close()
        
        logger.info(f"Executed {executed_count} scheduled winback steps")
        return executed_count
    
    def _execute_step(self, channel: WinbackChannel, message: str,
                     email: str, phone: str) -> bool:
        """Execute individual winback step"""
        try:
            if channel == WinbackChannel.EMAIL and email:
                # Send email (integrate with your email system)
                logger.info(f"EMAIL: {message[:50]}...")
                return True
            
            elif channel == WinbackChannel.SMS and phone:
                # Send SMS (integrate with Twilio)
                logger.info(f"SMS to {phone}: {message[:30]}...")
                return True
            
            elif channel == WinbackChannel.PHONE_CALL and phone:
                # Create call task (for manual follow-up)
                logger.info(f"CALL TASK: {phone} - {message[:30]}...")
                return True
            
            elif channel == WinbackChannel.FACEBOOK_RETARGETING:
                # Create Facebook retargeting audience
                logger.info(f"FB RETARGETING: {message[:30]}...")
                return True
            
            elif channel == WinbackChannel.DIRECT_MAIL:
                # Create direct mail task
                logger.info(f"DIRECT MAIL: {message[:30]}...")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to execute {channel.value} step: {e}")
            return False
    
    def run_churn_analysis_cycle(self) -> Dict[str, Any]:
        """Run complete churn analysis and winback cycle"""
        logger.info("Starting churn analysis and winback cycle")
        
        try:
            # Analyze customer churn risk
            churn_risks = self.analyze_customer_churn_risk()
            
            # Trigger winback campaigns
            new_campaigns = self.trigger_winback_campaigns(churn_risks)
            
            # Execute scheduled steps
            executed_steps = self.execute_scheduled_steps()
            
            # Generate summary
            summary = self._generate_cycle_summary(churn_risks, new_campaigns, executed_steps)
            
            logger.info(f"Churn cycle completed - {len(new_campaigns)} new campaigns, {executed_steps} steps executed")
            
            return summary
            
        except Exception as e:
            logger.error(f"Churn analysis cycle failed: {e}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    def _generate_cycle_summary(self, churn_risks: List[CustomerChurnRisk],
                              new_campaigns: List[WinbackCampaign], 
                              executed_steps: int) -> Dict[str, Any]:
        """Generate cycle summary report"""
        
        # Analyze churn risk distribution
        status_counts = {}
        for status in CustomerStatus:
            status_counts[status.value] = sum(1 for r in churn_risks if r.status == status)
        
        # Calculate potential revenue at risk
        at_risk_revenue = sum(r.predicted_ltv for r in churn_risks 
                            if r.status in [CustomerStatus.AT_RISK, CustomerStatus.CHURNED])
        
        return {
            'cycle_id': str(int(time.time())),
            'customers_analyzed': len(churn_risks),
            'status_distribution': status_counts,
            'potential_revenue_at_risk': at_risk_revenue,
            'new_campaigns_triggered': len(new_campaigns),
            'winback_steps_executed': executed_steps,
            'campaign_breakdown': {
                seq_type.value: sum(1 for c in new_campaigns if c.sequence_type == seq_type)
                for seq_type in WinbackSequenceType
            },
            'high_value_at_risk': len([r for r in churn_risks 
                                    if r.predicted_ltv > 300 and r.status != CustomerStatus.ACTIVE]),
            'timestamp': datetime.now().isoformat()
        }

# Clinton Auto Detailing Churn/Winback Engine
clinton_churn_engine = ChurnWinbackEngine()

def test_churn_winback_engine():
    """Test the churn/winback engine"""
    print("Testing Churn/Winback Engine for Clinton Auto Detailing...")
    
    # Run complete cycle
    result = clinton_churn_engine.run_churn_analysis_cycle()
    
    print(f"Churn Analysis Results:")
    print(f"- Customers Analyzed: {result.get('customers_analyzed', 0)}")
    print(f"- Revenue at Risk: ${result.get('potential_revenue_at_risk', 0):.2f}")
    print(f"- New Campaigns: {result.get('new_campaigns_triggered', 0)}")
    print(f"- Steps Executed: {result.get('winback_steps_executed', 0)}")
    
    if 'status_distribution' in result:
        print(f"\\nCustomer Status Distribution:")
        for status, count in result['status_distribution'].items():
            print(f"- {status.replace('_', ' ').title()}: {count}")
    
    return result

if __name__ == "__main__":
    test_churn_winback_engine()