#!/usr/bin/env python3
"""
SINCOR Syndicator Agent - Advanced Content Creation & Distribution System

This agent handles:
- Safe business discovery and scraping with obscuring methods
- Automated ad creation for service businesses  
- Content syndication across platforms
- Integration with Canva API for visual assets
- 24/7 automated pipeline operations
"""

import os
import sys
import json
import time
import random
import logging
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass
import yaml
from jinja2 import Environment, FileSystemLoader
import pandas as pd
import sqlite3
from urllib.parse import quote, urlencode
import hashlib
import threading
from collections import deque

# Safe scraping imports
import fake_useragent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

@dataclass
class Business:
    """Represents a discovered service business."""
    name: str
    business_type: str
    location: str
    phone: str = None
    website: str = None
    address: str = None
    rating: float = None
    review_count: int = None
    discovery_source: str = None
    last_contacted: datetime = None
    contact_status: str = "new"  # new, contacted, responded, converted
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

@dataclass
class ContentAsset:
    """Represents generated marketing content."""
    asset_id: str
    business_id: str
    asset_type: str  # ad, post, video, image
    content: str
    title: str
    call_to_action: str
    hashtags: List[str]
    platforms: List[str]
    created_at: datetime = None
    scheduled_for: datetime = None
    status: str = "generated"  # generated, scheduled, published, failed
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

