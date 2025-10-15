"""
Industry Expansion Agent for SINCOR

Scales the business intelligence and marketing automation system beyond auto detailing
to capture multiple service industries. Automatically identifies new market opportunities,
adapts templates and personas for different business types, and manages multi-industry campaigns.

Target Industries:
- Auto Detailing (60K businesses) - Primary
- HVAC Services (120K businesses)  
- Landscaping (400K businesses)
- Plumbing Services (130K businesses)
- Roofing Contractors (100K businesses)
- Cleaning Services (200K businesses)
- Pool Services (50K businesses)
- And more...

Features:
- Industry-specific business discovery
- Template adaptation for different service types
- Cross-industry campaign management
- Market size analysis and prioritization
- Industry-specific persona development
- Service type classification and targeting
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import yaml
from dataclasses import dataclass
import requests

import sys
sys.path.append(str(Path(__file__).parent.parent))
from base_agent import BaseAgent
from intelligence.business_intel_agent import BusinessIntelAgent
from intelligence.template_engine import TemplateEngine


@dataclass
class IndustryConfig:
    """Configuration for different industries."""
    name: str
    search_terms: List[str]
    business_type: str
    market_size_estimate: int
    avg_revenue_estimate: int
    seasonality: str  # "year_round", "seasonal", "weather_dependent"
    primary_personas: List[str]
    pain_points: List[str]
    service_keywords: List[str]
    competition_level: str  # "low", "medium", "high"
    digital_adoption: str  # "low", "medium", "high"


class IndustryExpansionAgent(BaseAgent):
    """Agent for expanding SINCOR to multiple service industries."""
    
    def __init__(self, name="IndustryExpansion", log_path="logs/industry_expansion.log", config=None):
        super().__init__(name, log_path, config)
        
        # Dependencies
        self.business_intel = BusinessIntelAgent(config=config)
        self.template_engine = TemplateEngine(config=config)
        
        # Industry database
        self.industry_db = Path("data/industry_expansion.db")
        self.industry_db.parent.mkdir(parents=True, exist_ok=True)
        
        # Load industry configurations
        self.industries = self._load_industry_configs()
        
        # Market research configuration
        self.market_research_enabled = config.get("market_research_enabled", True) if config else True
        
        # Initialize database
        self._init_industry_database()
        
        self._log("Industry Expansion Agent initialized")
    
    def _load_industry_configs(self) -> Dict[str, IndustryConfig]:
        """Load industry-specific configurations."""
        industries = {
            "auto_detailing": IndustryConfig(
                name="Auto Detailing",
                search_terms=["auto detailing", "car detailing", "mobile detailing", "car wash"],
                business_type="auto_detailing",
                market_size_estimate=60000,
                avg_revenue_estimate=150000,
                seasonality="year_round",
                primary_personas=["business_owner", "fleet_manager"],
                pain_points=["time_consuming", "weather_dependent", "equipment_costs", "customer_retention"],
                service_keywords=["wash", "wax", "interior", "exterior", "ceramic coating", "paint protection"],
                competition_level="medium",
                digital_adoption="medium"
            ),
            
            "hvac_services": IndustryConfig(
                name="HVAC Services",
                search_terms=["HVAC repair", "heating cooling", "air conditioning", "furnace repair"],
                business_type="hvac_services",
                market_size_estimate=120000,
                avg_revenue_estimate=300000,
                seasonality="seasonal",
                primary_personas=["business_owner", "service_manager", "technician_owner"],
                pain_points=["emergency_calls", "seasonal_demand", "equipment_costs", "skilled_labor"],
                service_keywords=["repair", "installation", "maintenance", "emergency", "24/7"],
                competition_level="high",
                digital_adoption="medium"
            ),
            
            "pest_control": IndustryConfig(
                name="Pest Control",
                search_terms=["pest control", "exterminator", "bug control", "termite control"],
                business_type="pest_control",
                market_size_estimate=25000,
                avg_revenue_estimate=120000,
                seasonality="year_round",
                primary_personas=["business_owner", "technician"],
                pain_points=["regulatory_compliance", "chemical_safety", "seasonal_pests", "customer_education"],
                service_keywords=["extermination", "inspection", "prevention", "treatment", "fumigation"],
                competition_level="medium",
                digital_adoption="medium"
            ),
            
            "plumbing_services": IndustryConfig(
                name="Plumbing Services",
                search_terms=["plumber", "plumbing repair", "drain cleaning", "water heater"],
                business_type="plumbing_services",
                market_size_estimate=130000,
                avg_revenue_estimate=250000,
                seasonality="year_round",
                primary_personas=["business_owner", "master_plumber"],
                pain_points=["emergency_calls", "parts_availability", "skilled_labor", "licensing"],
                service_keywords=["repair", "installation", "emergency", "leak", "clog", "replacement"],
                competition_level="medium",
                digital_adoption="medium"
            ),
            
            "electrical": IndustryConfig(
                name="Electrical Contractor",
                search_terms=["electrician", "electrical contractor", "electrical repair", "electrical installation"],
                business_type="electrical",
                market_size_estimate=75000,
                avg_revenue_estimate=200000,
                seasonality="year_round",
                primary_personas=["business_owner", "master_electrician"],
                pain_points=["safety_compliance", "code_requirements", "emergency_calls", "skilled_labor"],
                service_keywords=["installation", "repair", "wiring", "electrical", "circuits"],
                competition_level="medium",
                digital_adoption="medium"
            ),
            
            "chiropractor": IndustryConfig(
                name="Chiropractic",
                search_terms=["chiropractor", "chiropractic clinic", "spinal adjustment", "pain relief"],
                business_type="chiropractor",
                market_size_estimate=45000,
                avg_revenue_estimate=180000,
                seasonality="year_round",
                primary_personas=["practice_owner", "chiropractor"],
                pain_points=["insurance_billing", "patient_retention", "competition", "equipment_costs"],
                service_keywords=["adjustment", "therapy", "pain relief", "wellness", "treatment"],
                competition_level="high",
                digital_adoption="medium"
            ),
            
            "dog_grooming": IndustryConfig(
                name="Dog Grooming",
                search_terms=["dog grooming", "pet grooming", "dog groomer", "pet salon"],
                business_type="dog_grooming",
                market_size_estimate=18000,
                avg_revenue_estimate=85000,
                seasonality="year_round",
                primary_personas=["business_owner", "pet_groomer"],
                pain_points=["animal_handling", "equipment_costs", "customer_scheduling", "seasonal_demand"],
                service_keywords=["grooming", "bathing", "trimming", "nail_clipping", "styling"],
                competition_level="medium",
                digital_adoption="low"
            )
        }
        
        return industries
    
    def _init_industry_database(self):
        """Initialize industry expansion database."""
        try:
            conn = sqlite3.connect(self.industry_db)
            cursor = conn.cursor()
            
            # Industry analysis table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS industry_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    industry_type TEXT,
                    location TEXT,
                    total_businesses_found INTEGER,
                    avg_lead_score REAL,
                    market_saturation REAL,
                    competition_density REAL,
                    opportunity_score REAL,
                    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Cross-industry campaigns
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS multi_industry_campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_name TEXT,
                    target_industries TEXT,
                    businesses_targeted INTEGER DEFAULT 0,
                    total_sent INTEGER DEFAULT 0,
                    total_responses INTEGER DEFAULT 0,
                    roi_estimate REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Industry-specific templates
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS industry_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    industry_type TEXT,
                    template_type TEXT,
                    template_name TEXT,
                    template_content TEXT,
                    performance_score REAL DEFAULT 0,
                    usage_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Market opportunity tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS market_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    industry_type TEXT,
                    location TEXT,
                    opportunity_type TEXT,
                    description TEXT,
                    potential_businesses INTEGER,
                    revenue_potential REAL,
                    difficulty_score INTEGER,
                    priority_score INTEGER,
                    status TEXT DEFAULT 'identified',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            self._log("Industry expansion database initialized successfully")
            
        except Exception as e:
            self._log(f"Industry database initialization error: {e}")
    
    def analyze_industry_opportunity(self, industry_type: str, location: str) -> Dict:
        """Analyze market opportunity for a specific industry in a location."""
        try:
            if industry_type not in self.industries:
                self._log(f"Industry type '{industry_type}' not configured")
                return {}
            
            industry_config = self.industries[industry_type]
            
            # Search for businesses in this industry
            businesses = []
            for search_term in industry_config.search_terms:
                found_businesses = self.business_intel.search_businesses_by_location(
                    location, search_term
                )
                businesses.extend(found_businesses)
            
            # Remove duplicates by name/address
            unique_businesses = self._deduplicate_businesses(businesses)
            
            # Calculate scores and save businesses
            if unique_businesses:
                # Update business types
                for business in unique_businesses:
                    business["business_type"] = industry_config.business_type
                
                # Save to business intel database
                self.business_intel.save_businesses(unique_businesses)
            
            # Analyze market characteristics
            analysis = self._calculate_market_metrics(unique_businesses, industry_config)
            
            # Save analysis
            analysis_id = self._save_industry_analysis(industry_type, location, analysis)
            
            # Identify opportunities
            opportunities = self._identify_market_opportunities(
                industry_type, location, analysis, unique_businesses
            )
            
            result = {
                "analysis_id": analysis_id,
                "industry": industry_config.name,
                "location": location,
                "businesses_found": len(unique_businesses),
                "market_analysis": analysis,
                "opportunities": opportunities,
                "businesses": unique_businesses[:10]  # First 10 for preview
            }
            
            self._log(f"Analyzed {industry_type} opportunity in {location}: {len(unique_businesses)} businesses found")
            return result
            
        except Exception as e:
            self._log(f"Error analyzing industry opportunity: {e}")
            return {}
    
    def _deduplicate_businesses(self, businesses: List[Dict]) -> List[Dict]:
        """Remove duplicate businesses based on name and address similarity."""
        unique_businesses = []
        seen_combinations = set()
        
        for business in businesses:
            # Create a simple key for deduplication
            name = business.get("business_name", "").lower().strip()
            address = business.get("address", "").lower().strip()
            
            # Simple deduplication key
            key = f"{name}|{address}"
            
            if key not in seen_combinations and name and address:
                seen_combinations.add(key)
                unique_businesses.append(business)
        
        return unique_businesses
    
    def _calculate_market_metrics(self, businesses: List[Dict], industry_config: IndustryConfig) -> Dict:
        """Calculate market analysis metrics."""
        if not businesses:
            return {
                "avg_lead_score": 0,
                "market_saturation": 0,
                "competition_density": 0,
                "opportunity_score": 0,
                "quality_distribution": {},
                "digital_presence": 0
            }
        
        # Calculate average lead score
        lead_scores = [b.get("lead_score", 0) for b in businesses if b.get("lead_score")]
        avg_lead_score = sum(lead_scores) / len(lead_scores) if lead_scores else 0
        
        # Digital presence analysis
        businesses_with_websites = sum(1 for b in businesses if b.get("website"))
        digital_presence = (businesses_with_websites / len(businesses)) * 100 if businesses else 0
        
        # Quality distribution
        high_quality = sum(1 for b in businesses if b.get("rating", 0) >= 4.0)
        medium_quality = sum(1 for b in businesses if 3.0 <= b.get("rating", 0) < 4.0)
        low_quality = len(businesses) - high_quality - medium_quality
        
        quality_distribution = {
            "high_quality": high_quality,
            "medium_quality": medium_quality,
            "low_quality": low_quality
        }
        
        # Market saturation (rough estimate based on found businesses vs estimated market size)
        market_saturation = min((len(businesses) / industry_config.market_size_estimate) * 1000, 100)
        
        # Competition density (businesses per capita - simplified)
        competition_density = len(businesses) / 100000  # Simplified metric
        
        # Overall opportunity score
        opportunity_score = self._calculate_opportunity_score(
            avg_lead_score, market_saturation, digital_presence, industry_config
        )
        
        return {
            "avg_lead_score": round(avg_lead_score, 2),
            "market_saturation": round(market_saturation, 2),
            "competition_density": round(competition_density, 2),
            "opportunity_score": round(opportunity_score, 2),
            "quality_distribution": quality_distribution,
            "digital_presence": round(digital_presence, 2)
        }
    
    def _calculate_opportunity_score(self, avg_lead_score: float, market_saturation: float, 
                                   digital_presence: float, industry_config: IndustryConfig) -> float:
        """Calculate overall opportunity score for an industry/location."""
        
        # Base score from lead quality
        score = avg_lead_score * 0.3
        
        # Market saturation impact (lower saturation = higher opportunity)
        saturation_score = max(0, 100 - market_saturation) * 0.25
        score += saturation_score
        
        # Digital adoption gap opportunity
        digital_gap = 100 - digital_presence
        if industry_config.digital_adoption == "low":
            score += digital_gap * 0.2  # Big opportunity in low-digital industries
        else:
            score += digital_gap * 0.1
        
        # Industry-specific factors
        if industry_config.seasonality == "year_round":
            score += 10
        elif industry_config.seasonality == "seasonal":
            score += 5
        
        if industry_config.competition_level == "low":
            score += 15
        elif industry_config.competition_level == "medium":
            score += 5
        
        # Revenue potential factor
        if industry_config.avg_revenue_estimate > 200000:
            score += 10
        elif industry_config.avg_revenue_estimate > 100000:
            score += 5
        
        return min(score, 100)  # Cap at 100
    
    def _identify_market_opportunities(self, industry_type: str, location: str, 
                                     analysis: Dict, businesses: List[Dict]) -> List[Dict]:
        """Identify specific market opportunities."""
        opportunities = []
        
        # Low digital presence opportunity
        if analysis.get("digital_presence", 0) < 50:
            opportunities.append({
                "type": "digital_gap",
                "description": f"Only {analysis.get('digital_presence', 0):.1f}% of {industry_type} businesses have websites",
                "potential_businesses": len([b for b in businesses if not b.get("website")]),
                "priority": "high" if analysis.get("digital_presence", 0) < 30 else "medium"
            })
        
        # Low review businesses
        low_review_businesses = [b for b in businesses if b.get("review_count", 0) < 10]
        if len(low_review_businesses) > len(businesses) * 0.3:
            opportunities.append({
                "type": "review_generation",
                "description": f"{len(low_review_businesses)} businesses have fewer than 10 reviews",
                "potential_businesses": len(low_review_businesses),
                "priority": "medium"
            })
        
        # High-value, uncontacted prospects
        high_value_uncontacted = [
            b for b in businesses 
            if b.get("lead_score", 0) > 70 and not b.get("contact_attempted", False)
        ]
        if high_value_uncontacted:
            opportunities.append({
                "type": "high_value_prospects",
                "description": f"{len(high_value_uncontacted)} high-value prospects not yet contacted",
                "potential_businesses": len(high_value_uncontacted),
                "priority": "high"
            })
        
        # Market expansion opportunity
        if analysis.get("opportunity_score", 0) > 75:
            opportunities.append({
                "type": "market_expansion",
                "description": f"High-opportunity market for {industry_type} in {location}",
                "potential_businesses": len(businesses),
                "priority": "high"
            })
        
        return opportunities
    
    def _save_industry_analysis(self, industry_type: str, location: str, analysis: Dict) -> int:
        """Save industry analysis to database."""
        try:
            conn = sqlite3.connect(self.industry_db)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO industry_analysis 
                (industry_type, location, total_businesses_found, avg_lead_score,
                 market_saturation, competition_density, opportunity_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                industry_type,
                location,
                analysis.get("businesses_found", 0),
                analysis.get("avg_lead_score", 0),
                analysis.get("market_saturation", 0),
                analysis.get("competition_density", 0),
                analysis.get("opportunity_score", 0)
            ))
            
            analysis_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return analysis_id
            
        except Exception as e:
            self._log(f"Error saving industry analysis: {e}")
            return 0
    
    def generate_industry_specific_templates(self, industry_type: str) -> Dict:
        """Generate templates specific to an industry."""
        try:
            if industry_type not in self.industries:
                return {}
            
            industry_config = self.industries[industry_type]
            
            # Create industry-specific HBPC framework
            industry_hbpc = self._create_industry_hbpc(industry_config)
            
            # Generate email template
            email_template = self._generate_industry_email_template(industry_config, industry_hbpc)
            
            # Generate video script template
            video_template = self._generate_industry_video_template(industry_config, industry_hbpc)
            
            # Save templates
            templates = {
                "email": email_template,
                "video_script": video_template,
                "hbpc_framework": industry_hbpc
            }
            
            # Save to database
            for template_type, content in templates.items():
                self._save_industry_template(industry_type, template_type, content)
            
            self._log(f"Generated industry-specific templates for {industry_type}")
            return templates
            
        except Exception as e:
            self._log(f"Error generating industry templates: {e}")
            return {}
    
    def _create_industry_hbpc(self, industry_config: IndustryConfig) -> Dict:
        """Create industry-specific Hook-Benefit-Proof-CTA framework."""
        
        # Industry-specific hooks based on pain points
        hooks_map = {
            "time_consuming": "Tired of spending weekends on [SERVICE] when you could be growing your business?",
            "weather_dependent": "Weather delays costing you customers? There's a better way.",
            "equipment_costs": "Equipment costs eating into your profits?",
            "customer_retention": "Struggling to keep customers coming back?",
            "emergency_calls": "Emergency calls disrupting your family time?",
            "seasonal_demand": "Feast or famine business cycle wearing you down?",
            "skilled_labor": "Can't find reliable, skilled workers?",
            "staff_turnover": "Tired of constantly training new staff?"
        }
        
        # Generate hooks from pain points
        hooks = []
        for pain_point in industry_config.pain_points:
            if pain_point in hooks_map:
                hook = hooks_map[pain_point].replace("[SERVICE]", industry_config.name.lower())
                hooks.append(hook)
        
        # Industry-specific benefits
        benefits_map = {
            "auto_detailing": [
                "Professional results that keep customers coming back",
                "Same-day service that keeps your business moving",
                "Predictable pricing and invoice-ready service"
            ],
            "hvac_services": [
                "24/7 emergency response that builds customer loyalty",
                "Preventive maintenance programs for steady revenue",
                "Energy-efficient solutions that sell themselves"
            ],
            "landscaping": [
                "Year-round service packages for steady income",
                "Professional crews that show up on time, every time",
                "Equipment and insurance included - no overhead worries"
            ],
            "plumbing_services": [
                "Emergency response system that captures every call",
                "Upfront pricing that eliminates customer surprises",
                "Licensed, bonded service that customers trust"
            ],
            "cleaning_services": [
                "Consistent quality that keeps contracts renewing",
                "Flexible scheduling that works around your clients",
                "Eco-friendly supplies that modern customers demand"
            ]
        }
        
        benefits = benefits_map.get(industry_config.business_type, [
            f"Professional {industry_config.name.lower()} that exceeds expectations",
            f"Reliable service that builds your reputation",
            f"Competitive pricing that wins more business"
        ])
        
        # Generic proofs that work for most industries
        proofs = [
            f"Over 500 {industry_config.name.lower()} businesses served",
            f"Licensed and insured for your peace of mind", 
            f"Customer testimonial: 'Best investment for our business growth'",
            f"Before/after results that speak for themselves"
        ]
        
        # Industry-appropriate CTAs
        ctas = [
            f"Book your {industry_config.name.lower()} consultation today",
            "Call now for same-day service: [PHONE]",
            "Visit [WEBSITE] to schedule online",
            f"Text '{industry_config.business_type.upper()}' to [PHONE] for instant quote"
        ]
        
        return {
            "id": f"HBPC_{industry_config.business_type}",
            "name": f"Hook-Benefit-Proof-CTA for {industry_config.name}",
            "hooks": hooks,
            "benefits": benefits,
            "proofs": proofs,
            "ctas": ctas
        }
    
    def _generate_industry_email_template(self, industry_config: IndustryConfig, hbpc: Dict) -> str:
        """Generate industry-specific email template."""
        
        service_name = industry_config.name
        
        template = f"""Subject: Boost Your {service_name} Business Revenue This Quarter

