"""
Schedule Filler Engine - Day 5 Hardening
Automatically launches targeted promotions to fill empty appointment slots
Drives traffic to clintondetailing.com/booking
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import random

logger = logging.getLogger(__name__)

class UtilizationLevel(Enum):
    OPTIMAL = "optimal"        # 80-100% booked
    GOOD = "good"             # 60-79% booked
    LOW = "low"               # 40-59% booked
    CRITICAL = "critical"     # <40% booked

class PromoUrgency(Enum):
    GENTLE = "gentle"         # 3+ days advance
    MODERATE = "moderate"     # 1-2 days advance
    URGENT = "urgent"         # Same day or next day

class PromoChannel(Enum):
    FACEBOOK_ADS = "facebook_ads"
    GOOGLE_ADS = "google_ads"
    EMAIL_BLAST = "email_blast"
    SMS_BLAST = "sms_blast"
    INSTAGRAM_STORY = "instagram_story"
    WEBSITE_BANNER = "website_banner"

@dataclass
class ScheduleAnalysis:
    date: datetime
    total_slots: int
    booked_slots: int
    available_slots: int
    utilization_percentage: float
    utilization_level: UtilizationLevel
    time_blocks: Dict[str, Dict[str, int]]  # morning/afternoon/evening availability
    revenue_potential: float
    days_until_date: int

@dataclass
class ScheduleFillerPromo:
    id: str
    target_date: datetime
    urgency: PromoUrgency
    discount_percentage: int
    discount_amount: float
    channels: List[PromoChannel]
    message: str
    cta_url: str
    target_audience: Dict[str, Any]
    budget_allocated: float
    slots_to_fill: int
    created_at: datetime
    expires_at: datetime

@dataclass
class PromoPerformance:
    promo_id: str
    channel: PromoChannel
    impressions: int
    clicks: int
    website_visits: int
    booking_page_visits: int
    bookings_generated: int
    revenue_generated: float
    cost: float
    ctr: float
    conversion_rate: float
    roi: float

class ScheduleFillerEngine:
    def __init__(self):
        self.db_path = "clinton_auto_detailing_schedule_filler.db"
        
        # Clinton Auto Detailing specific settings
        self.business_name = "Clinton Auto Detailing"
        self.location = "Clinton, IA"
        self.booking_url = "https://clintondetailing.com/booking"
        self.service_area_radius = 10  # miles
        
        # Business hours and capacity
        self.business_hours = {
            'monday': {'start': time(8, 0), 'end': time(17, 0)},
            'tuesday': {'start': time(8, 0), 'end': time(17, 0)},
            'wednesday': {'start': time(8, 0), 'end': time(17, 0)},
            'thursday': {'start': time(8, 0), 'end': time(17, 0)},
            'friday': {'start': time(8, 0), 'end': time(18, 0)},
            'saturday': {'start': time(9, 0), 'end': time(16, 0)},
            'sunday': {'start': None, 'end': None}  # Closed
        }
        
        self.slots_per_hour = 0.5  # Realistic: 30-minute buffer between appointments
        
        # Ad scheduling restrictions
        self.ad_cutoff_time = time(20, 0)  # No ads after 8 PM
        self.ad_start_time = time(7, 0)    # Start ads at 7 AM
        self.average_service_value = 125.0
        
        # Utilization thresholds (adjusted for low-booking scenario)
        self.optimal_utilization = 0.4    # 40% is great when starting with almost no bookings
        self.good_utilization = 0.2       # 20% is decent progress  
        self.low_utilization = 0.1        # 10% is critically low
        # Anything below 10% utilization triggers urgent promotions
        
        # Promo settings
        self.max_discount_percentage = 30
        self.max_daily_promo_budget = 100.0
        
        self.init_database()
        
    def init_database(self):
        """Initialize schedule filler database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Schedule analysis tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedule_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                total_slots INTEGER,
                booked_slots INTEGER,
                available_slots INTEGER,
                utilization_percentage REAL,
                utilization_level TEXT,
                time_blocks TEXT,
                revenue_potential REAL,
                days_until_date INTEGER,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(date, utilization_level)
            )
        ''')
        
        # Schedule filler promotions
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedule_filler_promos (
                id TEXT PRIMARY KEY,
                target_date DATE,
                urgency TEXT,
                discount_percentage INTEGER,
                discount_amount REAL,
                channels TEXT,
                message TEXT,
                cta_url TEXT,
                target_audience TEXT,
                budget_allocated REAL,
                slots_to_fill INTEGER,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(target_date, urgency)
            )
        ''')
        
        # Promo performance tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS promo_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id TEXT,
                channel TEXT,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                website_visits INTEGER DEFAULT 0,
                booking_page_visits INTEGER DEFAULT 0,
                bookings_generated INTEGER DEFAULT 0,
                revenue_generated REAL DEFAULT 0,
                cost REAL DEFAULT 0,
                ctr REAL DEFAULT 0,
                conversion_rate REAL DEFAULT 0,
                roi REAL DEFAULT 0,
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (promo_id) REFERENCES schedule_filler_promos (id),
                INDEX(promo_id, channel, date)
            )
        ''')
        
        # Calendar integration data
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS calendar_sync (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                hour INTEGER,
                is_available BOOLEAN,
                appointment_id TEXT,
                service_type TEXT,
                customer_name TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(date, hour)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("Schedule filler database initialized")
    
    def analyze_schedule_utilization(self, days_ahead: int = 7) -> List[ScheduleAnalysis]:
        """Analyze schedule utilization for upcoming days"""
        analyses = []
        
        for day_offset in range(1, days_ahead + 1):  # Start from tomorrow
            target_date = datetime.now() + timedelta(days=day_offset)
            
            # Skip Sundays (closed)
            if target_date.weekday() == 6:
                continue
            
            analysis = self._analyze_single_day(target_date)
            if analysis:
                analyses.append(analysis)
                self._store_schedule_analysis(analysis)
        
        logger.info(f"Analyzed schedule utilization for {len(analyses)} days")
        return analyses
    
    def _analyze_single_day(self, target_date: datetime) -> Optional[ScheduleAnalysis]:
        """Analyze utilization for a single day"""
        try:
            # Get day of week
            day_name = target_date.strftime('%A').lower()
            
            if day_name not in self.business_hours or not self.business_hours[day_name]['start']:
                return None  # Closed day
            
            # Calculate available time slots
            start_hour = self.business_hours[day_name]['start'].hour
            end_hour = self.business_hours[day_name]['end'].hour
            total_slots = (end_hour - start_hour) * self.slots_per_hour
            
            # Get booked appointments from calendar (simulate for now)
            booked_slots = self._get_booked_slots(target_date)
            
            available_slots = total_slots - booked_slots
            utilization_percentage = booked_slots / max(total_slots, 1)
            
            # Determine utilization level
            if utilization_percentage >= self.optimal_utilization:
                utilization_level = UtilizationLevel.OPTIMAL
            elif utilization_percentage >= self.good_utilization:
                utilization_level = UtilizationLevel.GOOD
            elif utilization_percentage >= self.low_utilization:
                utilization_level = UtilizationLevel.LOW
            else:
                utilization_level = UtilizationLevel.CRITICAL
            
            # Analyze time blocks
            time_blocks = self._analyze_time_blocks(target_date, start_hour, end_hour)
            
            # Calculate revenue potential
            revenue_potential = available_slots * self.average_service_value
            
            days_until_date = (target_date.date() - datetime.now().date()).days
            
            return ScheduleAnalysis(
                date=target_date,
                total_slots=total_slots,
                booked_slots=booked_slots,
                available_slots=available_slots,
                utilization_percentage=utilization_percentage,
                utilization_level=utilization_level,
                time_blocks=time_blocks,
                revenue_potential=revenue_potential,
                days_until_date=days_until_date
            )
            
        except Exception as e:
            logger.error(f"Failed to analyze schedule for {target_date.date()}: {e}")
            return None
    
    def _get_booked_slots(self, target_date: datetime) -> int:
        """Get number of booked slots for a date (realistic low booking scenario)"""
        try:
            # Realistic scenario: Very few bookings (like .10 open appointments this week)
            day_of_week = target_date.weekday()
            
            # Calculate total possible slots for the day
            day_name = target_date.strftime('%A').lower()
            if day_name not in self.business_hours or not self.business_hours[day_name]['start']:
                return 0  # Closed day
                
            start_hour = self.business_hours[day_name]['start'].hour
            end_hour = self.business_hours[day_name]['end'].hour
            total_possible_slots = int((end_hour - start_hour) * self.slots_per_hour)
            
            # Simulate very low booking rate (reflecting "0.10 open appointments this week")
            # Most days will have 0-1 bookings, occasionally 2
            if day_of_week in [4, 5]:  # Friday, Saturday - slightly better
                booked_slots = random.choice([0, 0, 1, 1, 2])
            else:  # Monday-Thursday - very low
                booked_slots = random.choice([0, 0, 0, 1])
            
            return min(booked_slots, total_possible_slots)
            
        except Exception as e:
            logger.error(f"Failed to get booked slots for {target_date}: {e}")
            return 0
    
    def _analyze_time_blocks(self, target_date: datetime, start_hour: int, end_hour: int) -> Dict[str, Dict[str, int]]:
        """Analyze availability by time blocks"""
        blocks = {
            'morning': {'start': start_hour, 'end': min(start_hour + 3, end_hour)},
            'afternoon': {'start': min(start_hour + 3, end_hour - 3), 'end': end_hour - 2},
            'evening': {'start': max(end_hour - 2, start_hour), 'end': end_hour}
        }
        
        time_blocks = {}
        
        for block_name, block_times in blocks.items():
            total_slots = (block_times['end'] - block_times['start']) * self.slots_per_hour
            # Simulate booked slots per block
            booked_slots = random.randint(0, total_slots)
            available_slots = max(0, total_slots - booked_slots)
            
            time_blocks[block_name] = {
                'total': total_slots,
                'booked': booked_slots,
                'available': available_slots
            }
        
        return time_blocks
    
    def _store_schedule_analysis(self, analysis: ScheduleAnalysis):
        """Store schedule analysis in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO schedule_analysis
            (date, total_slots, booked_slots, available_slots, utilization_percentage,
             utilization_level, time_blocks, revenue_potential, days_until_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            analysis.date.date(),
            analysis.total_slots,
            analysis.booked_slots,
            analysis.available_slots,
            analysis.utilization_percentage,
            analysis.utilization_level.value,
            json.dumps(analysis.time_blocks),
            analysis.revenue_potential,
            analysis.days_until_date
        ))
        
        conn.commit()
        conn.close()
    
    def generate_schedule_filler_promos(self, analyses: List[ScheduleAnalysis]) -> List[ScheduleFillerPromo]:
        """Generate targeted promotions for low utilization periods"""
        promos = []
        
        for analysis in analyses:
            # Only create promos for low/critical utilization
            if analysis.utilization_level in [UtilizationLevel.LOW, UtilizationLevel.CRITICAL]:
                promo = self._create_targeted_promo(analysis)
                if promo:
                    promos.append(promo)
                    self._store_schedule_filler_promo(promo)
        
        logger.info(f"Generated {len(promos)} schedule filler promotions")
        return promos
    
    def _create_targeted_promo(self, analysis: ScheduleAnalysis) -> Optional[ScheduleFillerPromo]:
        """Create targeted promotion for specific date/utilization"""
        try:
            # Determine urgency based on days until date
            if analysis.days_until_date <= 1:
                urgency = PromoUrgency.URGENT
                discount_percentage = 25 if analysis.utilization_level == UtilizationLevel.CRITICAL else 20
            elif analysis.days_until_date <= 2:
                urgency = PromoUrgency.MODERATE
                discount_percentage = 20 if analysis.utilization_level == UtilizationLevel.CRITICAL else 15
            else:
                urgency = PromoUrgency.GENTLE
                discount_percentage = 15 if analysis.utilization_level == UtilizationLevel.CRITICAL else 10
            
            # Calculate discount amount
            discount_amount = self.average_service_value * (discount_percentage / 100)
            
            # Select channels based on urgency
            channels = self._select_promo_channels(urgency, analysis.days_until_date)
            
            # Generate message
            message = self._generate_promo_message(analysis, urgency, discount_percentage)
            
            # Set target audience
            target_audience = self._define_target_audience(analysis)
            
            # Calculate budget
            budget_allocated = min(
                self.max_daily_promo_budget,
                analysis.available_slots * 20  # $20 per slot to fill
            )
            
            # Set expiration
            expires_at = analysis.date.replace(hour=23, minute=59, second=59)
            
            promo = ScheduleFillerPromo(
                id=str(uuid.uuid4()),
                target_date=analysis.date,
                urgency=urgency,
                discount_percentage=discount_percentage,
                discount_amount=discount_amount,
                channels=channels,
                message=message,
                cta_url=f"{self.booking_url}?promo={discount_percentage}off",
                target_audience=target_audience,
                budget_allocated=budget_allocated,
                slots_to_fill=min(analysis.available_slots, 4),  # Max 4 slots per promo
                created_at=datetime.now(),
                expires_at=expires_at
            )
            
            return promo
            
        except Exception as e:
            logger.error(f"Failed to create targeted promo: {e}")
            return None
    
    def _select_promo_channels(self, urgency: PromoUrgency, days_until: int) -> List[PromoChannel]:
        """Select appropriate channels based on urgency"""
        if urgency == PromoUrgency.URGENT:
            # Same day or next day - high visibility channels
            return [
                PromoChannel.SMS_BLAST,
                PromoChannel.EMAIL_BLAST,
                PromoChannel.FACEBOOK_ADS,
                PromoChannel.INSTAGRAM_STORY,
                PromoChannel.WEBSITE_BANNER
            ]
        elif urgency == PromoUrgency.MODERATE:
            # 1-2 days - moderate push
            return [
                PromoChannel.EMAIL_BLAST,
                PromoChannel.FACEBOOK_ADS,
                PromoChannel.GOOGLE_ADS,
                PromoChannel.INSTAGRAM_STORY
            ]
        else:  # GENTLE
            # 3+ days - gentle promotion
            return [
                PromoChannel.EMAIL_BLAST,
                PromoChannel.FACEBOOK_ADS,
                PromoChannel.GOOGLE_ADS
            ]
    
    def _generate_promo_message(self, analysis: ScheduleAnalysis, urgency: PromoUrgency, 
                              discount_percentage: int) -> str:
        """Generate compelling promo message"""
        
        date_str = analysis.date.strftime('%A, %B %d')
        
        # Iowa-specific messaging
        seasonal_context = self._get_seasonal_context()
        
        if urgency == PromoUrgency.URGENT:
            messages = [
                f"🚨 URGENT: {analysis.available_slots} slots open for {date_str}! {discount_percentage}% OFF auto detailing in Clinton, IA. {seasonal_context} Book at clintondetailing.com/booking",
                f"⚡ SAME DAY SPECIAL: {discount_percentage}% off your detail service! {analysis.available_slots} appointments available {date_str}. Clinton Auto Detailing → clintondetailing.com/booking",
                f"🔥 FLASH SALE: {discount_percentage}% OFF detail services {date_str}! {seasonal_context} {analysis.available_slots} slots left in Clinton, IA. Book now: clintondetailing.com/booking"
            ]
        elif urgency == PromoUrgency.MODERATE:
            messages = [
                f"💎 Special Deal Alert: {discount_percentage}% off detail services on {date_str}! {seasonal_context} Clinton Auto Detailing has {analysis.available_slots} openings. Book: clintondetailing.com/booking",
                f"🚗 {date_str} Openings: Save {discount_percentage}% on professional auto detailing in Clinton, IA! {analysis.available_slots} slots available. {seasonal_context} Book: clintondetailing.com/booking"
            ]
        else:  # GENTLE
            messages = [
                f"🌟 Planning Ahead? Save {discount_percentage}% on auto detailing {date_str}! {seasonal_context} Clinton Auto Detailing has {analysis.available_slots} openings. Book: clintondetailing.com/booking",
                f"📅 Book Early & Save: {discount_percentage}% off detail services {date_str}! {seasonal_context} {analysis.available_slots} appointments available in Clinton, IA. clintondetailing.com/booking"
            ]
        
        return random.choice(messages)
    
    def _get_seasonal_context(self) -> str:
        """Get Iowa-specific seasonal context"""
        month = datetime.now().month
        
        if month in [12, 1, 2]:  # Winter
            return "Protect your car from Iowa's harsh winter conditions!"
        elif month in [3, 4]:  # Spring
            return "Spring cleaning time - remove winter salt damage!"
        elif month in [5, 6, 7, 8]:  # Summer
            return "Keep your ride looking fresh for summer adventures!"
        elif month in [9, 10, 11]:  # Fall
            return "Prep your vehicle for Iowa's tough winter ahead!"
        
        return "Your car deserves the best care!"
    
    def _define_target_audience(self, analysis: ScheduleAnalysis) -> Dict[str, Any]:
        """Define target audience for promo"""
        return {
            'location': 'Clinton, IA',
            'radius_miles': self.service_area_radius,
            'interests': ['car_care', 'auto_maintenance', 'local_business'],
            'demographics': {
                'age_range': '25-65',
                'income_level': 'middle_to_upper'
            },
            'behavioral': {
                'car_owners': True,
                'service_frequency': 'quarterly',
                'price_sensitive': analysis.utilization_level == UtilizationLevel.CRITICAL
            },
            'exclude': ['recent_customers_30_days']  # Don't target recent customers
        }
    
    def _store_schedule_filler_promo(self, promo: ScheduleFillerPromo):
        """Store schedule filler promo in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO schedule_filler_promos
            (id, target_date, urgency, discount_percentage, discount_amount,
             channels, message, cta_url, target_audience, budget_allocated,
             slots_to_fill, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            promo.id,
            promo.target_date.date(),
            promo.urgency.value,
            promo.discount_percentage,
            promo.discount_amount,
            json.dumps([c.value for c in promo.channels]),
            promo.message,
            promo.cta_url,
            json.dumps(promo.target_audience),
            promo.budget_allocated,
            promo.slots_to_fill,
            promo.expires_at
        ))
        
        conn.commit()
        conn.close()
    
    def launch_active_promos(self) -> int:
        """Launch all active promotions across channels"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get promotions that should be active now
        cursor.execute('''
            SELECT * FROM schedule_filler_promos
            WHERE target_date >= date('now')
            AND expires_at > datetime('now')
            AND created_at > datetime('now', '-24 hours')
        ''')
        
        active_promos = cursor.fetchall()
        launched_count = 0
        
        for promo_data in active_promos:
            try:
                promo_id = promo_data[0]
                channels = json.loads(promo_data[5])
                message = promo_data[6]
                cta_url = promo_data[7]
                budget = promo_data[9]
                
                # Launch across each channel
                for channel_name in channels:
                    channel = PromoChannel(channel_name)
                    success = self._launch_channel_promo(promo_id, channel, message, cta_url, budget)
                    if success:
                        launched_count += 1
                
            except Exception as e:
                logger.error(f"Failed to launch promo {promo_data[0]}: {e}")
        
        conn.close()
        
        logger.info(f"Launched {launched_count} promotional campaigns")
        return launched_count
    
    def _launch_channel_promo(self, promo_id: str, channel: PromoChannel, 
                            message: str, cta_url: str, budget: float) -> bool:
        """Launch promo on specific channel (respects 8 PM ad cutoff)"""
        try:
            # Check if it's after 8 PM - don't run ads
            current_time = datetime.now().time()
            if current_time >= self.ad_cutoff_time or current_time < self.ad_start_time:
                if channel in [PromoChannel.FACEBOOK_ADS, PromoChannel.GOOGLE_ADS, PromoChannel.INSTAGRAM_STORY]:
                    logger.info(f"Skipping {channel.value} - outside ad hours (7 AM - 8 PM)")
                    return False
            if channel == PromoChannel.FACEBOOK_ADS:
                # Launch Facebook ad campaign
                logger.info(f"FACEBOOK AD: {message[:50]}... → {cta_url}")
                self._simulate_promo_performance(promo_id, channel, budget * 0.4)
                return True
            
            elif channel == PromoChannel.GOOGLE_ADS:
                # Launch Google ads campaign
                logger.info(f"GOOGLE AD: {message[:50]}... → {cta_url}")
                self._simulate_promo_performance(promo_id, channel, budget * 0.3)
                return True
            
            elif channel == PromoChannel.EMAIL_BLAST:
                # Send email blast to customer list
                logger.info(f"EMAIL BLAST: {message[:50]}... → {cta_url}")
                self._simulate_promo_performance(promo_id, channel, budget * 0.1)
                return True
            
            elif channel == PromoChannel.SMS_BLAST:
                # Send SMS blast to opted-in customers
                logger.info(f"SMS BLAST: {message[:50]}... → {cta_url}")
                self._simulate_promo_performance(promo_id, channel, budget * 0.1)
                return True
            
            elif channel == PromoChannel.INSTAGRAM_STORY:
                # Post Instagram story
                logger.info(f"INSTAGRAM STORY: {message[:50]}... → {cta_url}")
                self._simulate_promo_performance(promo_id, channel, budget * 0.05)
                return True
            
            elif channel == PromoChannel.WEBSITE_BANNER:
                # Add banner to website
                logger.info(f"WEBSITE BANNER: {message[:50]}... → {cta_url}")
                self._simulate_promo_performance(promo_id, channel, 0)  # No cost
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to launch {channel.value} promo: {e}")
            return False
    
    def _simulate_promo_performance(self, promo_id: str, channel: PromoChannel, cost: float):
        """Simulate promotional performance (replace with real tracking)"""
        # Simulate realistic performance metrics
        if channel == PromoChannel.FACEBOOK_ADS:
            impressions = random.randint(500, 2000)
            ctr = random.uniform(0.02, 0.06)
            conversion_rate = random.uniform(0.05, 0.15)
        elif channel == PromoChannel.GOOGLE_ADS:
            impressions = random.randint(300, 1000)
            ctr = random.uniform(0.03, 0.08)
            conversion_rate = random.uniform(0.08, 0.20)
        elif channel in [PromoChannel.EMAIL_BLAST, PromoChannel.SMS_BLAST]:
            impressions = random.randint(100, 500)
            ctr = random.uniform(0.10, 0.25)
            conversion_rate = random.uniform(0.03, 0.10)
        else:
            impressions = random.randint(50, 200)
            ctr = random.uniform(0.05, 0.15)
            conversion_rate = random.uniform(0.02, 0.08)
        
        clicks = int(impressions * ctr)
        website_visits = int(clicks * 0.8)  # 80% actually visit
        booking_page_visits = int(website_visits * 0.6)  # 60% reach booking page
        bookings_generated = int(booking_page_visits * conversion_rate)
        revenue_generated = bookings_generated * self.average_service_value * 0.8  # Account for discount
        
        roi = (revenue_generated - cost) / max(cost, 1) if cost > 0 else 0
        
        # Store performance
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO promo_performance
            (promo_id, channel, impressions, clicks, website_visits,
             booking_page_visits, bookings_generated, revenue_generated,
             cost, ctr, conversion_rate, roi, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            promo_id, channel.value, impressions, clicks, website_visits,
            booking_page_visits, bookings_generated, revenue_generated,
            cost, ctr, conversion_rate, roi, datetime.now().date()
        ))
        
        conn.commit()
        conn.close()
    
    def run_schedule_filler_cycle(self) -> Dict[str, Any]:
        """Run complete schedule filler cycle"""
        logger.info("Starting schedule filler optimization cycle")
        
        try:
            # Analyze schedule utilization
            analyses = self.analyze_schedule_utilization()
            
            # Generate targeted promos for low utilization periods
            promos = self.generate_schedule_filler_promos(analyses)
            
            # Launch active promotions
            launched_campaigns = self.launch_active_promos()
            
            # Calculate impact
            low_utilization_days = len([a for a in analyses 
                                      if a.utilization_level in [UtilizationLevel.LOW, UtilizationLevel.CRITICAL]])
            
            potential_revenue = sum(a.revenue_potential for a in analyses 
                                  if a.utilization_level in [UtilizationLevel.LOW, UtilizationLevel.CRITICAL])
            
            cycle_summary = {
                'cycle_id': str(int(time.time())),
                'days_analyzed': len(analyses),
                'low_utilization_days': low_utilization_days,
                'potential_revenue_at_risk': potential_revenue,
                'promos_generated': len(promos),
                'campaigns_launched': launched_campaigns,
                'booking_url': self.booking_url,
                'utilization_breakdown': {
                    level.value: len([a for a in analyses if a.utilization_level == level])
                    for level in UtilizationLevel
                },
                'average_utilization': sum(a.utilization_percentage for a in analyses) / max(len(analyses), 1),
                'timestamp': datetime.now().isoformat()
            }
            
            logger.info(f"Schedule filler cycle completed - {len(promos)} promos generated, {launched_campaigns} campaigns launched")
            
            return cycle_summary
            
        except Exception as e:
            logger.error(f"Schedule filler cycle failed: {e}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}

# Clinton Auto Detailing Schedule Filler Engine
clinton_schedule_filler = ScheduleFillerEngine()

def test_schedule_filler_engine():
    """Test the schedule filler engine"""
    print("Testing Schedule Filler Engine for Clinton Auto Detailing...")
    print(f"🎯 Target: Drive bookings to {clinton_schedule_filler.booking_url}")
    
    # Run complete cycle
    result = clinton_schedule_filler.run_schedule_filler_cycle()
    
    print(f"\\nSchedule Filler Results:")
    print(f"- Days Analyzed: {result.get('days_analyzed', 0)}")
    print(f"- Low Utilization Days: {result.get('low_utilization_days', 0)}")
    print(f"- Revenue at Risk: ${result.get('potential_revenue_at_risk', 0):.2f}")
    print(f"- Promos Generated: {result.get('promos_generated', 0)}")
    print(f"- Campaigns Launched: {result.get('campaigns_launched', 0)}")
    print(f"- Average Utilization: {result.get('average_utilization', 0):.1%}")
    
    if 'utilization_breakdown' in result:
        print(f"\\nUtilization Breakdown:")
        for level, count in result['utilization_breakdown'].items():
            if count > 0:
                print(f"- {level.replace('_', ' ').title()}: {count} days")
    
    print(f"\\n🚀 All promotions drive traffic to: {result.get('booking_url')}")
    
    return result

if __name__ == "__main__":
    test_schedule_filler_engine()