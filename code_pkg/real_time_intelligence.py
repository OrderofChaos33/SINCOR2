#!/usr/bin/env python3
"""
SINCOR Real-Time Market Intelligence & Live Competitive Intelligence

Gives agents access to live data streams that update every few minutes:
- Real-time stock prices, news, social media sentiment
- Competitor website changes, pricing updates, job postings
- Industry trend detection from multiple sources
- Live market conditions for instant strategy adjustment
- Event-driven intelligence triggers

This is what makes SINCOR agents 10x better than static consultants.
"""

import json
import asyncio
import os
import hashlib
from typing import Dict, List, Optional, Any, Tuple, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import time
from collections import defaultdict, deque

class IntelligenceSource(Enum):
    """Types of real-time intelligence sources"""
    FINANCIAL_MARKETS = "financial_markets"
    NEWS_FEEDS = "news_feeds" 
    SOCIAL_MEDIA = "social_media"
    COMPETITOR_WEBSITES = "competitor_websites"
    JOB_POSTINGS = "job_postings"
    PATENT_FILINGS = "patent_filings"
    REGULATORY_FILINGS = "regulatory_filings"
    INDUSTRY_REPORTS = "industry_reports"
    SEARCH_TRENDS = "search_trends"
    PRICING_INTELLIGENCE = "pricing_intelligence"

class AlertSeverity(Enum):
    """Severity levels for intelligence alerts"""
    LOW = "low"
    MEDIUM = "medium" 
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class IntelligenceDataPoint:
    """Single piece of real-time intelligence"""
    data_id: str
    source: IntelligenceSource
    timestamp: str
    content: Dict[str, Any]
    confidence: float
    relevance_score: float
    affected_entities: List[str]  # Companies, industries, keywords
    alert_level: AlertSeverity
    expiry_time: str  # When this data becomes stale
    source_url: Optional[str] = None
    
@dataclass  
class IntelligenceAlert:
    """Alert triggered by significant intelligence"""
    alert_id: str
    trigger_data_ids: List[str]
    alert_type: str  # competitor_move, market_shift, opportunity, threat
    severity: AlertSeverity
    title: str
    description: str
    affected_agents: List[str]
    recommended_actions: List[str]
    confidence: float
    created: str

@dataclass
class MarketConditions:
    """Current market condition snapshot"""
    condition_id: str
    industry: str
    overall_sentiment: float  # -1.0 to 1.0
    volatility_index: float   # 0.0 to 1.0
    growth_indicators: Dict[str, float]
    risk_factors: List[str]
    opportunities: List[str]
    last_updated: str