Dear {{{{ business_name }}}} Team,

I noticed {{{{ business_name }}}} has {{{{ rating }}}} stars - clearly you care about quality service. That's exactly why I wanted to reach out about growing your {service_name.lower()} business.

{{{{ hbpc.hooks | select_by_persona(persona) }}}}

**Here's what our {service_name.lower()} program delivers:**
{{{{ hbpc.benefits | select_by_persona(persona) }}}}

**Why {{{{ city }}}} {service_name.lower()} businesses choose our system:**
{{{{ hbpc.proofs | select_by_persona(persona) }}}}

**Special opportunity for {{{{ business_name }}}}:**
- Free analysis of your current {service_name.lower()} operations
- 30% discount on first quarter implementation
- Flexible setup that works around your existing schedule

{{{{ hbpc.ctas | select_by_persona(persona) }}}}

Best regards,
[YOUR_NAME]
[YOUR_PHONE | format_phone]
[YOUR_EMAIL]

P.S. Your competition is already investing in business growth systems. Don't let outdated methods be the reason customers choose them over {{{{ business_name }}}}.

---
*This message was personalized for {service_name} businesses in {{{{ city }}}}, {{{{ state }}}}*
"""
        
        return template
    
    def _generate_industry_video_template(self, industry_config: IndustryConfig, hbpc: Dict) -> str:
        """Generate industry-specific video script template."""
        
        service_name = industry_config.name
        
        template = f"""# Video Script for {{{{ business_name }}}} - {service_name}
