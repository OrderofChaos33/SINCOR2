"""
Square Calendar Integration - Real-time booking data
Connects directly to Square API for actual appointment scheduling
"""

import os
import json
import logging
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Any
import requests
import sqlite3

logger = logging.getLogger(__name__)

class SquareCalendarIntegration:
    def __init__(self):
        self.access_token = os.getenv('SQUARE_ACCESS_TOKEN', 'EAAAl4HXPrCVLFcZd7UOCyV4_e_q201jW2RbZa_uZObQ7IIBXPyuc497vKBBvUEG')
        self.location_id = os.getenv('SQUARE_LOCATION_ID', 'HXW7T74QF2EF4')
        self.environment = os.getenv('SQUARE_ENVIRONMENT', 'sandbox')
        
        if self.environment == 'production':
            self.base_url = "https://connect.squareup.com"
        else:
            self.base_url = "https://connect.squareupsandbox.com"
        
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Square-Version': '2023-10-18'
        }
        
        # Clinton Auto Detailing business hours
        self.business_hours = {
            'monday': {'start': time(8, 0), 'end': time(17, 0)},
            'tuesday': {'start': time(8, 0), 'end': time(17, 0)},
            'wednesday': {'start': time(8, 0), 'end': time(17, 0)},
            'thursday': {'start': time(8, 0), 'end': time(17, 0)},
            'friday': {'start': time(8, 0), 'end': time(18, 0)},
            'saturday': {'start': time(9, 0), 'end': time(16, 0)},
            'sunday': {'start': None, 'end': None}  # Closed
        }
    
    def get_bookings_for_date(self, target_date: datetime) -> List[Dict]:
        """Get all bookings for a specific date from Square"""
        try:
            start_time = target_date.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
            end_time = target_date.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
            
            url = f"{self.base_url}/v2/bookings"
            params = {
                'location_id': self.location_id,
                'start_at_min': start_time,
                'start_at_max': end_time
            }
            
            response = requests.get(url, headers=self.headers, params=params)
            
            if response.status_code == 200:
                data = response.json()
                bookings = data.get('bookings', [])
                
                logger.info(f"Retrieved {len(bookings)} bookings for {target_date.date()}")
                return bookings
            else:
                logger.error(f"Square API error: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Failed to get bookings for {target_date.date()}: {e}")
            return []
    
    def get_available_time_slots(self, target_date: datetime) -> List[Dict]:
        """Get available appointment slots for a date"""
        try:
            day_name = target_date.strftime('%A').lower()
            
            # Check if business is open
            if day_name not in self.business_hours or not self.business_hours[day_name]['start']:
                return []  # Closed day
            
            start_hour = self.business_hours[day_name]['start'].hour
            end_hour = self.business_hours[day_name]['end'].hour
            
            # Get existing bookings
            existing_bookings = self.get_bookings_for_date(target_date)
            
            # Generate all possible time slots (hourly)
            available_slots = []
            
            for hour in range(start_hour, end_hour):
                slot_time = target_date.replace(hour=hour, minute=0, second=0)
                
                # Check if this slot is already booked
                is_booked = False
                for booking in existing_bookings:
                    booking_start = datetime.fromisoformat(booking['appointment_segments'][0]['start_at'].replace('Z', '+00:00'))
                    if booking_start.hour == hour:
                        is_booked = True
                        break
                
                if not is_booked:
                    available_slots.append({
                        'datetime': slot_time,
                        'display_time': slot_time.strftime('%I:%M %p'),
                        'available': True
                    })
            
            return available_slots
            
        except Exception as e:
            logger.error(f"Failed to get available slots for {target_date.date()}: {e}")
            return []
    
    def calculate_real_utilization(self, target_date: datetime) -> Dict[str, Any]:
        """Calculate actual calendar utilization for a date"""
        try:
            day_name = target_date.strftime('%A').lower()
            
            if day_name not in self.business_hours or not self.business_hours[day_name]['start']:
                return {
                    'date': target_date.date(),
                    'total_slots': 0,
                    'booked_slots': 0,
                    'available_slots': 0,
                    'utilization_percentage': 0.0,
                    'is_open': False
                }
            
            start_hour = self.business_hours[day_name]['start'].hour
            end_hour = self.business_hours[day_name]['end'].hour
            total_slots = end_hour - start_hour
            
            # Get actual bookings from Square
            bookings = self.get_bookings_for_date(target_date)
            booked_slots = len(bookings)
            available_slots = total_slots - booked_slots
            utilization_percentage = booked_slots / max(total_slots, 1)
            
            return {
                'date': target_date.date(),
                'total_slots': total_slots,
                'booked_slots': booked_slots,
                'available_slots': available_slots,
                'utilization_percentage': utilization_percentage,
                'is_open': True,
                'bookings': bookings,
                'revenue_potential': available_slots * 125.0  # Average service value
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate utilization for {target_date.date()}: {e}")
            return {
                'date': target_date.date(),
                'total_slots': 0,
                'booked_slots': 0,
                'available_slots': 0,
                'utilization_percentage': 0.0,
                'is_open': False,
                'error': str(e)
            }
    
    def analyze_weekly_utilization(self) -> Dict[str, Any]:
        """Analyze utilization for the next 7 days"""
        weekly_analysis = {
            'period': 'next_7_days',
            'analyzed_at': datetime.now().isoformat(),
            'days': [],
            'summary': {
                'total_available_slots': 0,
                'total_booked_slots': 0,
                'average_utilization': 0.0,
                'low_utilization_days': 0,
                'total_revenue_potential': 0.0
            }
        }
        
        for day_offset in range(1, 8):  # Next 7 days
            target_date = datetime.now() + timedelta(days=day_offset)
            day_analysis = self.calculate_real_utilization(target_date)
            weekly_analysis['days'].append(day_analysis)
            
            if day_analysis['is_open']:
                weekly_analysis['summary']['total_available_slots'] += day_analysis['available_slots']
                weekly_analysis['summary']['total_booked_slots'] += day_analysis['booked_slots']
                weekly_analysis['summary']['total_revenue_potential'] += day_analysis.get('revenue_potential', 0)
                
                # Consider <20% utilization as "low" for Clinton Auto Detailing
                if day_analysis['utilization_percentage'] < 0.2:
                    weekly_analysis['summary']['low_utilization_days'] += 1
        
        # Calculate average utilization
        open_days = [d for d in weekly_analysis['days'] if d['is_open']]
        if open_days:
            total_utilization = sum(d['utilization_percentage'] for d in open_days)
            weekly_analysis['summary']['average_utilization'] = total_utilization / len(open_days)
        
        return weekly_analysis
    
    def get_urgent_promotion_targets(self) -> List[Dict]:
        """Get days that need urgent promotional attention"""
        urgent_targets = []
        
        weekly_analysis = self.analyze_weekly_utilization()
        
        for day_data in weekly_analysis['days']:
            if not day_data['is_open']:
                continue
            
            # Trigger urgent promotions for very low utilization
            if day_data['utilization_percentage'] < 0.1:  # Less than 10% booked
                urgency = 'CRITICAL'
                discount = 30
            elif day_data['utilization_percentage'] < 0.2:  # Less than 20% booked
                urgency = 'HIGH'
                discount = 25
            else:
                continue  # Not urgent
            
            days_until = (day_data['date'] - datetime.now().date()).days
            
            urgent_targets.append({
                'date': day_data['date'],
                'days_until': days_until,
                'available_slots': day_data['available_slots'],
                'utilization_percentage': day_data['utilization_percentage'],
                'urgency': urgency,
                'recommended_discount': discount,
                'revenue_potential': day_data.get('revenue_potential', 0),
                'message': f"🚨 {urgency}: Only {day_data['booked_slots']} booking(s) on {day_data['date'].strftime('%A, %B %d')}! {day_data['available_slots']} slots available."
            })
        
        return urgent_targets
    
    def create_booking_url_with_tracking(self, promo_code: str = None, utm_source: str = None) -> str:
        """Create optimized booking URL with tracking parameters"""
        base_url = "https://clintondetailing.com/booking"
        
        params = []
        
        if promo_code:
            params.append(f"promo={promo_code}")
        
        if utm_source:
            params.append(f"utm_source={utm_source}")
            params.append(f"utm_medium=promotion")
            params.append(f"utm_campaign=schedule_filler")
        
        # Add Square-specific parameters if needed
        params.append(f"location_id={self.location_id}")
        
        if params:
            return f"{base_url}?{'&'.join(params)}"
        
        return base_url

# Integration with Schedule Filler Engine
def integrate_square_calendar_with_schedule_filler():
    """Update Schedule Filler to use real Square calendar data"""
    
    # Update the _get_booked_slots method in ScheduleFillerEngine
    square_calendar = SquareCalendarIntegration()
    
    def get_real_booked_slots(target_date: datetime) -> int:
        """Get actual booked slots from Square API"""
        utilization_data = square_calendar.calculate_real_utilization(target_date)
        return utilization_data['booked_slots']
    
    return get_real_booked_slots

# Test function
def test_square_calendar_integration():
    """Test Square calendar integration"""
    print("🗓️ Testing Square Calendar Integration for Clinton Auto Detailing...")
    
    integration = SquareCalendarIntegration()
    
    # Test weekly analysis
    weekly_data = integration.analyze_weekly_utilization()
    
    print(f"📊 Weekly Analysis:")
    print(f"- Total Available Slots: {weekly_data['summary']['total_available_slots']}")
    print(f"- Total Booked Slots: {weekly_data['summary']['total_booked_slots']}")
    print(f"- Average Utilization: {weekly_data['summary']['average_utilization']:.1%}")
    print(f"- Revenue Potential: ${weekly_data['summary']['total_revenue_potential']:.2f}")
    print(f"- Low Utilization Days: {weekly_data['summary']['low_utilization_days']}")
    
    # Test urgent promotion targets
    urgent_targets = integration.get_urgent_promotion_targets()
    
    if urgent_targets:
        print(f"\\n🚨 Urgent Promotion Targets ({len(urgent_targets)} days):")
        for target in urgent_targets[:3]:  # Show first 3
            print(f"- {target['date']}: {target['urgency']} - {target['recommended_discount']}% off - {target['available_slots']} slots")
    else:
        print("\\n✅ No urgent promotion targets (good utilization!)")
    
    return weekly_data

if __name__ == "__main__":
    test_square_calendar_integration()