#!/usr/bin/env python3
"""
SINCOR Autonomous Upsell Engine
Automatically presents strategic upsells based on client profile and behavior
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum
import json
import time
from datetime import datetime, timedelta

class UpsellTrigger(Enum):
    IMMEDIATE_CHECKOUT = "immediate"      # At payment page
    POST_DELIVERY = "post_delivery"       # After BI report delivered  
    ENGAGEMENT_PEAK = "engagement"        # High engagement with report
    COMPETITIVE_ALERT = "competitive"     # Competitor intelligence triggers
    SEASONAL = "seasonal"                 # Time-based opportunities
    RENEWAL = "renewal"                   # Contract renewal time

@dataclass
class UpsellOffer:
    id: str
    name: str
    price: int
    original_price: int  # For showing savings
    description: str
    urgency_message: str
    value_props: List[str]
    trigger: UpsellTrigger
    success_rate: float  # Historical conversion rate
    expires_hours: int   # Time-limited offer
    media_assets: List[str]

@dataclass
class ClientProfile:
    company_name: str
    domain: str
    industry: str
    annual_revenue: int
    employee_count: int
    expansion_planned: bool
    competitive_pressure: str  # low, medium, high
    previous_purchases: List[str]
    engagement_score: float  # 0-1 based on report interaction
    decision_speed: str  # fast, medium, slow

class UpsellEngine:
    """Autonomous upsell engine that maximizes lifetime value"""
    
    def __init__(self):
        self.offers = self._initialize_upsell_offers()
        self.client_history = {}
        
    def _initialize_upsell_offers(self) -> Dict[str, UpsellOffer]:
        """Initialize all available upsell offers"""
        
        return {
            "media_pack_flash": UpsellOffer(
                id="media_pack_flash",
                name="Professional Media Pack - Flash Sale",
                price=2500,
                original_price=5000,
                description="Complete branding suite with logos, templates, and marketing materials",
                urgency_message="50% OFF - Limited to next 24 hours only",
                value_props=[
                    "Save $2,500 vs buying separately",
                    "Professional branding worth $5,000",
                    "Ready in 24 hours",
                    "Matches your market intelligence findings"
                ],
                trigger=UpsellTrigger.IMMEDIATE_CHECKOUT,
                success_rate=0.42,  # 42% conversion rate
                expires_hours=24,
                media_assets=["logo_suite", "social_templates", "presentation_deck"]
            ),
            
            "competitive_monitoring": UpsellOffer(
                id="competitive_monitoring",
                name="6-Month Competitive Intelligence Monitoring",
                price=1500,
                original_price=3000,
                description="Automated alerts on competitor moves, pricing changes, and market shifts",
                urgency_message="Your competitors are moving - stay ahead",
                value_props=[
                    "Real-time competitor intelligence",
                    "Automated market shift alerts", 
                    "6 months of continuous monitoring",
                    "ROI: Avoid one bad decision saves $50,000+"
                ],
                trigger=UpsellTrigger.POST_DELIVERY,
                success_rate=0.35,
                expires_hours=72,
                media_assets=["dashboard_preview", "alert_samples"]
            ),
            
            "implementation_roadmap": UpsellOffer(
                id="implementation_roadmap",
                name="90-Day Implementation Roadmap",
                price=2000,
                original_price=4000,
                description="Step-by-step action plan to execute your market intelligence findings",
                urgency_message="Turn insights into revenue - execution is everything",
                value_props=[
                    "Detailed 90-day action plan",
                    "Timeline with milestones and budgets",
                    "Success metrics and KPIs",
                    "Based on your specific market analysis"
                ],
                trigger=UpsellTrigger.ENGAGEMENT_PEAK,
                success_rate=0.38,
                expires_hours=48,
                media_assets=["roadmap_sample", "timeline_template"]
            ),
            
            "strategic_suite_upgrade": UpsellOffer(
                id="strategic_suite_upgrade", 
                name="Upgrade to Complete Strategic Suite",
                price=12500,  # Difference from $7,500 base
                original_price=17500,
                description="Full strategic intelligence package with ongoing monitoring and implementation tools",
                urgency_message="Complete your strategic advantage - limited enterprise slots",
                value_props=[
                    "Save $5,000 vs buying components separately",
                    "Media pack + Implementation + Monitoring included",
                    "Priority support and faster delivery",
                    "Quarterly strategy updates included"
                ],
                trigger=UpsellTrigger.IMMEDIATE_CHECKOUT,
                success_rate=0.25,
                expires_hours=6,  # Short urgency window
                media_assets=["complete_package_overview", "success_stories"]
            ),
            
            "market_entry_toolkit": UpsellOffer(
                id="market_entry_toolkit",
                name="Market Entry Execution Toolkit", 
                price=3000,
                original_price=5000,
                description="Legal templates, vendor lists, staffing guides - everything to execute expansion",
                urgency_message="Perfect timing - strike while market window is open",
                value_props=[
                    "Complete execution toolkit",
                    "Legal templates and compliance guides", 
                    "Vendor and supplier databases",
                    "Staffing and hiring frameworks"
                ],
                trigger=UpsellTrigger.COMPETITIVE_ALERT,
                success_rate=0.31,
                expires_hours=48,
                media_assets=["toolkit_preview", "template_samples"]
            ),
            
            "quarterly_reviews": UpsellOffer(
                id="quarterly_reviews",
                name="Quarterly Strategic Reviews - Annual Plan",
                price=8000,
                original_price=12000,
                description="Ongoing strategic optimization with quarterly performance reviews",
                urgency_message="Markets change fast - stay strategically current",
                value_props=[
                    "4 comprehensive quarterly reviews",
                    "Strategy adjustments based on performance",
                    "New opportunity identification",
                    "Continuous competitive monitoring"
                ],
                trigger=UpsellTrigger.RENEWAL,
                success_rate=0.28,
                expires_hours=120,  # 5-day decision window
                media_assets=["review_sample", "performance_dashboard"]
            )
        }
    
    def get_contextual_upsells(self, client: ClientProfile, trigger: UpsellTrigger, limit: int = 3) -> List[UpsellOffer]:
        """Get personalized upsells based on client profile and trigger"""
        
        relevant_offers = []
        
        for offer_id, offer in self.offers.items():
            # Skip if wrong trigger
            if offer.trigger != trigger:
                continue
                
            # Skip if client already purchased
            if offer_id in client.previous_purchases:
                continue
                
            # Contextual filtering
            score = self._calculate_offer_relevance(client, offer)
            if score > 0.3:  # Threshold for showing offer
                relevant_offers.append((score, offer))
        
        # Sort by relevance score and return top N
        relevant_offers.sort(key=lambda x: x[0], reverse=True)
        return [offer for score, offer in relevant_offers[:limit]]
    
    def _calculate_offer_relevance(self, client: ClientProfile, offer: UpsellOffer) -> float:
        """Calculate how relevant an offer is for this specific client"""
        
        score = 0.0
        
        # Base success rate
        score += offer.success_rate
        
        # Revenue-based scoring
        if client.annual_revenue > 10000000:  # $10M+
            score += 0.2
        elif client.annual_revenue > 5000000:  # $5M+
            score += 0.1
            
        # Industry-specific boosts
        if "media" in offer.name.lower() and client.industry in ["retail", "hospitality", "services"]:
            score += 0.15
            
        if "competitive" in offer.name.lower() and client.competitive_pressure == "high":
            score += 0.2
            
        if "implementation" in offer.name.lower() and client.expansion_planned:
            score += 0.25
            
        # Engagement boosts
        if client.engagement_score > 0.7:  # Highly engaged with report
            score += 0.15
            
        # Decision speed adjustments
        if client.decision_speed == "fast" and offer.expires_hours <= 24:
            score += 0.1
        elif client.decision_speed == "slow" and offer.expires_hours > 72:
            score += 0.05
            
        return min(1.0, score)  # Cap at 100%
    
    def generate_upsell_page(self, client: ClientProfile, offers: List[UpsellOffer]) -> Dict[str, Any]:
        """Generate dynamic upsell page content"""
        
        if not offers:
            return {"show_upsell": False}
            
        primary_offer = offers[0]  # Highest scoring offer
        secondary_offers = offers[1:3]  # Up to 2 additional offers
        
        return {
            "show_upsell": True,
            "client_name": client.company_name,
            "personalization": {
                "industry_context": f"Companies in {client.industry} typically see {primary_offer.success_rate:.0%} success with this upgrade",
                "revenue_context": f"At ${client.annual_revenue:,} revenue, this investment pays for itself in weeks",
                "urgency_context": primary_offer.urgency_message
            },
            "primary_offer": {
                "name": primary_offer.name,
                "price": primary_offer.price,
                "original_price": primary_offer.original_price,
                "savings": primary_offer.original_price - primary_offer.price,
                "savings_percent": round(((primary_offer.original_price - primary_offer.price) / primary_offer.original_price) * 100),
                "description": primary_offer.description,
                "value_props": primary_offer.value_props,
                "expires_at": datetime.now() + timedelta(hours=primary_offer.expires_hours),
                "media_assets": primary_offer.media_assets
            },
            "secondary_offers": [
                {
                    "name": offer.name,
                    "price": offer.price,
                    "savings": offer.original_price - offer.price,
                    "description": offer.description,
                    "quick_value": offer.value_props[0]  # Top value prop only
                }
                for offer in secondary_offers
            ],
            "social_proof": {
                "conversion_rate": f"{primary_offer.success_rate:.0%} of similar companies upgrade",
                "urgency_buyers": f"{int(primary_offer.success_rate * 100)} companies upgraded this month"
            },
            "risk_reversal": "100% satisfaction guarantee - if you don't see ROI in 90 days, full refund"
        }
    
    def track_upsell_performance(self, offer_id: str, client_profile: ClientProfile, outcome: str):
        """Track upsell performance for optimization"""
        
        timestamp = datetime.now().isoformat()
        
        performance_data = {
            "timestamp": timestamp,
            "offer_id": offer_id,
            "client_profile": {
                "industry": client_profile.industry,
                "revenue": client_profile.annual_revenue,
                "engagement": client_profile.engagement_score
            },
            "outcome": outcome,  # "converted", "declined", "abandoned"
            "trigger": self.offers[offer_id].trigger.value
        }
        
        # In production, save to database
        # For now, just log
        print(f"📊 Upsell Performance: {outcome} for {offer_id}")
        
        return performance_data
    
    def get_upsell_metrics(self) -> Dict[str, Any]:
        """Get upsell performance analytics"""
        
        total_revenue_potential = sum(offer.price for offer in self.offers.values())
        weighted_expected_revenue = sum(
            offer.price * offer.success_rate for offer in self.offers.values()
        )
        
        return {
            "total_offers": len(self.offers),
            "average_success_rate": sum(offer.success_rate for offer in self.offers.values()) / len(self.offers),
            "total_revenue_potential": total_revenue_potential,
            "expected_revenue_per_client": weighted_expected_revenue,
            "ltv_multiplier": weighted_expected_revenue / 7500,  # vs base package
            "offers_by_trigger": {
                trigger.value: len([o for o in self.offers.values() if o.trigger == trigger])
                for trigger in UpsellTrigger
            }
        }

# === USAGE EXAMPLES ===

def demo_upsell_engine():
    """Demonstrate the upsell engine with sample clients"""
    
    engine = UpsellEngine()
    
    # Sample client profiles
    clients = [
        ClientProfile(
            company_name="Acme Retail Chain",
            domain="acmeretail.com", 
            industry="retail",
            annual_revenue=15000000,
            employee_count=200,
            expansion_planned=True,
            competitive_pressure="high",
            previous_purchases=[],
            engagement_score=0.8,
            decision_speed="fast"
        ),
        
        ClientProfile(
            company_name="QuickServe Restaurants",
            domain="quickserve.com",
            industry="hospitality", 
            annual_revenue=5000000,
            employee_count=150,
            expansion_planned=False,
            competitive_pressure="medium",
            previous_purchases=["media_pack_flash"],
            engagement_score=0.6,
            decision_speed="medium"
        )
    ]
    
    print("🚀 SINCOR Upsell Engine Demo")
    print("=" * 50)
    
    # Test different triggers
    triggers = [UpsellTrigger.IMMEDIATE_CHECKOUT, UpsellTrigger.POST_DELIVERY, UpsellTrigger.ENGAGEMENT_PEAK]
    
    for client in clients:
        print(f"\n👤 Client: {client.company_name}")
        print(f"   Industry: {client.industry} | Revenue: ${client.annual_revenue:,}")
        
        for trigger in triggers:
            offers = engine.get_contextual_upsells(client, trigger, limit=2)
            
            if offers:
                print(f"\n   🎯 {trigger.value.title()} Trigger:")
                for offer in offers:
                    savings = offer.original_price - offer.price
                    print(f"      • {offer.name}: ${offer.price:,} (Save ${savings:,})")
                    
                # Generate full upsell page for first trigger
                if trigger == triggers[0]:
                    upsell_page = engine.generate_upsell_page(client, offers)
                    if upsell_page["show_upsell"]:
                        primary = upsell_page["primary_offer"]
                        print(f"\n   📄 Generated Upsell Page:")
                        print(f"      Primary: {primary['name']} - {primary['savings_percent']}% OFF")
                        print(f"      Urgency: {upsell_page['personalization']['urgency_context']}")
    
    # Show overall metrics
    print(f"\n📊 Upsell Engine Metrics:")
    metrics = engine.get_upsell_metrics()
    print(f"   Expected Revenue Per Client: ${metrics['expected_revenue_per_client']:,.0f}")
    print(f"   LTV Multiplier: {metrics['ltv_multiplier']:.1f}x")
    print(f"   Average Success Rate: {metrics['average_success_rate']:.1%}")

if __name__ == "__main__":
    demo_upsell_engine()