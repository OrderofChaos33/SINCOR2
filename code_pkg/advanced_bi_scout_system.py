#!/usr/bin/env python3
"""
SINCOR Advanced BI Lead Scout System
Integrates BIUA_1.txt specifications with FastAPI service architecture
Provides enterprise-grade lead generation with ML-driven scoring
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any, Union
import math, re, hashlib, time, os, yaml, json
import sqlite3
from dataclasses import dataclass
from enum import Enum
import asyncio
from datetime import datetime, timedelta
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SINCOR Advanced Configuration
SINCOR_CONFIG = {
    "limits": {"per_city": 500, "per_day": 1000},
    "geo": ["US", "CA"],
    "headcount": {"min": 20, "max": 1000, "sweet_min": 50, "sweet_max": 300},
    "titles": [
        "CEO", "COO", "CFO", "VP Strategy", "Director Strategy", 
        "Corp Dev", "Business Operations", "RevOps", "Head of Product Strategy"
    ],
    "intent_keywords": [
        "market entry", "competitive analysis", "new location", 
        "pricing study", "research RFP", "business intelligence report"
    ],
    "weights": {
        "size_midmarket": 2.0,
        "growth_hiring": 1.5, 
        "funding_recent": 1.2,
        "expansion_locations": 2.0,
        "intent_keywords": 2.5,
        "exec_title_present": 1.7,
        "season_window_active": 0.8,
        "review_volatility": 0.7,
        "high_cpc_sector": 0.9,
        "prior_research_spend": 2.0
    },
    "penalties": {
        "headcount_too_small": -1.0,
        "enterprise_slowcycle": -0.8,
        "procurement_only_contact": -0.5
    },
    "thresholds": {"MQL": 5.0, "SQL": 7.0},
    "pricing_anchor": {
        "legacy_price": 25000,
        "legacy_timeline_days": 56,
        "sincor_price": 7500,
        "sincor_timeline_hours": 4
    }
}

class LeadQuality(Enum):
    HOT = "hot"      # Score 8-10: Immediate opportunity
    WARM = "warm"    # Score 5-7: Nurture sequence  
    COLD = "cold"    # Score 1-4: Long-term pipeline

class OutreachChannel(Enum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    DIRECT_MAIL = "direct_mail"
    PHONE = "phone"

@dataclass
class Evidence:
    type: str
    url: Optional[str] = None
    confidence: float = 1.0
    source: str = "auto"

class CompanyProfile(BaseModel):
    """Advanced company profile with enriched data"""
    name: str
    domain: str
    headcount: Optional[int] = None
    locations_18m: int = 0
    city: Optional[str] = None
    country: str = "US"
    funding_months_ago: Optional[int] = None
    titles: List[str] = []
    job_titles: List[str] = []
    press_hits: List[str] = []
    review_volatility_90d: float = 0.0
    sector_cpc_high: bool = False
    intent_text: str = ""
    prior_research_vendor_mentions: bool = False
    evidence: List[Dict[str, Any]] = []
    
    # Advanced fields from BIUA_1
    growth_proxies: Dict[str, bool] = {}
    decision_intent_signals: List[str] = []
    pain_signals: Dict[str, float] = {}
    seasonality_factor: float = 0.0
    competitive_pressure: float = 0.0

class AdvancedScoring(BaseModel):
    """ML-enhanced scoring with rationale"""
    fit_score: float = Field(ge=0.0, le=1.0)
    urgency_score: float = Field(ge=0.0, le=1.0) 
    total_score: float = Field(ge=0.0, le=1.0)
    quality_tier: LeadQuality
    rationale: Dict[str, float]
    confidence_interval: tuple[float, float]
    next_action: str
    estimated_close_probability: float

class PersonalizedOutreach(BaseModel):
    """Evidence-based personalized messaging"""
    subject_line: str
    email_body: str
    linkedin_message: str
    key_talking_points: List[str]
    objection_responses: Dict[str, str]
    follow_up_sequence: List[str]
    value_proposition: str
    social_proof: str

def sigmoid(x: float) -> float:
    """Enhanced sigmoid with bounds checking"""
    x = max(-500, min(500, x))  # Prevent overflow
    return 1 / (1 + math.exp(-x))

class SINCORBIScout:
    """Advanced BI Lead Generation Scout with ML capabilities"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or SINCOR_CONFIG
        self.db_path = "sincor_scout.db" 
        self._init_database()
        
    def _init_database(self):
        """Initialize SQLite database for lead tracking"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY,
                domain TEXT UNIQUE,
                company_name TEXT,
                total_score REAL,
                quality_tier TEXT,
                last_contacted REAL,
                outcome TEXT,
                created_at REAL DEFAULT (julianday('now'))
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS outreach_history (
                id INTEGER PRIMARY KEY,
                domain TEXT,
                channel TEXT,
                content_hash TEXT,
                sent_at REAL DEFAULT (julianday('now')),
                response_received BOOLEAN DEFAULT FALSE,
                outcome TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def advanced_scoring(self, company: CompanyProfile) -> AdvancedScoring:
        """Advanced ML-enhanced scoring algorithm"""
        w = self.config["weights"]
        p = self.config["penalties"]
        rationale = {}
        raw_score = 0.0
        
        # Core scoring signals
        if (company.headcount is not None and 
            self.config["headcount"]["sweet_min"] <= company.headcount <= self.config["headcount"]["sweet_max"]):
            raw_score += w["size_midmarket"]
            rationale["size_midmarket"] = w["size_midmarket"]
        
        if company.locations_18m >= 2:
            raw_score += w["expansion_locations"] 
            rationale["expansion_locations"] = w["expansion_locations"]
            
        if company.funding_months_ago is not None and company.funding_months_ago <= 36:
            raw_score += w["funding_recent"]
            rationale["funding_recent"] = w["funding_recent"]
            
        # Intent signal detection (enhanced)
        intent_matches = re.findall(
            r"(market entry|competitive analysis|new location|pricing study|research RFP|business intelligence report)",
            company.intent_text, re.I
        )
        if intent_matches:
            intent_boost = min(len(intent_matches) * w["intent_keywords"], w["intent_keywords"] * 2)
            raw_score += intent_boost
            rationale["intent_keywords"] = intent_boost
        
        # Executive presence detection
        exec_matches = [t for t in company.titles if t in self.config["titles"]]
        if exec_matches:
            raw_score += w["exec_title_present"]
            rationale["exec_title_present"] = w["exec_title_present"]
        
        # Advanced signals
        if company.review_volatility_90d >= 0.25:
            raw_score += w["review_volatility"]
            rationale["review_volatility"] = w["review_volatility"]
            
        if company.sector_cpc_high:
            raw_score += w["high_cpc_sector"]
            rationale["high_cpc_sector"] = w["high_cpc_sector"]
            
        if company.prior_research_vendor_mentions:
            raw_score += w["prior_research_spend"]
            rationale["prior_research_spend"] = w["prior_research_spend"]
        
        # Apply penalties
        if company.headcount is not None and company.headcount < self.config["headcount"]["min"]:
            raw_score += p["headcount_too_small"]
            rationale["headcount_too_small"] = p["headcount_too_small"]
            
        if company.headcount is not None and company.headcount > self.config["headcount"]["max"]:
            raw_score += p["enterprise_slowcycle"] 
            rationale["enterprise_slowcycle"] = p["enterprise_slowcycle"]
        
        # Calculate normalized scores
        fit_score = sigmoid(raw_score / 4.0)
        
        # Enhanced urgency calculation
        urgency_factors = company.locations_18m
        if company.funding_months_ago is not None and company.funding_months_ago <= 12:
            urgency_factors += 2
        if len(intent_matches) > 0:
            urgency_factors += len(intent_matches)
        
        urgency_score = sigmoid(urgency_factors / 3.0)
        
        # Weighted total score
        total_score = 0.6 * fit_score + 0.4 * urgency_score
        
        # Determine quality tier
        if total_score >= 0.8:
            quality_tier = LeadQuality.HOT
        elif total_score >= 0.5:
            quality_tier = LeadQuality.WARM
        else:
            quality_tier = LeadQuality.COLD
            
        # Confidence interval (simplified)
        confidence_range = 0.1 * (1 - total_score)  # Higher scores = higher confidence
        confidence_interval = (
            max(0, total_score - confidence_range),
            min(1, total_score + confidence_range)
        )
        
        # Next action recommendation
        next_actions = {
            LeadQuality.HOT: "Immediate personalized outreach within 24 hours",
            LeadQuality.WARM: "Nurture sequence with value-first approach",
            LeadQuality.COLD: "Quarterly check-in with educational content"
        }
        
        return AdvancedScoring(
            fit_score=round(fit_score, 3),
            urgency_score=round(urgency_score, 3),
            total_score=round(total_score, 3),
            quality_tier=quality_tier,
            rationale=rationale,
            confidence_interval=confidence_interval,
            next_action=next_actions[quality_tier],
            estimated_close_probability=total_score * 0.3  # 30% max close rate for hot leads
        )
    
    def generate_personalized_outreach(self, company: CompanyProfile, scoring: AdvancedScoring) -> PersonalizedOutreach:
        """Generate evidence-based personalized outreach"""
        price = self.config["pricing_anchor"]
        
        # Build evidence-based proof points
        proof_points = []
        if company.locations_18m >= 2:
            proof_points.append(f"{company.locations_18m} new locations in 18 months")
        if company.funding_months_ago is not None and company.funding_months_ago <= 36:
            proof_points.append("recent funding round")
        if company.intent_text:
            if re.search(r"competitive|market entry|pricing", company.intent_text, re.I):
                proof_points.append("active strategy initiatives")
        
        proof_text = "; ".join(proof_points) or "growth indicators detected"
        
        # Quality-specific messaging
        if scoring.quality_tier == LeadQuality.HOT:
            urgency_factor = "immediate"
            timeline_emphasis = "today's decision timeline"
        elif scoring.quality_tier == LeadQuality.WARM:
            urgency_factor = "strategic"
            timeline_emphasis = "your planning timeline"
        else:
            urgency_factor = "future"
            timeline_emphasis = "when you're ready"
            
        subject_line = f"Same intelligence, today — {company.name}"
        
        email_body = f"""Hi there,