class RealTimeIntelligenceEngine:
    """Real-time intelligence collection and analysis engine"""
    
    def __init__(self):
        self.engine_id = f"intel_{uuid.uuid4().hex[:8]}"
        
        # Data storage
        self.live_data = {}  # source -> deque of recent data points
        self.active_alerts = []
        self.market_conditions = {}  # industry -> MarketConditions
        
        # Monitoring configuration
        self.monitored_entities = defaultdict(list)  # entity -> [source types]
        self.alert_thresholds = self._initialize_alert_thresholds()
        self.data_sources = self._initialize_data_sources()
        
        # Performance tracking
        self.intelligence_quality_scores = []
        self.alert_accuracy_history = []
        self.data_freshness_metrics = {}
        
        # Event-driven triggers
        self.event_handlers = {}  # event_type -> [callback functions]
        self.intelligence_cache = {}
        
    def _initialize_alert_thresholds(self) -> Dict[str, Dict[str, float]]:
        """Initialize alert thresholds for different intelligence types"""
        
        return {
            "stock_price_change": {
                "medium": 0.05,    # 5% price change
                "high": 0.10,      # 10% price change  
                "critical": 0.20   # 20% price change
            },
            "news_sentiment_shift": {
                "medium": 0.3,     # 30% sentiment change
                "high": 0.5,       # 50% sentiment change
                "critical": 0.7    # 70% sentiment change
            },
            "competitor_pricing_change": {
                "medium": 0.08,    # 8% pricing change
                "high": 0.15,      # 15% pricing change
                "critical": 0.25   # 25% pricing change
            },
            "job_posting_surge": {
                "medium": 2.0,     # 2x normal posting rate
                "high": 3.0,       # 3x normal posting rate
                "critical": 5.0    # 5x normal posting rate
            },
            "search_trend_spike": {
                "medium": 1.5,     # 1.5x normal search volume
                "high": 2.5,       # 2.5x normal search volume
                "critical": 4.0    # 4x normal search volume
            }
        }
    
    def _initialize_data_sources(self) -> Dict[IntelligenceSource, Dict[str, Any]]:
        """Initialize configuration for each intelligence source"""
        
        return {
            IntelligenceSource.FINANCIAL_MARKETS: {
                "update_frequency": 60,    # Seconds between updates
                "endpoints": ["yahoo_finance", "alpha_vantage", "iex_cloud"],
                "rate_limits": {"requests_per_minute": 300},
                "priority": "high",
                "cost_per_request": 0.001
            },
            IntelligenceSource.NEWS_FEEDS: {
                "update_frequency": 300,   # 5 minutes
                "endpoints": ["newsapi", "google_news", "reuters", "bloomberg"],
                "rate_limits": {"requests_per_minute": 100},
                "priority": "high",
                "cost_per_request": 0.002
            },
            IntelligenceSource.SOCIAL_MEDIA: {
                "update_frequency": 180,   # 3 minutes
                "endpoints": ["twitter_api", "reddit_api", "linkedin_api"],
                "rate_limits": {"requests_per_minute": 50},
                "priority": "medium",
                "cost_per_request": 0.001
            },
            IntelligenceSource.COMPETITOR_WEBSITES: {
                "update_frequency": 1800,  # 30 minutes
                "endpoints": ["web_scraping", "change_detection"],
                "rate_limits": {"requests_per_minute": 20},
                "priority": "medium",
                "cost_per_request": 0.005
            },
            IntelligenceSource.JOB_POSTINGS: {
                "update_frequency": 3600,  # 1 hour
                "endpoints": ["indeed_api", "linkedin_jobs", "glassdoor"],
                "rate_limits": {"requests_per_minute": 30},
                "priority": "low",
                "cost_per_request": 0.003
            },
            IntelligenceSource.SEARCH_TRENDS: {
                "update_frequency": 900,   # 15 minutes
                "endpoints": ["google_trends", "bing_trends"],
                "rate_limits": {"requests_per_minute": 40},
                "priority": "medium",
                "cost_per_request": 0.002
            },
            IntelligenceSource.PRICING_INTELLIGENCE: {
                "update_frequency": 3600,  # 1 hour
                "endpoints": ["price_monitoring", "competitor_pricing"],
                "rate_limits": {"requests_per_minute": 15},
                "priority": "high",
                "cost_per_request": 0.01
            }
        }
    
    async def start_real_time_monitoring(self, monitored_entities: Dict[str, List[str]]):
        """Start real-time monitoring for specified entities"""
        
        print(f"[INTEL] Starting real-time monitoring for {len(monitored_entities)} entities")
        
        # Store monitored entities
        self.monitored_entities.update(monitored_entities)
        
        # Start monitoring tasks for each source
        monitoring_tasks = []
        
        for source, config in self.data_sources.items():
            task = asyncio.create_task(
                self._monitor_source(source, config)
            )
            monitoring_tasks.append(task)
        
        # Start alert processing
        alert_task = asyncio.create_task(self._process_intelligence_alerts())
        monitoring_tasks.append(alert_task)
        
        # Start market condition analysis
        market_task = asyncio.create_task(self._analyze_market_conditions())
        monitoring_tasks.append(market_task)
        
        print(f"[INTEL] Started {len(monitoring_tasks)} monitoring tasks")
        
        # Run monitoring indefinitely
        await asyncio.gather(*monitoring_tasks, return_exceptions=True)
    
    async def _monitor_source(self, source: IntelligenceSource, config: Dict[str, Any]):
        """Monitor a specific intelligence source continuously"""
        
        update_frequency = config["update_frequency"]
        
        while True:
            try:
                # Collect data from this source
                data_points = await self._collect_source_data(source, config)
                
                # Store data points
                if source not in self.live_data:
                    self.live_data[source] = deque(maxlen=1000)  # Keep last 1000 points
                
                for data_point in data_points:
                    self.live_data[source].append(data_point)
                    
                    # Check for alert conditions
                    alerts = await self._check_alert_conditions(data_point)
                    self.active_alerts.extend(alerts)
                
                # Update freshness metrics
                self.data_freshness_metrics[source.value] = datetime.now().isoformat()
                
                if data_points:
                    print(f"[INTEL] {source.value}: collected {len(data_points)} data points")
                
            except Exception as e:
                print(f"[INTEL] Error monitoring {source.value}: {e}")
            
            # Wait before next update
            await asyncio.sleep(update_frequency)
    
    async def _collect_source_data(self, source: IntelligenceSource, 
                                 config: Dict[str, Any]) -> List[IntelligenceDataPoint]:
        """Collect data from specific source (mock implementation)"""
        
        data_points = []
        
        # Mock data collection based on source type
        if source == IntelligenceSource.FINANCIAL_MARKETS:
            data_points = await self._collect_financial_data()
        elif source == IntelligenceSource.NEWS_FEEDS:
            data_points = await self._collect_news_data()
        elif source == IntelligenceSource.SOCIAL_MEDIA:
            data_points = await self._collect_social_media_data()
        elif source == IntelligenceSource.COMPETITOR_WEBSITES:
            data_points = await self._collect_competitor_data()
        elif source == IntelligenceSource.JOB_POSTINGS:
            data_points = await self._collect_job_posting_data()
        elif source == IntelligenceSource.SEARCH_TRENDS:
            data_points = await self._collect_search_trend_data()
        elif source == IntelligenceSource.PRICING_INTELLIGENCE:
            data_points = await self._collect_pricing_data()
        
        return data_points
    
    async def _collect_financial_data(self) -> List[IntelligenceDataPoint]:
        """Collect financial market data"""
        
        # Mock financial data
        mock_stocks = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]
        data_points = []
        
        for stock in mock_stocks:
            if stock in [entity for entities in self.monitored_entities.values() for entity in entities]:
                # Simulate price movement
                import random
                price_change = (random.random() - 0.5) * 0.1  # -5% to +5%
                
                data_point = IntelligenceDataPoint(
                    data_id=f"fin_{stock}_{int(time.time())}",
                    source=IntelligenceSource.FINANCIAL_MARKETS,
                    timestamp=datetime.now().isoformat(),
                    content={
                        "symbol": stock,
                        "price_change_percent": price_change,
                        "volume_change": random.uniform(-0.3, 0.5),
                        "market_cap_change": price_change * random.uniform(0.8, 1.2)
                    },
                    confidence=0.95,
                    relevance_score=abs(price_change) * 2,  # Higher changes more relevant
                    affected_entities=[stock],
                    alert_level=self._calculate_financial_alert_level(price_change),
                    expiry_time=(datetime.now() + timedelta(minutes=5)).isoformat()
                )
                data_points.append(data_point)
        
        return data_points
    
    def _calculate_financial_alert_level(self, price_change: float) -> AlertSeverity:
        """Calculate alert level for financial data"""
        
        abs_change = abs(price_change)
        thresholds = self.alert_thresholds["stock_price_change"]
        
        if abs_change >= thresholds["critical"]:
            return AlertSeverity.CRITICAL
        elif abs_change >= thresholds["high"]:
            return AlertSeverity.HIGH
        elif abs_change >= thresholds["medium"]:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _collect_news_data(self) -> List[IntelligenceDataPoint]:
        """Collect news feed data"""
        
        # Mock news data
        mock_news_items = [
            {
                "title": "Tech Giant Announces Major AI Partnership",
                "sentiment": 0.7,
                "entities": ["GOOGL", "MSFT"],
                "impact_score": 0.8
            },
            {
                "title": "New Regulations Could Impact SaaS Industry",
                "sentiment": -0.4,
                "entities": ["SaaS", "CRM"],
                "impact_score": 0.6
            },
            {
                "title": "Market Leader Launches Competitive Product",
                "sentiment": 0.2,
                "entities": ["competitor_analysis"],
                "impact_score": 0.9
            }
        ]
        
        data_points = []
        
        for news_item in mock_news_items:
            # Check if any monitored entities are affected
            relevant_entities = []
            for entity_list in self.monitored_entities.values():
                relevant_entities.extend([e for e in news_item["entities"] if e in entity_list])
            
            if relevant_entities:
                data_point = IntelligenceDataPoint(
                    data_id=f"news_{hashlib.sha256(news_item['title'].encode()).hexdigest()[:16]}",
                    source=IntelligenceSource.NEWS_FEEDS,
                    timestamp=datetime.now().isoformat(),
                    content=news_item,
                    confidence=0.85,
                    relevance_score=news_item["impact_score"],
                    affected_entities=relevant_entities,
                    alert_level=self._calculate_news_alert_level(news_item["sentiment"], news_item["impact_score"]),
                    expiry_time=(datetime.now() + timedelta(hours=24)).isoformat(),
                    source_url="https://example-news-source.com"
                )
                data_points.append(data_point)
        
        return data_points
    
    def _calculate_news_alert_level(self, sentiment: float, impact_score: float) -> AlertSeverity:
        """Calculate alert level for news data"""
        
        # Combine sentiment magnitude and impact
        alert_score = abs(sentiment) * impact_score
        
        if alert_score >= 0.7:
            return AlertSeverity.CRITICAL
        elif alert_score >= 0.5:
            return AlertSeverity.HIGH
        elif alert_score >= 0.3:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _collect_social_media_data(self) -> List[IntelligenceDataPoint]:
        """Collect social media sentiment data"""
        
        import random
        
        # Mock social media data
        data_points = []
        
        for entity_list in self.monitored_entities.values():
            for entity in entity_list[:3]:  # Limit to avoid spam
                sentiment_change = random.uniform(-0.5, 0.5)
                
                data_point = IntelligenceDataPoint(
                    data_id=f"social_{entity}_{int(time.time())}",
                    source=IntelligenceSource.SOCIAL_MEDIA,
                    timestamp=datetime.now().isoformat(),
                    content={
                        "entity": entity,
                        "sentiment_change": sentiment_change,
                        "mention_volume": random.randint(50, 500),
                        "trending_topics": [f"topic_{i}" for i in range(random.randint(1, 4))]
                    },
                    confidence=0.75,
                    relevance_score=abs(sentiment_change),
                    affected_entities=[entity],
                    alert_level=self._calculate_social_alert_level(sentiment_change),
                    expiry_time=(datetime.now() + timedelta(hours=2)).isoformat()
                )
                data_points.append(data_point)
        
        return data_points
    
    def _calculate_social_alert_level(self, sentiment_change: float) -> AlertSeverity:
        """Calculate alert level for social media data"""
        
        abs_change = abs(sentiment_change)
        thresholds = self.alert_thresholds["news_sentiment_shift"]
        
        if abs_change >= thresholds["critical"]:
            return AlertSeverity.CRITICAL
        elif abs_change >= thresholds["high"]:
            return AlertSeverity.HIGH
        elif abs_change >= thresholds["medium"]:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _collect_competitor_data(self) -> List[IntelligenceDataPoint]:
        """Collect competitor website changes"""
        
        import random
        
        mock_competitor_changes = [
            {
                "competitor": "CompetitorA",
                "change_type": "pricing_update",
                "details": {"price_change_percent": random.uniform(-0.15, 0.20)},
                "impact": random.uniform(0.3, 0.9)
            },
            {
                "competitor": "CompetitorB", 
                "change_type": "new_feature",
                "details": {"feature": "AI-powered analytics", "launch_date": "2025-02-01"},
                "impact": random.uniform(0.5, 1.0)
            }
        ]
        
        data_points = []
        
        for change in mock_competitor_changes:
            data_point = IntelligenceDataPoint(
                data_id=f"comp_{change['competitor']}_{int(time.time())}",
                source=IntelligenceSource.COMPETITOR_WEBSITES,
                timestamp=datetime.now().isoformat(),
                content=change,
                confidence=0.80,
                relevance_score=change["impact"],
                affected_entities=[change["competitor"]],
                alert_level=self._calculate_competitor_alert_level(change),
                expiry_time=(datetime.now() + timedelta(days=7)).isoformat()
            )
            data_points.append(data_point)
        
        return data_points
    
    def _calculate_competitor_alert_level(self, change: Dict[str, Any]) -> AlertSeverity:
        """Calculate alert level for competitor changes"""
        
        impact = change["impact"]
        
        if change["change_type"] == "pricing_update":
            price_change = abs(change["details"].get("price_change_percent", 0))
            if price_change >= 0.15:
                return AlertSeverity.CRITICAL
        
        if impact >= 0.8:
            return AlertSeverity.HIGH
        elif impact >= 0.6:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _collect_job_posting_data(self) -> List[IntelligenceDataPoint]:
        """Collect job posting trends"""
        
        import random
        
        # Mock job posting data indicating company growth/contraction
        data_points = []
        
        companies = ["TechCorpA", "StartupB", "EnterpriseC"]
        
        for company in companies:
            if company in [entity for entities in self.monitored_entities.values() for entity in entities]:
                posting_surge = random.uniform(0.5, 4.0)  # 0.5x to 4x normal
                
                data_point = IntelligenceDataPoint(
                    data_id=f"jobs_{company}_{int(time.time())}",
                    source=IntelligenceSource.JOB_POSTINGS,
                    timestamp=datetime.now().isoformat(),
                    content={
                        "company": company,
                        "posting_rate_multiplier": posting_surge,
                        "top_roles": ["Software Engineer", "Product Manager", "Sales Rep"],
                        "locations": ["SF", "NYC", "Remote"]
                    },
                    confidence=0.85,
                    relevance_score=max(0, posting_surge - 1),  # Above-normal posting is relevant
                    affected_entities=[company],
                    alert_level=self._calculate_job_alert_level(posting_surge),
                    expiry_time=(datetime.now() + timedelta(days=3)).isoformat()
                )
                data_points.append(data_point)
        
        return data_points
    
    def _calculate_job_alert_level(self, posting_surge: float) -> AlertSeverity:
        """Calculate alert level for job posting data"""
        
        thresholds = self.alert_thresholds["job_posting_surge"]
        
        if posting_surge >= thresholds["critical"]:
            return AlertSeverity.CRITICAL
        elif posting_surge >= thresholds["high"]:
            return AlertSeverity.HIGH
        elif posting_surge >= thresholds["medium"]:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _collect_search_trend_data(self) -> List[IntelligenceDataPoint]:
        """Collect search trend data"""
        
        import random
        
        # Mock search trend data
        data_points = []
        
        keywords = ["AI tools", "CRM software", "business intelligence", "market research"]
        
        for keyword in keywords:
            if keyword in [entity for entities in self.monitored_entities.values() for entity in entities]:
                trend_multiplier = random.uniform(0.7, 3.5)  # 0.7x to 3.5x normal search volume
                
                data_point = IntelligenceDataPoint(
                    data_id=f"trends_{keyword.replace(' ', '_')}_{int(time.time())}",
                    source=IntelligenceSource.SEARCH_TRENDS,
                    timestamp=datetime.now().isoformat(),
                    content={
                        "keyword": keyword,
                        "trend_multiplier": trend_multiplier,
                        "geographic_data": {"US": 1.2, "UK": 0.9, "DE": 1.1},
                        "related_queries": [f"related_{i}" for i in range(3)]
                    },
                    confidence=0.90,
                    relevance_score=max(0, trend_multiplier - 1),
                    affected_entities=[keyword],
                    alert_level=self._calculate_trend_alert_level(trend_multiplier),
                    expiry_time=(datetime.now() + timedelta(hours=6)).isoformat()
                )
                data_points.append(data_point)
        
        return data_points
    
    def _calculate_trend_alert_level(self, trend_multiplier: float) -> AlertSeverity:
        """Calculate alert level for search trend data"""
        
        thresholds = self.alert_thresholds["search_trend_spike"]
        
        if trend_multiplier >= thresholds["critical"]:
            return AlertSeverity.CRITICAL
        elif trend_multiplier >= thresholds["high"]:
            return AlertSeverity.HIGH
        elif trend_multiplier >= thresholds["medium"]:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _collect_pricing_data(self) -> List[IntelligenceDataPoint]:
        """Collect pricing intelligence data"""
        
        import random
        
        # Mock pricing data
        data_points = []
        
        products = ["ProductA", "ProductB", "ProductC"]
        
        for product in products:
            if product in [entity for entities in self.monitored_entities.values() for entity in entities]:
                price_change = random.uniform(-0.25, 0.30)  # -25% to +30%
                
                data_point = IntelligenceDataPoint(
                    data_id=f"price_{product}_{int(time.time())}",
                    source=IntelligenceSource.PRICING_INTELLIGENCE,
                    timestamp=datetime.now().isoformat(),
                    content={
                        "product": product,
                        "price_change_percent": price_change,
                        "competitor_comparison": {"vs_competitor_1": price_change * 0.8},
                        "market_position": "premium" if price_change > 0.1 else "competitive"
                    },
                    confidence=0.90,
                    relevance_score=abs(price_change),
                    affected_entities=[product],
                    alert_level=self._calculate_pricing_alert_level(price_change),
                    expiry_time=(datetime.now() + timedelta(days=1)).isoformat()
                )
                data_points.append(data_point)
        
        return data_points
    
    def _calculate_pricing_alert_level(self, price_change: float) -> AlertSeverity:
        """Calculate alert level for pricing data"""
        
        abs_change = abs(price_change)
        thresholds = self.alert_thresholds["competitor_pricing_change"]
        
        if abs_change >= thresholds["critical"]:
            return AlertSeverity.CRITICAL
        elif abs_change >= thresholds["high"]:
            return AlertSeverity.HIGH
        elif abs_change >= thresholds["medium"]:
            return AlertSeverity.MEDIUM
        else:
            return AlertSeverity.LOW
    
    async def _check_alert_conditions(self, data_point: IntelligenceDataPoint) -> List[IntelligenceAlert]:
        """Check if data point triggers any alerts"""
        
        alerts = []
        
        # Alert condition 1: High severity data point
        if data_point.alert_level in [AlertSeverity.HIGH, AlertSeverity.CRITICAL]:
            alert = IntelligenceAlert(
                alert_id=f"alert_{uuid.uuid4().hex[:8]}",
                trigger_data_ids=[data_point.data_id],
                alert_type="significant_change",
                severity=data_point.alert_level,
                title=f"{data_point.source.value} Alert: {data_point.affected_entities}",
                description=f"Significant change detected: {data_point.content}",
                affected_agents=await self._determine_relevant_agents(data_point),
                recommended_actions=await self._generate_recommended_actions(data_point),
                confidence=data_point.confidence,
                created=datetime.now().isoformat()
            )
            alerts.append(alert)
        
        # Alert condition 2: Pattern-based alerts (multiple data points)
        pattern_alerts = await self._check_pattern_alerts(data_point)
        alerts.extend(pattern_alerts)
        
        return alerts
    
    async def _determine_relevant_agents(self, data_point: IntelligenceDataPoint) -> List[str]:
        """Determine which agents should be notified of this intelligence"""
        
        relevant_agents = []
        
        # Match agents based on their specializations and the data point
        agent_specialization_map = {
            IntelligenceSource.FINANCIAL_MARKETS: ["E-auriga-01", "E-polaris-09"],
            IntelligenceSource.NEWS_FEEDS: ["E-vega-02", "E-sirius-08"],
            IntelligenceSource.COMPETITOR_WEBSITES: ["E-spica-05", "E-arcturus-10"],
            IntelligenceSource.SOCIAL_MEDIA: ["E-sirius-08", "E-procyon-14"],
            IntelligenceSource.PRICING_INTELLIGENCE: ["E-polaris-09", "E-betelgeuse-11"]
        }
        
        relevant_agents = agent_specialization_map.get(data_point.source, [])
        
        return relevant_agents
    
    async def _generate_recommended_actions(self, data_point: IntelligenceDataPoint) -> List[str]:
        """Generate recommended actions based on intelligence"""
        
        actions = []
        
        if data_point.source == IntelligenceSource.FINANCIAL_MARKETS:
            price_change = data_point.content.get("price_change_percent", 0)
            if abs(price_change) > 0.1:  # 10% change
                actions.append(f"Analyze market impact of {price_change:.1%} price movement")
                actions.append("Review competitor positioning in response")
                
        elif data_point.source == IntelligenceSource.NEWS_FEEDS:
            sentiment = data_point.content.get("sentiment", 0)
            if sentiment < -0.5:
                actions.append("Assess negative sentiment impact on market position")
                actions.append("Prepare crisis communication strategy")
            elif sentiment > 0.5:
                actions.append("Capitalize on positive market sentiment")
                actions.append("Accelerate marketing initiatives")
                
        elif data_point.source == IntelligenceSource.COMPETITOR_WEBSITES:
            change_type = data_point.content.get("change_type")
            if change_type == "pricing_update":
                actions.append("Conduct competitive pricing analysis")
                actions.append("Evaluate pricing strategy adjustment")
            elif change_type == "new_feature":
                actions.append("Assess competitive threat from new feature")
                actions.append("Prioritize feature development roadmap")
        
        # Default actions
        if not actions:
            actions.append("Monitor situation for additional developments")
            actions.append("Update market intelligence reports")
        
        return actions
    
    async def _check_pattern_alerts(self, data_point: IntelligenceDataPoint) -> List[IntelligenceAlert]:
        """Check for pattern-based alerts across multiple data points"""
        
        pattern_alerts = []
        
        # Pattern 1: Coordinated competitor moves
        if data_point.source == IntelligenceSource.COMPETITOR_WEBSITES:
            recent_competitor_data = [
                dp for dp in self.live_data.get(IntelligenceSource.COMPETITOR_WEBSITES, [])
                if (datetime.now() - datetime.fromisoformat(dp.timestamp)).total_seconds() < 3600  # Last hour
            ]
            
            if len(recent_competitor_data) >= 3:  # Multiple competitor moves
                alert = IntelligenceAlert(
                    alert_id=f"pattern_alert_{uuid.uuid4().hex[:8]}",
                    trigger_data_ids=[dp.data_id for dp in recent_competitor_data],
                    alert_type="coordinated_competitive_moves",
                    severity=AlertSeverity.HIGH,
                    title="Multiple Competitor Actions Detected",
                    description=f"Detected {len(recent_competitor_data)} competitor moves in the last hour",
                    affected_agents=await self._determine_relevant_agents(data_point),
                    recommended_actions=[
                        "Conduct emergency competitive analysis",
                        "Review defensive strategies",
                        "Alert executive team"
                    ],
                    confidence=0.85,
                    created=datetime.now().isoformat()
                )
                pattern_alerts.append(alert)
        
        # Pattern 2: Market sentiment convergence
        if data_point.source == IntelligenceSource.NEWS_FEEDS:
            recent_news_data = [
                dp for dp in self.live_data.get(IntelligenceSource.NEWS_FEEDS, [])
                if (datetime.now() - datetime.fromisoformat(dp.timestamp)).total_seconds() < 1800  # Last 30 min
            ]
            
            if len(recent_news_data) >= 2:
                avg_sentiment = sum(dp.content.get("sentiment", 0) for dp in recent_news_data) / len(recent_news_data)
                
                if abs(avg_sentiment) > 0.6:  # Strong sentiment convergence
                    alert = IntelligenceAlert(
                        alert_id=f"sentiment_alert_{uuid.uuid4().hex[:8]}",
                        trigger_data_ids=[dp.data_id for dp in recent_news_data],
                        alert_type="market_sentiment_convergence",
                        severity=AlertSeverity.MEDIUM,
                        title=f"Strong {'Positive' if avg_sentiment > 0 else 'Negative'} Sentiment Trend",
                        description=f"Average sentiment: {avg_sentiment:.2f} across {len(recent_news_data)} recent news items",
                        affected_agents=await self._determine_relevant_agents(data_point),
                        recommended_actions=[
                            "Adjust marketing messaging to align with sentiment",
                            "Prepare strategic response plan",
                            "Monitor for sentiment continuation"
                        ],
                        confidence=0.80,
                        created=datetime.now().isoformat()
                    )
                    pattern_alerts.append(alert)
        
        return pattern_alerts
    
    async def _process_intelligence_alerts(self):
        """Process and distribute intelligence alerts"""
        
        while True:
            try:
                if self.active_alerts:
                    # Process pending alerts
                    alerts_to_process = self.active_alerts.copy()
                    self.active_alerts.clear()
                    
                    for alert in alerts_to_process:
                        await self._distribute_alert(alert)
                        
                        # Track alert for accuracy analysis
                        self.alert_accuracy_history.append({
                            "alert_id": alert.alert_id,
                            "created": alert.created,
                            "severity": alert.severity.value,
                            "confidence": alert.confidence
                        })
                
                # Clean up expired alerts and data
                await self._cleanup_expired_data()
                
            except Exception as e:
                print(f"[INTEL] Error processing alerts: {e}")
            
            await asyncio.sleep(30)  # Process alerts every 30 seconds
    
    async def _distribute_alert(self, alert: IntelligenceAlert):
        """Distribute alert to relevant agents and systems"""
        
        print(f"[ALERT] {alert.severity.value.upper()}: {alert.title}")
        print(f"[ALERT] Affected agents: {alert.affected_agents}")
        print(f"[ALERT] Actions: {alert.recommended_actions[:2]}")  # Show first 2 actions
        
        # In a real system, this would:
        # 1. Send notifications to affected agents
        # 2. Update agent task priorities
        # 3. Trigger emergency response protocols
        # 4. Update dashboards and reporting systems
    
    async def _cleanup_expired_data(self):
        """Clean up expired intelligence data"""
        
        now = datetime.now()
        cleaned_count = 0
        
        for source, data_deque in self.live_data.items():
            # Remove expired data points
            while data_deque and datetime.fromisoformat(data_deque[0].expiry_time) < now:
                data_deque.popleft()
                cleaned_count += 1
        
        if cleaned_count > 0:
            print(f"[INTEL] Cleaned up {cleaned_count} expired data points")
    
    async def _analyze_market_conditions(self):
        """Analyze overall market conditions across all intelligence"""
        
        while True:
            try:
                # Analyze market conditions for each industry
                industries = set()
                for entity_list in self.monitored_entities.values():
                    for entity in entity_list:
                        if entity in ["SaaS", "AI", "CRM", "fintech"]:  # Industry keywords
                            industries.add(entity)
                
                for industry in industries:
                    conditions = await self._calculate_market_conditions(industry)
                    self.market_conditions[industry] = conditions
                
                print(f"[INTEL] Updated market conditions for {len(industries)} industries")
                
            except Exception as e:
                print(f"[INTEL] Error analyzing market conditions: {e}")
            
            await asyncio.sleep(900)  # Update every 15 minutes
    
    async def _calculate_market_conditions(self, industry: str) -> MarketConditions:
        """Calculate market conditions for specific industry"""
        
        # Aggregate sentiment from recent data
        sentiment_scores = []
        volatility_indicators = []
        risk_factors = []
        opportunities = []
        
        # Analyze recent news sentiment
        for data_point in self.live_data.get(IntelligenceSource.NEWS_FEEDS, []):
            if industry in data_point.affected_entities:
                sentiment_scores.append(data_point.content.get("sentiment", 0))
        
        # Analyze financial volatility
        for data_point in self.live_data.get(IntelligenceSource.FINANCIAL_MARKETS, []):
            if any(entity in data_point.affected_entities for entity in [industry]):
                price_change = abs(data_point.content.get("price_change_percent", 0))
                volatility_indicators.append(price_change)
        
        # Identify risk factors and opportunities
        for data_point in self.live_data.get(IntelligenceSource.COMPETITOR_WEBSITES, []):
            if industry in data_point.affected_entities:
                change_type = data_point.content.get("change_type")
                if change_type == "pricing_update":
                    price_change = data_point.content.get("details", {}).get("price_change_percent", 0)
                    if price_change < -0.1:  # 10% price drop
                        risk_factors.append("Competitor pricing pressure")
                elif change_type == "new_feature":
                    opportunities.append("Feature gap opportunity identified")
        
        # Calculate overall metrics
        overall_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0
        volatility_index = sum(volatility_indicators) / len(volatility_indicators) if volatility_indicators else 0
        
        conditions = MarketConditions(
            condition_id=f"market_{industry}_{int(time.time())}",
            industry=industry,
            overall_sentiment=overall_sentiment,
            volatility_index=min(1.0, volatility_index * 5),  # Scale to 0-1
            growth_indicators={
                "sentiment_trend": overall_sentiment,
                "activity_level": len(sentiment_scores) / 10,  # Normalize activity
                "opportunity_score": len(opportunities) / max(1, len(risk_factors))
            },
            risk_factors=risk_factors,
            opportunities=opportunities,
            last_updated=datetime.now().isoformat()
        )
        
        return conditions
    
    def get_intelligence_dashboard(self) -> Dict[str, Any]:
        """Get comprehensive intelligence dashboard"""
        
        # Data freshness
        freshness_status = {}
        for source, last_update in self.data_freshness_metrics.items():
            age_minutes = (datetime.now() - datetime.fromisoformat(last_update)).total_seconds() / 60
            freshness_status[source] = {
                "last_update": last_update,
                "age_minutes": age_minutes,
                "status": "fresh" if age_minutes < 30 else "stale"
            }
        
        # Alert summary
        alert_summary = {
            "total_alerts": len(self.alert_accuracy_history),
            "recent_alerts": len([a for a in self.alert_accuracy_history 
                                if (datetime.now() - datetime.fromisoformat(a["created"])).total_seconds() < 3600]),
            "severity_distribution": {}
        }
        
        for severity in AlertSeverity:
            alert_summary["severity_distribution"][severity.value] = len([
                a for a in self.alert_accuracy_history if a["severity"] == severity.value
            ])
        
        # Data volume
        data_volume = {}
        for source, data_deque in self.live_data.items():
            data_volume[source.value] = len(data_deque)
        
        return {
            "engine_status": {
                "engine_id": self.engine_id,
                "monitored_entities": len(sum(self.monitored_entities.values(), [])),
                "active_sources": len([s for s, status in freshness_status.items() if status["status"] == "fresh"]),
                "market_conditions_tracked": len(self.market_conditions)
            },
            "data_freshness": freshness_status,
            "alert_summary": alert_summary,
            "data_volume": data_volume,
            "market_conditions": {industry: asdict(conditions) for industry, conditions in self.market_conditions.items()},
            "intelligence_quality": {
                "average_confidence": sum(self.intelligence_quality_scores) / len(self.intelligence_quality_scores) if self.intelligence_quality_scores else 0,
                "data_points_processed": sum(data_volume.values())
            }
        }
    
    def get_live_intelligence_for_agent(self, agent_specializations: List[str], 
                                      entities_of_interest: List[str]) -> List[IntelligenceDataPoint]:
        """Get relevant live intelligence for specific agent"""
        
        relevant_data = []
        
        # Map specializations to intelligence sources
        specialization_source_map = {
            "market_research": [IntelligenceSource.NEWS_FEEDS, IntelligenceSource.SEARCH_TRENDS],
            "competitive_analysis": [IntelligenceSource.COMPETITOR_WEBSITES, IntelligenceSource.PRICING_INTELLIGENCE],
            "financial_analysis": [IntelligenceSource.FINANCIAL_MARKETS],
            "social_monitoring": [IntelligenceSource.SOCIAL_MEDIA]
        }
        
        # Collect relevant sources
        relevant_sources = set()
        for spec in agent_specializations:
            relevant_sources.update(specialization_source_map.get(spec, []))
        
        # Gather relevant data points
        for source in relevant_sources:
            if source in self.live_data:
                for data_point in self.live_data[source]:
                    # Check if data point is relevant to agent's entities
                    if any(entity in data_point.affected_entities for entity in entities_of_interest):
                        # Check if data is still fresh
                        if datetime.fromisoformat(data_point.expiry_time) > datetime.now():
                            relevant_data.append(data_point)
        
        # Sort by relevance score and recency
        relevant_data.sort(key=lambda dp: (dp.relevance_score, dp.timestamp), reverse=True)
        
        return relevant_data[:20]  # Return top 20 most relevant points