class SafeScraper:
    """Safe web scraping with anti-detection measures."""
    
    def __init__(self):
        self.session = requests.Session()
        self.user_agents = fake_useragent.UserAgent()
        self.setup_session()
        self.request_delays = deque(maxlen=100)
        self.min_delay = 2.0  # minimum seconds between requests
        self.max_delay = 8.0  # maximum seconds between requests
        
    def setup_session(self):
        """Configure session with anti-detection measures."""
        # Retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Default headers
        self.session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1'
        })
    
    def get_safe_headers(self) -> Dict[str, str]:
        """Generate randomized headers for each request."""
        return {
            'User-Agent': self.user_agents.random,
            'Accept': random.choice([
                'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            ]),
            'Accept-Language': random.choice([
                'en-US,en;q=0.9',
                'en-US,en;q=0.8,es;q=0.6',
                'en-GB,en;q=0.9,en-US;q=0.8'
            ])
        }
    
    def smart_delay(self):
        """Implement intelligent request delays."""
        now = time.time()
        self.request_delays.append(now)
        
        if len(self.request_delays) > 1:
            recent_requests = [t for t in self.request_delays if now - t < 60]
            if len(recent_requests) > 10:  # Too many requests in last minute
                delay = random.uniform(self.max_delay * 2, self.max_delay * 4)
            else:
                delay = random.uniform(self.min_delay, self.max_delay)
        else:
            delay = random.uniform(self.min_delay, self.max_delay)
        
        time.sleep(delay)
    
    def safe_request(self, url: str, params: Dict = None) -> Optional[requests.Response]:
        """Make a safe HTTP request with anti-detection measures."""
        try:
            self.smart_delay()
            headers = self.get_safe_headers()
            
            response = self.session.get(
                url, 
                params=params, 
                headers=headers,
                timeout=30,
                allow_redirects=True
            )
            response.raise_for_status()
            return response
            
        except Exception as e:
            logging.error(f"Safe request failed for {url}: {e}")
            return None

class BusinessDiscoverer:
    """Discovers service businesses using safe scraping methods."""
    
    def __init__(self, google_api_key: str):
        self.google_api_key = google_api_key
        self.scraper = SafeScraper()
        self.service_types = [
            'auto detailing', 'car wash', 'mobile car detailing',
            'hvac repair', 'air conditioning repair', 'heating repair',
            'plumbing', 'emergency plumber', 'drain cleaning',
            'electrician', 'electrical repair', 'electrical contractor',
            'carpet cleaning', 'upholstery cleaning',
            'window cleaning', 'pressure washing',
            'landscaping', 'lawn care', 'tree service',
            'roofing', 'roof repair', 'gutter cleaning',
            'pest control', 'exterminator',
            'handyman', 'home repair'
        ]
        
    def discover_local_businesses(self, location: str, service_type: str, 
                                radius: int = 25000) -> List[Business]:
        """Discover local service businesses using Google Places API."""
        businesses = []
        
        try:
            # Use Google Places API for safe, legitimate business discovery
            url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            
            params = {
                'key': self.google_api_key,
                'location': location,
                'radius': radius,
                'type': 'establishment',
                'keyword': service_type
            }
            
            response = self.scraper.safe_request(url, params)
            if not response:
                return businesses
                
            data = response.json()
            
            for place in data.get('results', []):
                try:
                    business = Business(
                        name=place.get('name', ''),
                        business_type=service_type,
                        location=location,
                        rating=place.get('rating'),
                        discovery_source='google_places'
                    )
                    
                    # Get additional details if place_id available
                    if place.get('place_id'):
                        details = self.get_business_details(place['place_id'])
                        if details:
                            business.phone = details.get('phone')
                            business.website = details.get('website')
                            business.address = details.get('address')
                    
                    businesses.append(business)
                    
                except Exception as e:
                    logging.error(f"Error processing business data: {e}")
                    continue
                    
        except Exception as e:
            logging.error(f"Business discovery failed: {e}")
            
        return businesses
    
    def get_business_details(self, place_id: str) -> Optional[Dict]:
        """Get detailed business information."""
        try:
            url = "https://maps.googleapis.com/maps/api/place/details/json"
            
            params = {
                'key': self.google_api_key,
                'place_id': place_id,
                'fields': 'name,formatted_phone_number,website,formatted_address'
            }
            
            response = self.scraper.safe_request(url, params)
            if not response:
                return None
                
            data = response.json()
            result = data.get('result', {})
            
            return {
                'phone': result.get('formatted_phone_number'),
                'website': result.get('website'),
                'address': result.get('formatted_address')
            }
            
        except Exception as e:
            logging.error(f"Failed to get business details: {e}")
            return None

class ContentGenerator:
    """Generates marketing content for service businesses."""
    
    def __init__(self, templates_dir: str):
        self.templates_dir = Path(templates_dir)
        self.jinja_env = Environment(loader=FileSystemLoader(templates_dir))
        
        # Service-specific content templates
        self.content_templates = {
            'auto_detailing': {
                'hooks': [
                    "Transform your car's look in just 2 hours",
                    "Make your vehicle shine like new again",
                    "Professional detailing at your doorstep",
                    "Protect your car's value with expert care"
                ],
                'benefits': [
                    "Increases resale value", "Protects paint", "Interior deep clean",
                    "Mobile convenience", "Professional results", "Paint protection"
                ],
                'ctas': [
                    "Book your detail today!", "Call now for instant quote!",
                    "Schedule mobile detailing!", "Transform your car now!"
                ]
            },
            'hvac': {
                'hooks': [
                    "Stay comfortable year-round with expert HVAC",
                    "Emergency AC repair - same day service",
                    "Cut energy bills with efficient HVAC systems",
                    "24/7 heating and cooling solutions"
                ],
                'benefits': [
                    "Energy savings", "Improved air quality", "Reliable comfort",
                    "Emergency service", "Licensed technicians", "Warranty protection"
                ],
                'ctas': [
                    "Call for emergency service!", "Schedule maintenance today!",
                    "Get free estimate now!", "Fix your HVAC today!"
                ]
            },
            'plumbing': {
                'hooks': [
                    "Emergency plumbing - we're on our way!",
                    "Stop leaks before they cause damage",
                    "Professional plumbing solutions 24/7",
                    "Fast, reliable plumbing repairs"
                ],
                'benefits': [
                    "24/7 emergency service", "Licensed plumbers", "Upfront pricing",
                    "Quality workmanship", "Prevent water damage", "Same day service"
                ],
                'ctas': [
                    "Call emergency line now!", "Schedule repair today!",
                    "Get instant quote!", "Stop the leak now!"
                ]
            }
        }
        
    def generate_ad_content(self, business: Business) -> ContentAsset:
        """Generate targeted ad content for a business."""
        service_key = self.normalize_service_type(business.business_type)
        template_data = self.content_templates.get(service_key, self.content_templates['auto_detailing'])
        
        # Select random elements for variety
        hook = random.choice(template_data['hooks'])
        benefits = random.sample(template_data['benefits'], min(3, len(template_data['benefits'])))
        cta = random.choice(template_data['ctas'])
        
        # Generate hashtags
        hashtags = self.generate_hashtags(business)
        
        # Create content
        title = f"{business.business_type.title()} | {business.location}"
        content = self.create_ad_copy(hook, benefits, cta, business)
        
        asset = ContentAsset(
            asset_id=f"ad_{business.name}_{int(time.time())}",
            business_id=hashlib.md5(f"{business.name}_{business.location}".encode()).hexdigest(),
            asset_type="ad",
            content=content,
            title=title,
            call_to_action=cta,
            hashtags=hashtags,
            platforms=["facebook", "google_ads", "instagram"]
        )
        
        return asset
    
    def normalize_service_type(self, service_type: str) -> str:
        """Normalize service type to template key."""
        service_lower = service_type.lower()
        if 'auto' in service_lower or 'car' in service_lower or 'detail' in service_lower:
            return 'auto_detailing'
        elif 'hvac' in service_lower or 'air' in service_lower or 'heat' in service_lower:
            return 'hvac'
        elif 'plumb' in service_lower:
            return 'plumbing'
        else:
            return 'auto_detailing'  # default
    
    def create_ad_copy(self, hook: str, benefits: List[str], cta: str, business: Business) -> str:
        """Create compelling ad copy."""
        benefits_text = " • ".join(benefits)
        
        ad_copy = f""">> {hook}

>>> {benefits_text}

[*] Serving {business.location}
[+] Professional Service Guaranteed

{cta}

#LocalBusiness #{business.business_type.replace(' ', '')} #{business.location.replace(' ', '').replace(',', '')}"""
        
        return ad_copy
    
    def generate_hashtags(self, business: Business) -> List[str]:
        """Generate relevant hashtags."""
        base_tags = ['LocalBusiness', 'ProfessionalService']
        
        # Service-specific tags
        service_lower = business.business_type.lower()
        if 'auto' in service_lower or 'car' in service_lower:
            base_tags.extend(['AutoDetailing', 'CarCare', 'MobileDetailing'])
        elif 'hvac' in service_lower:
            base_tags.extend(['HVAC', 'AirConditioning', 'Heating'])
        elif 'plumb' in service_lower:
            base_tags.extend(['Plumbing', 'PlumbingRepair', 'Emergency'])
        
        # Location tags
        location_clean = business.location.replace(' ', '').replace(',', '')
        base_tags.append(location_clean)
        
        return base_tags[:10]  # Limit to 10 hashtags

class CanvaIntegration:
    """Integration with Canva API for automated design creation."""
    
    def __init__(self, canva_api_key: str = None):
        self.api_key = canva_api_key
        self.base_url = "https://api.canva.com/v1"
        
        # Template IDs for different business types
        self.design_templates = {
            'auto_detailing': 'DAFYbQ-FpkQ',  # Sample template ID
            'hvac': 'DAFYbQ-Gg2s',
            'plumbing': 'DAFYbQ-Hh3t'
        }
    
    async def create_ad_design(self, content_asset: ContentAsset) -> Optional[str]:
        """Create a visual design for the content asset."""
        if not self.api_key:
            logging.warning("Canva API key not configured")
            return None
            
        try:
            # This would integrate with actual Canva API
            # For now, return a placeholder
            design_url = f"https://canva.com/design/{content_asset.asset_id}"
            logging.info(f"Generated Canva design: {design_url}")
            return design_url
            
        except Exception as e:
            logging.error(f"Canva integration failed: {e}")
            return None

class SyndicatorAgent:
    """Main syndicator agent coordinating all operations."""
    
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = self.load_config()
        self.root_path = self.config_path.parent
        
        # Initialize components
        self.google_api_key = os.getenv('GOOGLE_API_KEY')
        self.discoverer = BusinessDiscoverer(self.google_api_key) if self.google_api_key else None
        self.content_generator = ContentGenerator(self.root_path / "templates")
        self.canva = CanvaIntegration(os.getenv('CANVA_API_KEY'))
        
        # Database for persistence
        self.db_path = self.root_path / "syndicator.db"
        self.setup_database()
        
        # Operational flags
        self.is_running = False
        self.automation_thread = None
        
        logging.info("Syndicator Agent initialized")
    
    def load_config(self) -> Dict:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path) as f:
                return yaml.safe_load(f)
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
            return {}
    
    def setup_database(self):
        """Initialize SQLite database for persistence."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Businesses table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS businesses (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    business_type TEXT,
                    location TEXT,
                    phone TEXT,
                    website TEXT,
                    address TEXT,
                    rating REAL,
                    discovery_source TEXT,
                    contact_status TEXT,
                    created_at TEXT,
                    last_contacted TEXT
                )
            ''')
            
            # Content assets table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS content_assets (
                    asset_id TEXT PRIMARY KEY,
                    business_id TEXT,
                    asset_type TEXT,
                    content TEXT,
                    title TEXT,
                    call_to_action TEXT,
                    hashtags TEXT,
                    platforms TEXT,
                    created_at TEXT,
                    scheduled_for TEXT,
                    status TEXT,
                    design_url TEXT
                )
            ''')
            
            conn.commit()
            conn.close()
            logging.info("Database initialized successfully")
            
        except Exception as e:
            logging.error(f"Database setup failed: {e}")
    
    def save_business(self, business: Business):
        """Save business to database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            business_id = hashlib.md5(f"{business.name}_{business.location}".encode()).hexdigest()
            
            cursor.execute('''
                INSERT OR REPLACE INTO businesses 
                (id, name, business_type, location, phone, website, address, 
                 rating, discovery_source, contact_status, created_at, last_contacted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                business_id, business.name, business.business_type, business.location,
                business.phone, business.website, business.address, business.rating,
                business.discovery_source, business.contact_status,
                business.created_at.isoformat() if business.created_at else None,
                business.last_contacted.isoformat() if business.last_contacted else None
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logging.error(f"Failed to save business: {e}")
    
    def save_content_asset(self, asset: ContentAsset, design_url: str = None):
        """Save content asset to database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO content_assets
                (asset_id, business_id, asset_type, content, title, call_to_action,
                 hashtags, platforms, created_at, scheduled_for, status, design_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                asset.asset_id, asset.business_id, asset.asset_type, asset.content,
                asset.title, asset.call_to_action, json.dumps(asset.hashtags),
                json.dumps(asset.platforms),
                asset.created_at.isoformat() if asset.created_at else None,
                asset.scheduled_for.isoformat() if asset.scheduled_for else None,
                asset.status, design_url
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logging.error(f"Failed to save content asset: {e}")
    
    async def discover_and_create_pipeline(self):
        """Main pipeline: discover businesses and create content."""
        if not self.discoverer:
            logging.error("Business discoverer not initialized - missing Google API key")
            return
        
        target_locations = [
            "Chicago, IL", "Austin, TX", "Phoenix, AZ", "Denver, CO",
            "Nashville, TN", "Atlanta, GA", "Miami, FL", "Las Vegas, NV"
        ]
        
        for location in target_locations:
            for service_type in self.discoverer.service_types[:3]:  # Limit for testing
                try:
                    logging.info(f"Discovering {service_type} businesses in {location}")
                    
                    # Discover businesses
                    businesses = self.discoverer.discover_local_businesses(location, service_type)
                    
                    for business in businesses:
                        # Save business to database
                        self.save_business(business)
                        
                        # Generate content asset
                        content_asset = self.content_generator.generate_ad_content(business)
                        
                        # Create design (if Canva is available)
                        design_url = await self.canva.create_ad_design(content_asset)
                        
                        # Save content asset
                        self.save_content_asset(content_asset, design_url)
                        
                        logging.info(f"Created content for {business.name}")
                        
                        # Rate limiting delay
                        await asyncio.sleep(random.uniform(5, 15))
                
                except Exception as e:
                    logging.error(f"Pipeline error for {service_type} in {location}: {e}")
                    continue
    
    def start_24_7_automation(self):
        """Start 24/7 automated operations."""
        if self.is_running:
            logging.warning("Automation already running")
            return
        
        self.is_running = True
        self.automation_thread = threading.Thread(target=self._automation_loop, daemon=True)
        self.automation_thread.start()
        
        logging.info("24/7 automation started")
    
    def _automation_loop(self):
        """Background automation loop."""
        while self.is_running:
            try:
                # Run discovery and content creation pipeline
                asyncio.run(self.discover_and_create_pipeline())
                
                # Wait before next cycle (e.g., run every 4 hours)
                cycle_delay = 4 * 60 * 60  # 4 hours in seconds
                time.sleep(cycle_delay)
                
            except Exception as e:
                logging.error(f"Automation loop error: {e}")
                time.sleep(300)  # Wait 5 minutes on error
    
    def stop_automation(self):
        """Stop automated operations."""
        self.is_running = False
        if self.automation_thread:
            self.automation_thread.join(timeout=30)
        logging.info("Automation stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get operational statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Business stats
            cursor.execute("SELECT COUNT(*) FROM businesses")
            total_businesses = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM businesses WHERE created_at > datetime('now', '-24 hours')")
            businesses_today = cursor.fetchone()[0]
            
            # Content stats
            cursor.execute("SELECT COUNT(*) FROM content_assets")
            total_content = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM content_assets WHERE created_at > datetime('now', '-24 hours')")
            content_today = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                'total_businesses_discovered': total_businesses,
                'businesses_discovered_today': businesses_today,
                'total_content_created': total_content,
                'content_created_today': content_today,
                'automation_running': self.is_running,
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Failed to get stats: {e}")
            return {'error': str(e)}

# Initialize global syndicator instance
syndicator = None

def initialize_syndicator(config_path: str = None) -> SyndicatorAgent:
    """Initialize the global syndicator agent."""
    global syndicator
    
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    syndicator = SyndicatorAgent(config_path)
    return syndicator

def get_syndicator() -> Optional[SyndicatorAgent]:
    """Get the global syndicator instance."""
    return syndicator

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('syndicator.log'),
        logging.StreamHandler()
    ]
)

if __name__ == "__main__":
    # Initialize and start syndicator
    agent = initialize_syndicator()
    
    print("SINCOR Syndicator Agent")
    print("=" * 40)
    print("Starting 24/7 automated operations...")
    
    agent.start_24_7_automation()
    
    try:
        while True:
            stats = agent.get_stats()
            print(f"\nStats: {stats}")
            time.sleep(60)  # Print stats every minute
    except KeyboardInterrupt:
        print("\nStopping syndicator...")
        agent.stop_automation()