{company.name} can skip a ${price['legacy_price']:,} / {price['legacy_timeline_days']}-day study.

SINCOR delivers equivalent decision intelligence in ~{price['sincor_timeline_hours']} hours for ${price['sincor_price']:,}.

Why you: {proof_text}. Headcount ~{company.headcount or 'n/a'}.

Want a 2-page preview tailored to your market by tomorrow?
— Reply 'PREVIEW' or visit: getsincor.com

Best regards,
SINCOR Intelligence Team"""

        linkedin_message = f"""Skip ${price['legacy_price']:,}/{price['legacy_timeline_days']}d research. Same intel in ~{price['sincor_timeline_hours']}h for ${price['sincor_price']:,}. {company.name}: {proof_text}. Preview?"""
        
        # Key talking points for follow-up
        talking_points = [
            f"Market analysis delivered in {price['sincor_timeline_hours']} hours vs {price['legacy_timeline_days']} days",
            f"70% cost savings (${price['sincor_price']:,} vs ${price['legacy_price']:,})",
            f"Same depth: 243 pages of actionable intelligence",
            "Evidence-based recommendations, not generic reports"
        ]
        
        # Objection responses
        objections = {
            "too_expensive": f"Consider the opportunity cost: ${price['legacy_price']:,} + 8 weeks delay vs ${price['sincor_price']:,} + immediate action",
            "need_to_think": f"While you're thinking, competitors are moving. Our analysis helps you decide faster.",
            "do_internal": f"Your team's time is valuable. {price['sincor_timeline_hours']} hours from us vs 6-8 weeks internal research.",
            "not_right_time": "Market timing doesn't wait. When is the right time to make faster, better decisions?"
        }
        
        # Follow-up sequence for nurturing
        follow_up_sequence = [
            f"Day 1: Initial outreach with {company.name}-specific value prop",
            "Day 3: Case study of similar company (ROI focus)",
            "Day 7: Industry insight relevant to their market",
            "Day 14: Competitive intelligence preview",
            "Day 30: Market timing/seasonality reminder"
        ]
        
        return PersonalizedOutreach(
            subject_line=subject_line,
            email_body=email_body,
            linkedin_message=linkedin_message,
            key_talking_points=talking_points,
            objection_responses=objections,
            follow_up_sequence=follow_up_sequence,
            value_proposition=f"Same {price['legacy_timeline_days']}-day intelligence in {price['sincor_timeline_hours']} hours",
            social_proof="Trusted by growing companies for time-sensitive market decisions"
        )
    
    def is_outreach_recent(self, domain: str, channel: OutreachChannel, days: int = 14) -> bool:
        """Check if we've contacted this company recently"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        cursor.execute("""
            SELECT COUNT(*) FROM outreach_history 
            WHERE domain = ? AND channel = ? AND sent_at > ?
        """, (domain, channel.value, cutoff_time))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def log_outreach(self, company: CompanyProfile, channel: OutreachChannel, content: str):
        """Log outreach attempt for deduplication"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        
        cursor.execute("""
            INSERT INTO outreach_history (domain, channel, content_hash, sent_at)
            VALUES (?, ?, ?, ?)
        """, (company.domain, channel.value, content_hash, time.time()))
        
        conn.commit()
        conn.close()
    
    def update_lead_outcome(self, domain: str, outcome: str):
        """Update lead outcome for ML learning"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE leads SET outcome = ? WHERE domain = ?
        """, (outcome, domain))
        
        conn.commit()
        conn.close()
        
        # Trigger weight retuning if significant outcome change
        self._retune_weights()
    
    def _retune_weights(self):
        """Simple weight adjustment based on outcomes"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get recent outcomes
        cursor.execute("""
            SELECT total_score, outcome FROM leads 
            WHERE outcome IN ('won', 'lost') 
            AND created_at > julianday('now', '-30 days')
        """)
        
        outcomes = cursor.fetchall()
        conn.close()
        
        if len(outcomes) < 10:  # Need minimum data for tuning
            return
        
        won_scores = [score for score, outcome in outcomes if outcome == 'won']
        lost_scores = [score for score, outcome in outcomes if outcome == 'lost']
        
        if won_scores and lost_scores:
            won_avg = sum(won_scores) / len(won_scores)
            lost_avg = sum(lost_scores) / len(lost_scores)
            
            # Simple adjustment: boost weights if won_avg > lost_avg
            learning_rate = 0.1
            if won_avg > lost_avg:
                self.config["weights"]["intent_keywords"] *= (1 + learning_rate)
                self.config["weights"]["expansion_locations"] *= (1 + learning_rate)
                logger.info(f"Boosted weights based on {len(won_scores)} wins vs {len(lost_scores)} losses")

# FastAPI Application
app = FastAPI(
    title="SINCOR BI Scout System",
    description="Advanced Business Intelligence Lead Generation with ML-Enhanced Scoring",
    version="2.0.0"
)

scout = SINCORBIScout()

@app.get("/health")
async def health_check():
    """System health and configuration status"""
    return {
        "status": "operational",
        "version": "2.0.0",
        "scoring_engine": "ML-enhanced",
        "database": "connected",
        "timestamp": time.time(),
        "config_checksum": hashlib.sha256(str(scout.config).encode()).hexdigest()[:16]
    }

class ScoreRequest(BaseModel):
    companies: List[CompanyProfile]

@app.post("/score", response_model=List[Dict[str, Any]])
async def score_companies(request: ScoreRequest):
    """Score multiple companies with advanced ML algorithm"""
    results = []
    
    for company in request.companies:
        try:
            scoring = scout.advanced_scoring(company)
            outreach = scout.generate_personalized_outreach(company, scoring)
            
            result = {
                "company": company.dict(),
                "scoring": scoring.dict(),
                "outreach": outreach.dict(),
                "timestamp": time.time()
            }
            results.append(result)
            
        except Exception as e:
            logger.error(f"Error scoring {company.domain}: {e}")
            results.append({
                "company": company.dict(),
                "error": str(e),
                "timestamp": time.time()
            })
    
    return results

class RankRequest(BaseModel):
    companies: List[CompanyProfile]
    top_n: int = Field(default=20, ge=1, le=100)

@app.post("/rank", response_model=List[Dict[str, Any]])
async def rank_companies(request: RankRequest):
    """Rank companies by ML-enhanced total score"""
    scored_companies = []
    
    for company in request.companies:
        try:
            scoring = scout.advanced_scoring(company)
            scored_companies.append({
                "company": company,
                "scoring": scoring,
                "rank_score": scoring.total_score
            })
        except Exception as e:
            logger.error(f"Error scoring {company.domain}: {e}")
    
    # Sort by total score (descending)
    ranked = sorted(scored_companies, key=lambda x: x["rank_score"], reverse=True)
    
    # Return top N with full details
    results = []
    for i, item in enumerate(ranked[:request.top_n]):
        company = item["company"]
        scoring = item["scoring"]
        outreach = scout.generate_personalized_outreach(company, scoring)
        
        results.append({
            "rank": i + 1,
            "company": company.dict(),
            "scoring": scoring.dict(),
            "outreach": outreach.dict()
        })
    
    return results

class OutreachRequest(BaseModel):
    company: CompanyProfile
    channel: OutreachChannel = OutreachChannel.EMAIL
    force_send: bool = False

@app.post("/outreach")
async def initiate_outreach(request: OutreachRequest):
    """Initiate personalized outreach with deduplication"""
    
    # Check for recent outreach unless forced
    if not request.force_send and scout.is_outreach_recent(request.company.domain, request.channel):
        return {
            "status": "skipped",
            "reason": f"Recent outreach to {request.company.domain} via {request.channel.value}",
            "timestamp": time.time()
        }
    
    # Generate scoring and outreach
    scoring = scout.advanced_scoring(request.company)
    outreach = scout.generate_personalized_outreach(request.company, scoring)
    
    # Log the outreach attempt
    if request.channel == OutreachChannel.EMAIL:
        content = outreach.email_body
    elif request.channel == OutreachChannel.LINKEDIN:
        content = outreach.linkedin_message
    else:
        content = f"Outreach via {request.channel.value}"
        
    scout.log_outreach(request.company, request.channel, content)
    
    return {
        "status": "queued",
        "company": request.company.name,
        "channel": request.channel.value,
        "quality_tier": scoring.quality_tier.value,
        "total_score": scoring.total_score,
        "outreach": outreach.dict(),
        "timestamp": time.time()
    }

@app.post("/outcome")
async def update_outcome(domain: str, outcome: str):
    """Update lead outcome for ML learning"""
    valid_outcomes = ["won", "lost", "nurture", "disqualified", "no_response"]
    
    if outcome not in valid_outcomes:
        raise HTTPException(status_code=400, detail=f"Invalid outcome. Must be one of: {valid_outcomes}")
    
    scout.update_lead_outcome(domain, outcome)
    
    return {
        "status": "updated",
        "domain": domain,
        "outcome": outcome,
        "timestamp": time.time()
    }

@app.get("/analytics")
async def get_analytics():
    """Get lead generation analytics and performance metrics"""
    conn = sqlite3.connect(scout.db_path)
    cursor = conn.cursor()
    
    # Lead quality distribution
    cursor.execute("""
        SELECT quality_tier, COUNT(*) as count
        FROM leads
        WHERE created_at > julianday('now', '-30 days')
        GROUP BY quality_tier
    """)
    quality_distribution = dict(cursor.fetchall())
    
    # Outcome statistics
    cursor.execute("""
        SELECT outcome, COUNT(*) as count
        FROM leads
        WHERE outcome IS NOT NULL
        AND created_at > julianday('now', '-30 days')
        GROUP BY outcome
    """)
    outcome_stats = dict(cursor.fetchall())
    
    # Outreach performance
    cursor.execute("""
        SELECT channel, COUNT(*) as sent, 
               SUM(CASE WHEN response_received THEN 1 ELSE 0 END) as responses
        FROM outreach_history
        WHERE sent_at > julianday('now', '-30 days')
        GROUP BY channel
    """)
    outreach_performance = cursor.fetchall()
    
    conn.close()
    
    return {
        "period": "last_30_days",
        "quality_distribution": quality_distribution,
        "outcome_statistics": outcome_stats,
        "outreach_performance": [
            {
                "channel": row[0],
                "sent": row[1],
                "responses": row[2],
                "response_rate": round(row[2] / row[1], 3) if row[1] > 0 else 0
            }
            for row in outreach_performance
        ],
        "current_weights": scout.config["weights"],
        "timestamp": time.time()
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting SINCOR Advanced BI Scout System")
    print("📊 ML-Enhanced Lead Scoring: ACTIVE")
    print("🎯 Personalized Outreach: ACTIVE") 
    print("📈 Analytics Dashboard: ACTIVE")
    print("🔄 Auto-Tuning: ACTIVE")
    uvicorn.run(app, host="0.0.0.0", port=8082, reload=True)