async def main():
    """Demo real-time intelligence engine"""
    print("SINCOR Real-Time Intelligence Engine Demo")
    print("=" * 48)
    
    # Create intelligence engine
    engine = RealTimeIntelligenceEngine()
    
    # Set up monitoring for demo entities
    monitored_entities = {
        "financial": ["AAPL", "GOOGL", "MSFT"],
        "industries": ["SaaS", "AI", "CRM"],
        "competitors": ["CompetitorA", "CompetitorB"],
        "products": ["ProductA", "ProductB"]
    }
    
    print(f"Setting up monitoring for {sum(len(v) for v in monitored_entities.values())} entities")
    
    # Start monitoring (would run indefinitely in production)
    monitoring_task = asyncio.create_task(
        engine.start_real_time_monitoring(monitored_entities)
    )
    
    # Let it run for demo duration
    try:
        await asyncio.wait_for(monitoring_task, timeout=10)  # 10 second demo
    except asyncio.TimeoutError:
        monitoring_task.cancel()
        print("\nDemo monitoring completed")
    
    # Show dashboard
    dashboard = engine.get_intelligence_dashboard()
    print(f"\nIntelligence Dashboard:")
    for section, data in dashboard.items():
        print(f"\n{section.replace('_', ' ').title()}:")
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, dict):
                    print(f"  {key}: {len(value)} items")
                else:
                    print(f"  {key}: {value}")
        else:
            print(f"  {data}")

if __name__ == "__main__":
    asyncio.run(main())