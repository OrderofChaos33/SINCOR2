"""
Business Intelligence Agent for SINCOR

Automatically discovers and profiles businesses for targeted marketing campaigns.
Focuses on auto detailing shops but extensible to any service industry.

Features:
- Google Places API integration for business discovery
- Web scraping for additional business details
- Lead scoring and prioritization
- Business data enrichment and validation
- Integration with content generation pipeline
"""

import json
import requests
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse
import re

import sys
sys.path.append(str(Path(__file__).parent.parent))
from base_agent import BaseAgent


class BusinessIntelAgent(BaseAgent):
    """Agent for discovering and profiling businesses for marketing campaigns."""
    
    def __init__(self, name="BusinessIntel", log_path="logs/business_intel.log", config=None):
        super().__init__(name, log_path, config)
        
        # Database setup
        self.db_path = Path("data/business_intel.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # API configuration
        self.google_api_key = config.get("google_api_key") if config else None
        self.yelp_api_key = config.get("yelp_api_key") if config else None
        
        # Search parameters
        self.search_radius = config.get("search_radius", 50000) if config else 50000  # 50km default
        self.rate_limit_delay = config.get("rate_limit_delay", 1) if config else 1  # seconds between requests
        
        # Initialize database
        self._init_database()
        
        self._log("Business Intelligence Agent initialized")
    
    def _init_database(self):
        """Initialize the business intelligence database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Businesses table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS businesses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    google_place_id TEXT UNIQUE,
                    business_name TEXT NOT NULL,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    phone TEXT,
                    email TEXT,
                    website TEXT,
                    business_type TEXT,
                    rating REAL,
                    review_count INTEGER,
                    price_level INTEGER,
                    hours TEXT,
                    services TEXT,
                    lead_score INTEGER DEFAULT 0,
                    contact_attempted BOOLEAN DEFAULT FALSE,
                    contacted_date TEXT,
                    response_status TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Search history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_query TEXT,
                    location TEXT,
                    radius INTEGER,
                    results_found INTEGER,
                    search_date TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Lead campaigns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_name TEXT,
                    target_business_type TEXT,
                    template_used TEXT,
                    businesses_targeted INTEGER,
                    emails_sent INTEGER,
                    responses_received INTEGER,
                    conversion_rate REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            self._log("Database initialized successfully")
            
        except Exception as e:
            self._log(f"Database initialization error: {e}")
    
    def search_businesses_by_location(self, location: str, business_type: str = "car detailing", 
                                    radius: int = None) -> List[Dict]:
        """
        Search for businesses by location using Google Places API.
        
        Args:
            location: City, state or coordinates
            business_type: Type of business to search for
            radius: Search radius in meters
            
        Returns:
            List of business dictionaries
        """
        if not self.google_api_key:
            self._log("ERROR: Google API key not configured")
            return []
        
        radius = radius or self.search_radius
        businesses = []
        
        try:
            # Google Places Text Search
            base_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
            
            params = {
                "query": f"{business_type} in {location}",
                "radius": radius,
                "key": self.google_api_key
            }
            
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "OK":
                self._log(f"Google Places API error: {data.get('error_message', data.get('status'))}")
                return []
            
            # Process results
            for place in data.get("results", []):
                business_data = self._extract_place_details(place)
                businesses.append(business_data)
                
                # Get detailed information
                detailed_data = self._get_place_details(place.get("place_id"))
                if detailed_data:
                    business_data.update(detailed_data)
                
                # Rate limiting
                time.sleep(self.rate_limit_delay)
            
            # Log search
            self._log_search(f"{business_type} in {location}", location, radius, len(businesses))
            self._log(f"Found {len(businesses)} businesses for '{business_type}' in {location}")
            
            return businesses
            
        except Exception as e:
            self._log(f"Error searching businesses: {e}")
            return []
    
    def _extract_place_details(self, place: Dict) -> Dict:
        """Extract basic business details from Google Places result."""
        return {
            "google_place_id": place.get("place_id"),
            "business_name": place.get("name"),
            "address": place.get("formatted_address"),
            "business_type": "auto_detailing",
            "rating": place.get("rating"),
            "review_count": place.get("user_ratings_total"),
            "price_level": place.get("price_level"),
            "geometry": place.get("geometry", {}).get("location", {}),
        }
    
    def _get_place_details(self, place_id: str) -> Optional[Dict]:
        """Get detailed information for a specific place."""
        if not place_id or not self.google_api_key:
            return None
        
        try:
            url = "https://maps.googleapis.com/maps/api/place/details/json"
            params = {
                "place_id": place_id,
                "fields": "name,formatted_address,formatted_phone_number,website,opening_hours,reviews",
                "key": self.google_api_key
            }
            
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "OK":
                return None
            
            result = data.get("result", {})
            
            # Extract additional details
            details = {
                "phone": self._clean_phone(result.get("formatted_phone_number")),
                "website": result.get("website"),
                "hours": json.dumps(result.get("opening_hours", {}).get("weekday_text", [])),
                "recent_reviews": result.get("reviews", [])[:3]  # Latest 3 reviews
            }
            
            # Extract email from website if possible
            if details.get("website"):
                details["email"] = self._extract_email_from_website(details["website"])
            
            return details
            
        except Exception as e:
            self._log(f"Error getting place details for {place_id}: {e}")
            return None
    
    def _clean_phone(self, phone: str) -> str:
        """Clean and format phone number."""
        if not phone:
            return ""
        
        # Remove all non-digit characters except +
        cleaned = re.sub(r'[^\d+]', '', phone)
        
        # Add +1 if it's a US number without country code
        if cleaned and not cleaned.startswith('+'):
            if len(cleaned) == 10:
                cleaned = '+1' + cleaned
        
        return cleaned
    
    def _extract_email_from_website(self, website_url: str) -> str:
        """Attempt to extract email from business website."""
        try:
            # Simple email extraction - could be enhanced with more sophisticated scraping
            response = requests.get(website_url, timeout=10, 
                                  headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            response.raise_for_status()
            
            # Look for email patterns
            email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            emails = re.findall(email_pattern, response.text)
            
            # Filter out common non-business emails
            business_emails = [email for email in emails 
                             if not any(skip in email.lower() for skip in ['noreply', 'no-reply', 'support@wordpress'])]
            
            return business_emails[0] if business_emails else ""
            
        except Exception as e:
            self._log(f"Error extracting email from {website_url}: {e}")
            return ""
    
    def calculate_lead_score(self, business: Dict) -> int:
        """Calculate lead score based on business characteristics."""
        score = 0
        
        # Rating score (0-25 points)
        rating = business.get("rating", 0)
        if rating >= 4.5:
            score += 25
        elif rating >= 4.0:
            score += 20
        elif rating >= 3.5:
            score += 15
        elif rating >= 3.0:
            score += 10
        
        # Review count (0-20 points)
        review_count = business.get("review_count", 0)
        if review_count >= 100:
            score += 20
        elif review_count >= 50:
            score += 15
        elif review_count >= 20:
            score += 10
        elif review_count >= 5:
            score += 5
        
        # Contact information availability (0-30 points)
        if business.get("phone"):
            score += 10
        if business.get("email"):
            score += 15
        if business.get("website"):
            score += 5
        
        # Business activity indicators (0-25 points)
        if business.get("hours"):
            score += 10
        if business.get("recent_reviews"):
            score += 15
        
        return min(score, 100)  # Cap at 100
    
    def save_businesses(self, businesses: List[Dict]) -> int:
        """Save businesses to database with lead scoring."""
        saved_count = 0
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for business in businesses:
                # Calculate lead score
                business["lead_score"] = self.calculate_lead_score(business)
                
                # Parse address into components
                address_parts = self._parse_address(business.get("address", ""))
                business.update(address_parts)
                
                # Insert or update business
                cursor.execute('''
                    INSERT OR REPLACE INTO businesses 
                    (google_place_id, business_name, address, city, state, zip_code, 
                     phone, email, website, business_type, rating, review_count, 
                     price_level, hours, lead_score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    business.get("google_place_id"),
                    business.get("business_name"),
                    business.get("address"),
                    business.get("city"),
                    business.get("state"),
                    business.get("zip_code"),
                    business.get("phone"),
                    business.get("email"),
                    business.get("website"),
                    business.get("business_type"),
                    business.get("rating"),
                    business.get("review_count"),
                    business.get("price_level"),
                    business.get("hours"),
                    business.get("lead_score"),
                    datetime.now().isoformat()
                ))
                saved_count += 1
            
            conn.commit()
            conn.close()
            
            self._log(f"Saved {saved_count} businesses to database")
            return saved_count
            
        except Exception as e:
            self._log(f"Error saving businesses: {e}")
            return 0
    
    def _parse_address(self, address: str) -> Dict:
        """Parse address into city, state, zip components."""
        if not address:
            return {"city": "", "state": "", "zip_code": ""}
        
        # Simple address parsing - could be enhanced
        parts = address.split(", ")
        
        if len(parts) >= 2:
            # Last part usually contains state and zip
            last_part = parts[-1]
            state_zip = last_part.split()
            
            state = state_zip[0] if state_zip else ""
            zip_code = state_zip[1] if len(state_zip) > 1 else ""
            
            # Second to last is usually city
            city = parts[-2] if len(parts) > 1 else ""
            
            return {
                "city": city,
                "state": state,
                "zip_code": zip_code
            }
        
        return {"city": "", "state": "", "zip_code": ""}
    
    def _log_search(self, query: str, location: str, radius: int, results_count: int):
        """Log search activity."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO search_history (search_query, location, radius, results_found)
                VALUES (?, ?, ?, ?)
            ''', (query, location, radius, results_count))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self._log(f"Error logging search: {e}")
    
    def get_high_value_prospects(self, limit: int = 50, min_score: int = 70) -> List[Dict]:
        """Get high-value prospects for marketing campaigns."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM businesses 
                WHERE lead_score >= ? AND contact_attempted = FALSE
                ORDER BY lead_score DESC, rating DESC
                LIMIT ?
            ''', (min_score, limit))
            
            prospects = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            self._log(f"Retrieved {len(prospects)} high-value prospects (min score: {min_score})")
            return prospects
            
        except Exception as e:
            self._log(f"Error retrieving prospects: {e}")
            return []
    
    def mark_contacted(self, business_id: int, response_status: str = "pending"):
        """Mark a business as contacted."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE businesses 
                SET contact_attempted = TRUE, contacted_date = ?, response_status = ?
                WHERE id = ?
            ''', (datetime.now().isoformat(), response_status, business_id))
            
            conn.commit()
            conn.close()
            
            self._log(f"Marked business {business_id} as contacted with status: {response_status}")
            
        except Exception as e:
            self._log(f"Error marking business as contacted: {e}")
    
    def get_database_stats(self) -> Dict:
        """Get database statistics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            stats = {}
            
            # Total businesses
            cursor.execute("SELECT COUNT(*) FROM businesses")
            stats["total_businesses"] = cursor.fetchone()[0]
            
            # High-value prospects
            cursor.execute("SELECT COUNT(*) FROM businesses WHERE lead_score >= 70")
            stats["high_value_prospects"] = cursor.fetchone()[0]
            
            # Contacted businesses
            cursor.execute("SELECT COUNT(*) FROM businesses WHERE contact_attempted = TRUE")
            stats["contacted_businesses"] = cursor.fetchone()[0]
            
            # Average lead score
            cursor.execute("SELECT AVG(lead_score) FROM businesses")
            stats["average_lead_score"] = round(cursor.fetchone()[0] or 0, 2)
            
            # Top cities
            cursor.execute('''
                SELECT city, COUNT(*) as count FROM businesses 
                WHERE city != '' 
                GROUP BY city 
                ORDER BY count DESC 
                LIMIT 10
            ''')
            stats["top_cities"] = dict(cursor.fetchall())
            
            conn.close()
            return stats
            
        except Exception as e:
            self._log(f"Error getting database stats: {e}")
            return {}
    
    def _run_custom_diagnostics(self) -> Optional[Dict[str, Any]]:
        """Run Business Intel agent-specific diagnostics."""
        try:
            diagnostics = {
                "database_path": str(self.db_path),
                "database_exists": self.db_path.exists(),
                "google_api_configured": bool(self.google_api_key),
                "yelp_api_configured": bool(self.yelp_api_key),
                "search_radius": self.search_radius,
                "rate_limit_delay": self.rate_limit_delay
            }
            
            # Add database stats if available
            if self.db_path.exists():
                stats = self.get_database_stats()
                diagnostics.update({"database_stats": stats})
            
            return diagnostics
            
        except Exception as e:
            return {"diagnostics_error": str(e)}


if __name__ == "__main__":
    # Example usage
    config = {
        "google_api_key": "your_google_api_key_here",
        "search_radius": 50000,
        "rate_limit_delay": 1
    }
    
    agent = BusinessIntelAgent(config=config)
    
    # Search for detailing businesses in a city
    businesses = agent.search_businesses_by_location("Austin, TX", "auto detailing")
    
    if businesses:
        # Save to database
        saved_count = agent.save_businesses(businesses)
        print(f"Saved {saved_count} businesses")
        
        # Get high-value prospects
        prospects = agent.get_high_value_prospects(limit=20, min_score=75)
        print(f"Found {len(prospects)} high-value prospects")
        
        # Show database stats
        stats = agent.get_database_stats()
        print("Database Stats:", json.dumps(stats, indent=2))