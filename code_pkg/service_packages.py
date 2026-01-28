#!/usr/bin/env python3
"""
SINCOR Service Packages with Strategic Upsells
Every service designed to maximize lifetime customer value
"""

from dataclasses import dataclass
from typing import List, Dict, Any
from enum import Enum

class ServiceTier(Enum):
    INSTANT_BI = "instant_bi"           # $7,500 - Core 4-hour BI
    BI_PLUS = "bi_plus"                 # $12,500 - BI + Media Pack
    STRATEGIC_SUITE = "strategic_suite"  # $25,000 - Full strategic package
    ENTERPRISE = "enterprise"           # $50,000+ - Ongoing partnership

@dataclass
class ServiceComponent:
    name: str
    description: str
    delivery_time: str
    value_prop: str
    cost_to_deliver: float = 0.0

@dataclass 
class ServicePackage:
    tier: ServiceTier
    name: str
    price: int
    components: List[ServiceComponent]
    upsells: List[str]
    media_assets: List[str]
    automation_level: float  # 0-1, how automated delivery is

# === CORE SERVICE COMPONENTS ===

INSTANT_BI_CORE = ServiceComponent(
    name="4-Hour Market Intelligence Report",
    description="243-page comprehensive market analysis with competitor mapping, entry strategies, and risk assessment",
    delivery_time="4 hours",
    value_prop="Same intelligence as $25,000 consultants in 4 hours vs 75 days",
    cost_to_deliver=500.0  # Mostly automated
)

MEDIA_PACK_PREMIUM = ServiceComponent(
    name="Professional Media & Graphics Pack",
    description="Custom logo designs, social media templates, presentation decks, and brand guidelines",
    delivery_time="24 hours", 
    value_prop="$5,000 value branding package included free",
    cost_to_deliver=200.0  # Template-based with customization
)

COMPETITIVE_MONITORING = ServiceComponent(
    name="6-Month Competitive Intelligence Monitoring",
    description="Automated alerts on competitor moves, pricing changes, new locations, and market shifts",
    delivery_time="Ongoing",
    value_prop="Stay ahead of competition with real-time intelligence",
    cost_to_deliver=100.0  # Fully automated
)

IMPLEMENTATION_ROADMAP = ServiceComponent(
    name="90-Day Implementation Roadmap",
    description="Step-by-step action plan with timelines, budgets, and success metrics",
    delivery_time="48 hours",
    value_prop="Turn insights into action with proven implementation framework",
    cost_to_deliver=300.0  # Template + customization
)

MARKET_ENTRY_TOOLKIT = ServiceComponent(
    name="Market Entry Execution Toolkit", 
    description="Legal templates, vendor lists, staffing guides, and launch checklists",
    delivery_time="24 hours",
    value_prop="Everything needed to execute expansion immediately",
    cost_to_deliver=150.0  # Digital template library
)

QUARTERLY_REVIEWS = ServiceComponent(
    name="Quarterly Strategic Reviews",
    description="90-day performance analysis with strategy adjustments and new opportunities",
    delivery_time="Quarterly",
    value_prop="Continuous strategic optimization for sustained growth",
    cost_to_deliver=400.0  # Semi-automated with human oversight
)

# === SERVICE PACKAGES ===