**Target**: {service_name} in {{{{ city }}}}
**Persona**: {{{{ persona }}}}
**Duration**: 90 seconds

## Hook (0-8 seconds)
{{{{ hbpc.hooks | select_by_persona(persona) }}}}
*Visual: Split screen showing struggling vs thriving {service_name.lower()} business*

## Problem (8-20 seconds)
You started {{{{ business_name }}}} to build something great. But between {', '.join(industry_config.pain_points[:3])}, it's harder than you imagined.
*Visual: Montage of common {service_name.lower()} business challenges*

## Solution (20-45 seconds)
What if I told you there's a system that handles the business side while you focus on the {service_name.lower()}?

{{{{ hbpc.benefits | select_by_persona(persona) }}}}
*Visual: Before/after business transformation examples*

## Proof (45-70 seconds)  
{{{{ hbpc.proofs | select_by_persona(persona) }}}}
*Visual: Customer testimonials from similar {service_name.lower()} businesses*

## CTA (70-90 seconds)
Ready to transform {{{{ business_name }}}} from surviving to thriving?
{{{{ hbpc.ctas | select_by_persona(persona) }}}}
*Visual: Clean, professional business setup with contact information*

---
**Industry-Specific Notes:**
- Seasonality: {industry_config.seasonality}
- Key Services: {', '.join(industry_config.service_keywords)}
- Competition Level: {industry_config.competition_level}
- Digital Adoption: {industry_config.digital_adoption}
"""
        
        return template
    
    def _save_industry_template(self, industry_type: str, template_type: str, content: Any):
        """Save industry-specific template to database."""
        try:
            conn = sqlite3.connect(self.industry_db)
            cursor = conn.cursor()
            
            # Convert content to string if it's a dict
            content_str = json.dumps(content) if isinstance(content, dict) else str(content)
            
            cursor.execute('''
                INSERT OR REPLACE INTO industry_templates 
                (industry_type, template_type, template_name, template_content)
                VALUES (?, ?, ?, ?)
            ''', (
                industry_type,
                template_type,
                f"{industry_type}_{template_type}",
                content_str
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self._log(f"Error saving industry template: {e}")
    
    def get_industry_rankings(self) -> List[Dict]:
        """Get industries ranked by opportunity score."""
        try:
            conn = sqlite3.connect(self.industry_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT 
                    industry_type,
                    COUNT(*) as analyses_count,
                    AVG(opportunity_score) as avg_opportunity_score,
                    AVG(total_businesses_found) as avg_businesses_found,
                    MAX(analyzed_at) as last_analysis
                FROM industry_analysis
                GROUP BY industry_type
                ORDER BY avg_opportunity_score DESC
            ''')
            
            rankings = []
            for row in cursor.fetchall():
                industry_data = dict(row)
                if row['industry_type'] in self.industries:
                    industry_config = self.industries[row['industry_type']]
                    industry_data.update({
                        "industry_name": industry_config.name,
                        "market_size_estimate": industry_config.market_size_estimate,
                        "avg_revenue_estimate": industry_config.avg_revenue_estimate,
                        "competition_level": industry_config.competition_level
                    })
                rankings.append(industry_data)
            
            conn.close()
            return rankings
            
        except Exception as e:
            self._log(f"Error getting industry rankings: {e}")
            return []
    
    def create_multi_industry_campaign(self, campaign_name: str, target_industries: List[str],
                                     location: str = None) -> int:
        """Create a campaign targeting multiple industries."""
        try:
            # Analyze each industry if location provided
            total_businesses = 0
            
            if location:
                for industry in target_industries:
                    if industry in self.industries:
                        analysis = self.analyze_industry_opportunity(industry, location)
                        total_businesses += analysis.get("businesses_found", 0)
            
            # Save campaign
            conn = sqlite3.connect(self.industry_db)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO multi_industry_campaigns 
                (campaign_name, target_industries, businesses_targeted)
                VALUES (?, ?, ?)
            ''', (
                campaign_name,
                json.dumps(target_industries),
                total_businesses
            ))
            
            campaign_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            self._log(f"Created multi-industry campaign '{campaign_name}' targeting {len(target_industries)} industries")
            return campaign_id
            
        except Exception as e:
            self._log(f"Error creating multi-industry campaign: {e}")
            return 0
    
    def _run_custom_diagnostics(self) -> Optional[Dict[str, Any]]:
        """Run Industry Expansion agent-specific diagnostics."""
        try:
            diagnostics = {
                "industry_database": str(self.industry_db),
                "industry_db_exists": self.industry_db.exists(),
                "configured_industries": len(self.industries),
                "industry_types": list(self.industries.keys()),
                "market_research_enabled": self.market_research_enabled,
                "business_intel_ready": self.business_intel is not None,
                "template_engine_ready": self.template_engine is not None
            }
            
            # Database stats
            if self.industry_db.exists():
                conn = sqlite3.connect(self.industry_db)
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM industry_analysis")
                diagnostics["total_analyses"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(DISTINCT industry_type) FROM industry_analysis")
                diagnostics["analyzed_industries"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM industry_templates")
                diagnostics["industry_templates"] = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM multi_industry_campaigns")
                diagnostics["multi_industry_campaigns"] = cursor.fetchone()[0]
                
                conn.close()
            
            # Market size potential
            total_market_size = sum(config.market_size_estimate for config in self.industries.values())
            total_revenue_potential = sum(config.avg_revenue_estimate * config.market_size_estimate 
                                        for config in self.industries.values())
            
            diagnostics.update({
                "total_addressable_market": total_market_size,
                "total_revenue_potential": total_revenue_potential
            })
            
            return diagnostics
            
        except Exception as e:
            return {"diagnostics_error": str(e)}


if __name__ == "__main__":
    # Example usage
    agent = IndustryExpansionAgent()
    
    # Analyze HVAC opportunity in Austin
    hvac_analysis = agent.analyze_industry_opportunity("hvac_services", "Austin, TX")
    print("HVAC Analysis:", json.dumps(hvac_analysis, indent=2))
    
    # Generate industry-specific templates
    hvac_templates = agent.generate_industry_specific_templates("hvac_services")
    print("\\nHVAC Templates Generated:", len(hvac_templates))
    
    # Get industry rankings
    rankings = agent.get_industry_rankings()
    print("\\nIndustry Rankings:", rankings)
    
    # Create multi-industry campaign
    campaign_id = agent.create_multi_industry_campaign(
        "Texas Service Industries Q1", 
        ["auto_detailing", "hvac_services", "plumbing_services"],
        "Austin, TX"
    )
    print(f"\\nCreated campaign ID: {campaign_id}")
    
    # Get diagnostics
    diagnostics = agent._run_custom_diagnostics()
    print("\\nDiagnostics:", json.dumps(diagnostics, indent=2))