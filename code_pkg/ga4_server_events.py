"""
GA4 Server Events - Day 1-2 Hardening
Implements GA4 Measurement Protocol for server-side event tracking
"""

import os
import json
import requests
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import hashlib
import uuid

logger = logging.getLogger(__name__)

@dataclass
class GA4Event:
    """GA4 event structure"""
    name: str
    parameters: Dict[str, Any]
    
@dataclass 
class GA4User:
    """GA4 user identification"""
    client_id: str
    user_id: Optional[str] = None
    
class GA4ServerEvents:
    def __init__(self):
        self.measurement_id = os.getenv('GA4_MEASUREMENT_ID', 'G-XXXXXXXXXX')
        self.api_secret = os.getenv('GA4_API_SECRET', 'your_api_secret')
        self.base_url = "https://www.google-analytics.com/mp/collect"
        
        # Validate configuration
        if not self.measurement_id or not self.api_secret:
            logger.warning("GA4 credentials not configured. Set GA4_MEASUREMENT_ID and GA4_API_SECRET")
    
    def _generate_client_id(self, customer_email: str = None) -> str:
        """Generate deterministic client ID for consistent user tracking"""
        if customer_email:
            # Use email hash for returning customers
            return hashlib.md5(customer_email.encode()).hexdigest()
        else:
            # Generate random client ID for anonymous users
            return str(uuid.uuid4())
    
    def _send_event(self, user: GA4User, events: List[GA4Event], debug: bool = False) -> bool:
        """Send events to GA4 Measurement Protocol"""
        try:
            endpoint = self.base_url
            if debug:
                endpoint = "https://www.google-analytics.com/debug/mp/collect"
            
            payload = {
                'client_id': user.client_id,
                'events': [{'name': event.name, 'parameters': event.parameters} for event in events]
            }
            
            if user.user_id:
                payload['user_id'] = user.user_id
            
            params = {
                'measurement_id': self.measurement_id,
                'api_secret': self.api_secret
            }
            
            response = requests.post(
                endpoint,
                params=params,
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'}
            )
            
            if debug:
                logger.info(f"GA4 Debug Response: {response.text}")
            
            if response.status_code == 204:
                logger.info(f"GA4 event sent successfully: {[e.name for e in events]}")
                return True
            else:
                logger.error(f"GA4 event failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"GA4 event error: {e}")
            return False
    
    def track_lead_generated(self, 
                           customer_email: str = None,
                           source: str = "unknown",
                           medium: str = "unknown", 
                           campaign: str = "unknown",
                           lead_value: float = 0,
                           service_type: str = "auto_detailing",
                           **kwargs) -> bool:
        """Track lead generation event"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        event = GA4Event(
            name="generate_lead",
            parameters={
                'source': source,
                'medium': medium,
                'campaign': campaign,
                'value': lead_value,
                'currency': 'USD',
                'lead_type': service_type,
                'business_type': 'auto_detailing',
                'location': 'Clinton_IA',
                **kwargs
            }
        )
        
        return self._send_event(user, [event])
    
    def track_appointment_booked(self,
                               customer_email: str = None,
                               booking_value: float = 0,
                               service_type: str = "auto_detailing",
                               appointment_date: str = None,
                               source: str = "unknown",
                               **kwargs) -> bool:
        """Track appointment booking event"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        event = GA4Event(
            name="book_appointment",
            parameters={
                'value': booking_value,
                'currency': 'USD',
                'service_type': service_type,
                'appointment_date': appointment_date or datetime.now().strftime('%Y-%m-%d'),
                'source': source,
                'business_type': 'auto_detailing',
                'location': 'Clinton_IA',
                **kwargs
            }
        )
        
        return self._send_event(user, [event])
    
    def track_purchase_completed(self,
                               customer_email: str = None,
                               transaction_id: str = None,
                               value: float = 0,
                               items: List[Dict] = None,
                               payment_method: str = "unknown",
                               **kwargs) -> bool:
        """Track purchase completion event"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        parameters = {
            'transaction_id': transaction_id or str(uuid.uuid4()),
            'value': value,
            'currency': 'USD',
            'payment_method': payment_method,
            'business_type': 'auto_detailing',
            'location': 'Clinton_MS',
            **kwargs
        }
        
        # Add items if provided
        if items:
            parameters['items'] = items
        else:
            # Default item for auto detailing service
            parameters['items'] = [{
                'item_id': 'auto_detail_service',
                'item_name': 'Auto Detailing Service',
                'category': 'automotive_services',
                'quantity': 1,
                'price': value
            }]
        
        event = GA4Event(name="purchase", parameters=parameters)
        return self._send_event(user, [event])
    
    def track_subscription_started(self,
                                 customer_email: str = None,
                                 subscription_id: str = None,
                                 value: float = 0,
                                 subscription_type: str = "monthly",
                                 **kwargs) -> bool:
        """Track subscription start event"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        event = GA4Event(
            name="subscribe",
            parameters={
                'subscription_id': subscription_id or str(uuid.uuid4()),
                'value': value,
                'currency': 'USD',
                'subscription_type': subscription_type,
                'business_type': 'auto_detailing',
                'location': 'Clinton_IA',
                **kwargs
            }
        )
        
        return self._send_event(user, [event])
    
    def track_service_completed(self,
                              customer_email: str = None,
                              service_value: float = 0,
                              service_type: str = "auto_detailing",
                              duration_minutes: int = None,
                              satisfaction_rating: int = None,
                              **kwargs) -> bool:
        """Track service completion event"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        parameters = {
            'value': service_value,
            'currency': 'USD',
            'service_type': service_type,
            'business_type': 'auto_detailing',
            'location': 'Clinton_MS',
            **kwargs
        }
        
        if duration_minutes:
            parameters['service_duration'] = duration_minutes
            
        if satisfaction_rating:
            parameters['satisfaction_rating'] = satisfaction_rating
        
        event = GA4Event(name="service_completed", parameters=parameters)
        return self._send_event(user, [event])
    
    def track_email_engagement(self,
                             customer_email: str = None,
                             engagement_type: str = "open",  # open, click, unsubscribe
                             campaign_name: str = "unknown",
                             email_template: str = "unknown",
                             **kwargs) -> bool:
        """Track email engagement events"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        event = GA4Event(
            name=f"email_{engagement_type}",
            parameters={
                'campaign_name': campaign_name,
                'email_template': email_template,
                'medium': 'email',
                'source': 'email_system',
                'business_type': 'auto_detailing',
                **kwargs
            }
        )
        
        return self._send_event(user, [event])
    
    def track_ad_interaction(self,
                           customer_email: str = None,
                           ad_platform: str = "unknown",  # facebook, google, instagram
                           ad_id: str = None,
                           campaign_id: str = None,
                           interaction_type: str = "click",
                           **kwargs) -> bool:
        """Track ad interaction events"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        event = GA4Event(
            name=f"ad_{interaction_type}",
            parameters={
                'source': ad_platform,
                'medium': 'paid_social' if ad_platform in ['facebook', 'instagram'] else 'paid_search',
                'ad_id': ad_id,
                'campaign_id': campaign_id,
                'business_type': 'auto_detailing',
                **kwargs
            }
        )
        
        return self._send_event(user, [event])
    
    def track_custom_event(self,
                         event_name: str,
                         customer_email: str = None,
                         parameters: Dict[str, Any] = None,
                         **kwargs) -> bool:
        """Track custom business event"""
        
        user = GA4User(
            client_id=self._generate_client_id(customer_email),
            user_id=customer_email
        )
        
        event_params = {
            'business_type': 'auto_detailing',
            'location': 'Clinton_MS',
            **(parameters or {}),
            **kwargs
        }
        
        event = GA4Event(name=event_name, parameters=event_params)
        return self._send_event(user, [event])

class ClintonAutoDetailingGA4:
    """Clinton Auto Detailing specific GA4 tracking"""
    
    def __init__(self):
        self.ga4 = GA4ServerEvents()
        self.business_name = "Clinton Auto Detailing"
        self.location = "Clinton, MS"
    
    def track_square_payment(self, square_payment_data: Dict) -> bool:
        """Track Square payment as GA4 purchase event"""
        customer_email = square_payment_data.get('customer_email')
        amount = square_payment_data.get('amount', 0)
        transaction_id = square_payment_data.get('payment_id')
        service_type = square_payment_data.get('service_type', 'auto_detailing')
        
        return self.ga4.track_purchase_completed(
            customer_email=customer_email,
            transaction_id=transaction_id,
            value=amount,
            payment_method='square',
            source='square_pos',
            medium='in_person',
            service_category=service_type
        )
    
    def track_facebook_lead(self, facebook_lead_data: Dict) -> bool:
        """Track Facebook lead as GA4 lead generation event"""
        customer_email = facebook_lead_data.get('email')
        ad_id = facebook_lead_data.get('ad_id')
        campaign_id = facebook_lead_data.get('campaign_id')
        
        return self.ga4.track_lead_generated(
            customer_email=customer_email,
            source='facebook',
            medium='paid_social',
            campaign=campaign_id,
            lead_value=50,  # Estimated lead value
            ad_id=ad_id
        )
    
    def track_appointment_booking(self, booking_data: Dict) -> bool:
        """Track appointment booking"""
        customer_email = booking_data.get('customer_email')
        service_type = booking_data.get('service_type', 'auto_detailing')
        booking_value = booking_data.get('estimated_value', 0)
        source = booking_data.get('source', 'unknown')
        
        return self.ga4.track_appointment_booked(
            customer_email=customer_email,
            booking_value=booking_value,
            service_type=service_type,
            source=source,
            medium='organic' if source == 'website' else 'referral'
        )
    
    def track_service_delivery(self, service_data: Dict) -> bool:
        """Track completed auto detailing service"""
        customer_email = service_data.get('customer_email')
        service_value = service_data.get('amount', 0)
        service_type = service_data.get('service_type', 'full_detail')
        duration = service_data.get('duration_minutes')
        rating = service_data.get('customer_rating')
        
        return self.ga4.track_service_completed(
            customer_email=customer_email,
            service_value=service_value,
            service_type=service_type,
            duration_minutes=duration,
            satisfaction_rating=rating
        )
    
    def track_email_campaign(self, email_data: Dict) -> bool:
        """Track email campaign engagement"""
        customer_email = email_data.get('customer_email')
        engagement = email_data.get('engagement_type', 'open')
        campaign = email_data.get('campaign_name', 'unknown')
        template = email_data.get('template_name', 'unknown')
        
        return self.ga4.track_email_engagement(
            customer_email=customer_email,
            engagement_type=engagement,
            campaign_name=campaign,
            email_template=template
        )

# Global instance for Clinton Auto Detailing
clinton_ga4 = ClintonAutoDetailingGA4()

def test_ga4_events():
    """Test GA4 event tracking"""
    print("Testing GA4 Server Events...")
    
    # Test lead generation
    success = clinton_ga4.track_facebook_lead({
        'email': 'test@clintondetailing.com',
        'ad_id': 'test_ad_123',
        'campaign_id': 'clinton_auto_campaign'
    })
    print(f"Lead tracking: {'Success' if success else 'Failed'}")
    
    # Test appointment booking
    success = clinton_ga4.track_appointment_booking({
        'customer_email': 'test@clintondetailing.com',
        'service_type': 'full_detail',
        'estimated_value': 150,
        'source': 'website'
    })
    print(f"Appointment tracking: {'Success' if success else 'Failed'}")
    
    # Test purchase
    success = clinton_ga4.track_square_payment({
        'customer_email': 'test@clintondetailing.com',
        'amount': 125.00,
        'payment_id': 'square_payment_123',
        'service_type': 'interior_exterior_detail'
    })
    print(f"Purchase tracking: {'Success' if success else 'Failed'}")

if __name__ == "__main__":
    test_ga4_events()