PACKAGES = {
    ServiceTier.INSTANT_BI: ServicePackage(
        tier=ServiceTier.INSTANT_BI,
        name="Instant Business Intelligence",
        price=7500,
        components=[INSTANT_BI_CORE],
        upsells=[
            "Add Professional Media Pack (+$2,500) - Limited time offer",
            "6-Month Competitive Monitoring (+$1,500) - Stay ahead of competition", 
            "Implementation Roadmap (+$2,000) - Turn insights into action"
        ],
        media_assets=[
            "Executive summary presentation (PowerPoint)",
            "Key findings infographic", 
            "Competitor comparison chart",
            "Market opportunity visualization"
        ],
        automation_level=0.95
    ),
    
    ServiceTier.BI_PLUS: ServicePackage(
        tier=ServiceTier.BI_PLUS,
        name="BI Plus Media Suite",
        price=12500,
        components=[INSTANT_BI_CORE, MEDIA_PACK_PREMIUM, IMPLEMENTATION_ROADMAP],
        upsells=[
            "Competitive Monitoring Package (+$1,500) - 50% off first year",
            "Market Entry Toolkit (+$3,000) - Execute immediately",
            "Upgrade to Strategic Suite (+$12,500) - Full ongoing partnership"
        ],
        media_assets=[
            "Professional logo suite (5 variations)",
            "Social media template library (50+ posts)",
            "Presentation template with branding",
            "Brand guidelines document",
            "Business card and letterhead designs",
            "Website mockup and color palette"
        ],
        automation_level=0.90
    ),
    
    ServiceTier.STRATEGIC_SUITE: ServicePackage(
        tier=ServiceTier.STRATEGIC_SUITE, 
        name="Complete Strategic Intelligence Suite",
        price=25000,
        components=[
            INSTANT_BI_CORE, 
            MEDIA_PACK_PREMIUM, 
            IMPLEMENTATION_ROADMAP,
            COMPETITIVE_MONITORING,
            MARKET_ENTRY_TOOLKIT
        ],
        upsells=[
            "Quarterly Strategic Reviews (+$8,000/year) - Ongoing optimization",
            "Priority Support & Expedited Delivery (+$5,000) - VIP treatment",
            "Enterprise Partnership (+$25,000/year) - Unlimited intelligence"
        ],
        media_assets=[
            "Complete brand identity system",
            "Marketing campaign templates", 
            "Investor presentation template",
            "Website wireframes and content",
            "Video script templates",
            "PR kit and media templates"
        ],
        automation_level=0.85
    ),
    
    ServiceTier.ENTERPRISE: ServicePackage(
        tier=ServiceTier.ENTERPRISE,
        name="Enterprise Intelligence Partnership", 
        price=50000,
        components=[
            INSTANT_BI_CORE,
            MEDIA_PACK_PREMIUM,
            IMPLEMENTATION_ROADMAP, 
            COMPETITIVE_MONITORING,
            MARKET_ENTRY_TOOLKIT,
            QUARTERLY_REVIEWS
        ],
        upsells=[
            "Additional Market Analysis (+$7,500 each) - Expand to new regions",
            "Custom Research Projects (+$10,000-$25,000) - Specialized intelligence", 
            "White-label Licensing (+$100,000/year) - Resell our intelligence"
        ],
        media_assets=[
            "Full multimedia brand system",
            "Video production templates and scripts",
            "Complete marketing automation setup",
            "Custom web portal for reports",
            "Mobile app mockups and flows", 
            "Trade show and event materials"
        ],
        automation_level=0.75  # More human touch for enterprise
    )
}

# === UPSELL AUTOMATION ===

def get_smart_upsells(client_profile: Dict[str, Any], current_package: ServiceTier) -> List[Dict[str, Any]]:
    """Generate contextual upsells based on client profile"""
    
    upsells = []
    
    # Revenue-based upsells
    if client_profile.get('annual_revenue', 0) > 10000000:  # $10M+ revenue
        upsells.append({
            "offer": "Enterprise Partnership Upgrade",
            "price": 50000,
            "savings": "Save $15,000 vs individual services",
            "urgency": "Limited to 10 enterprise clients per quarter"
        })
    
    # Growth stage upsells
    if client_profile.get('expansion_planned', False):
        upsells.append({
            "offer": "Market Entry Toolkit",
            "price": 3000,
            "savings": "Avoid $50,000+ expansion mistakes", 
            "urgency": "Execute expansion in 30 days vs 6 months"
        })
    
    # Competitive pressure upsells
    if client_profile.get('competitive_pressure') == 'high':
        upsells.append({
            "offer": "6-Month Competitive Monitoring",
            "price": 1500,
            "savings": "Stay ahead of competitors moving faster",
            "urgency": "Market window closing - act now"
        })
        
    # Industry-specific upsells
    industry = client_profile.get('industry', '').lower()
    if 'retail' in industry or 'restaurant' in industry:
        upsells.append({
            "offer": "Location Intelligence Add-on", 
            "price": 5000,
            "savings": "Optimize site selection with 95% accuracy",
            "urgency": "Perfect locations get snatched up fast"
        })
    
    return upsells

def calculate_lifetime_value(initial_package: ServiceTier, upsell_probability: float = 0.3) -> Dict[str, float]:
    """Calculate expected lifetime value with upsells"""
    
    base_price = PACKAGES[initial_package].price
    
    # Average upsell values by tier
    upsell_values = {
        ServiceTier.INSTANT_BI: 4000,      # Media pack + monitoring
        ServiceTier.BI_PLUS: 8000,         # Strategic suite upgrade  
        ServiceTier.STRATEGIC_SUITE: 15000, # Enterprise + quarterly
        ServiceTier.ENTERPRISE: 25000      # Additional projects
    }
    
    expected_upsell = upsell_values[initial_package] * upsell_probability
    repeat_business = base_price * 0.4  # 40% likely to use again
    referral_value = base_price * 0.2   # 20% referral rate
    
    return {
        "initial_sale": base_price,
        "expected_upsells": expected_upsell,
        "repeat_business": repeat_business, 
        "referral_value": referral_value,
        "total_ltv": base_price + expected_upsell + repeat_business + referral_value
    }

# === DELIVERY AUTOMATION ===

def get_delivery_timeline(package: ServicePackage) -> Dict[str, str]:
    """Get automated delivery schedule"""
    
    timeline = {}
    
    for i, component in enumerate(package.components):
        if "4 hours" in component.delivery_time:
            timeline[f"Hour {4}"] = component.name
        elif "24 hours" in component.delivery_time:
            timeline[f"Day {1}"] = component.name  
        elif "48 hours" in component.delivery_time:
            timeline[f"Day {2}"] = component.name
        elif "Ongoing" in component.delivery_time:
            timeline["Continuous"] = component.name
        elif "Quarterly" in component.delivery_time:
            timeline["Every 90 Days"] = component.name
    
    return timeline

def calculate_profit_margins(package: ServicePackage) -> Dict[str, float]:
    """Calculate profit margins for each package"""
    
    total_cost = sum(comp.cost_to_deliver for comp in package.components)
    revenue = package.price
    profit = revenue - total_cost
    margin = (profit / revenue) * 100
    
    return {
        "revenue": revenue,
        "costs": total_cost,
        "profit": profit,
        "margin_percent": round(margin, 1),
        "automation_savings": total_cost * (1 - package.automation_level)
    }

# === EXAMPLE USAGE ===

if __name__ == "__main__":
    print("🚀 SINCOR Service Package Analysis")
    print("=" * 50)
    
    for tier, package in PACKAGES.items():
        print(f"\n📦 {package.name} - ${package.price:,}")
        print(f"   Components: {len(package.components)}")
        print(f"   Automation Level: {package.automation_level:.0%}")
        
        margins = calculate_profit_margins(package)
        print(f"   Profit Margin: {margins['margin_percent']}%")
        
        ltv = calculate_lifetime_value(tier)
        print(f"   Expected LTV: ${ltv['total_ltv']:,.0f}")
        
        print(f"   Upsells Available: {len(package.upsells)}")
        
    print(f"\n💰 Total Revenue Potential per Client:")
    print(f"   Entry Level: $7,500 → ${calculate_lifetime_value(ServiceTier.INSTANT_BI)['total_ltv']:,.0f} LTV")
    print(f"   Enterprise: $50,000 → ${calculate_lifetime_value(ServiceTier.ENTERPRISE)['total_ltv']:,.0f} LTV")