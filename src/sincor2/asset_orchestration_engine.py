"""
SINCOR Asset Orchestration Engine
Coordinates creation, quality, and monetization of all asset types
Integrates: RecursiveValueEngine + QualityEngine + DynamicPricing + AgentCoordination
"""

import json
import uuid
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any
import sqlite3

logger = logging.getLogger('sincor2.asset_orchestration')

# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class AssetType(Enum):
    """All asset types SINCOR creates"""
    INTELLIGENCE_REPORT = "intelligence_report"
    PREDICTIVE_MODEL = "predictive_model"
    MARKET_ANALYSIS = "market_analysis"
    COMPETITIVE_INTELLIGENCE = "competitive_intelligence"
    AUTOMATION_SOLUTION = "automation_solution"
    CUSTOM_AI_AGENT = "custom_ai_agent"
    DATA_INSIGHTS = "data_insights"
    STRATEGIC_CONSULTING = "strategic_consulting"
    BI_DASHBOARD_TEMPLATE = "bi_dashboard_template"
    GROWTH_STRATEGY = "growth_strategy"
    INVESTMENT_RECOMMENDATION = "investment_recommendation"
    CUSTOMER_INSIGHTS = "customer_insights"

class AssetStatus(Enum):
    """Asset lifecycle status"""
    REQUESTED = "requested"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    QUALITY_REVIEW = "quality_review"
    REVISION_NEEDED = "revision_needed"
    APPROVED = "approved"
    DELIVERED = "delivered"
    ARCHIVED = "archived"
    FAILED = "failed"

class QualityStandard(Enum):
    """Quality tier standards"""
    STANDARD = "standard"       # 0.70-0.79 acceptable
    PREMIUM = "premium"         # 0.80-0.89 high quality
    ENTERPRISE = "enterprise"   # 0.90-1.00 production-ready

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class AssetRequest:
    """Customer asset request"""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    asset_type: AssetType = AssetType.INTELLIGENCE_REPORT
    client_id: str = ""
    client_tier: str = "mid_market"  # startup, small_business, mid_market, enterprise, fortune_500
    description: str = ""
    requirements: Dict[str, Any] = field(default_factory=dict)
    urgency_level: float = 1.0  # 0.5 (low) to 3.0 (emergency)
    budget: float = 0.0  # if 0, use dynamic pricing
    required_quality_tier: QualityStandard = QualityStandard.STANDARD
    deadline: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    properties: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AssetQualityMetrics:
    """Quality assessment across 9 dimensions"""
    accuracy: float = 0.0           # 0.6-0.9
    completeness: float = 0.0       # 0.75 minimum
    relevance: float = 0.0          # 0.6-0.9
    timeliness: float = 0.0         # speed factor
    clarity: float = 0.0            # presentation
    actionability: float = 0.0      # implementability
    innovation: float = 0.0         # novelty
    depth: float = 0.0              # thoroughness
    credibility: float = 0.0        # source reliability

    @property
    def overall_score(self) -> float:
        """Weighted average of all dimensions"""
        scores = [self.accuracy, self.completeness, self.relevance, self.timeliness,
                 self.clarity, self.actionability, self.innovation, self.depth, self.credibility]
        return sum(scores) / len(scores)

    @property
    def minimum_passed(self) -> bool:
        """Check if all dimensions meet minimum thresholds"""
        minimums = {
            'accuracy': 0.60, 'completeness': 0.75, 'relevance': 0.60,
            'timeliness': 0.50, 'clarity': 0.70, 'actionability': 0.65,
            'innovation': 0.40, 'depth': 0.60, 'credibility': 0.70
        }
        for dim, min_val in minimums.items():
            if getattr(self, dim) < min_val:
                return False
        return True

@dataclass
class AssetValueMetrics:
    """Value delivered and revenue generated"""
    total_revenue: float = 0.0
    base_price: float = 0.0
    dynamic_multipliers: Dict[str, float] = field(default_factory=dict)
    final_price: float = 0.0
    creation_cost: float = 0.0
    gross_margin: float = 0.0
    client_value_delivered: float = 0.0  # quantified business impact
    agent_contribution: Dict[str, float] = field(default_factory=dict)  # agent_id -> earnings

@dataclass
class Asset:
    """Complete asset record"""
    asset_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = ""
    asset_type: AssetType = AssetType.INTELLIGENCE_REPORT
    status: AssetStatus = AssetStatus.REQUESTED
    title: str = ""
    executive_summary: str = ""
    deliverable: Dict[str, Any] = field(default_factory=dict)

    # Quality tracking
    quality_metrics: AssetQualityMetrics = field(default_factory=AssetQualityMetrics)
    quality_tier: QualityStandard = QualityStandard.STANDARD
    quality_feedback: List[str] = field(default_factory=list)

    # Value tracking
    value_metrics: AssetValueMetrics = field(default_factory=AssetValueMetrics)

    # Execution tracking
    assigned_agents: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    completion_time: Optional[datetime] = None
    revision_count: int = 0

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    properties: Dict[str, Any] = field(default_factory=dict)

# ============================================================================
# ASSET ORCHESTRATION ENGINE
# ============================================================================

class AssetOrchestrationEngine:
    """Master orchestrator for all asset creation workflows"""

    def __init__(self, db_path: str = "asset_orchestration.db"):
        self.db_path = db_path
        self._init_db()
        self.quality_standards = self._load_quality_standards()
        self.asset_registry: Dict[str, Asset] = {}
        logger.info("Asset Orchestration Engine initialized")

    def _init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Asset requests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS asset_requests (
                request_id TEXT PRIMARY KEY,
                asset_type TEXT NOT NULL,
                client_id TEXT,
                client_tier TEXT,
                urgency_level REAL,
                budget REAL,
                quality_tier TEXT,
                deadline TEXT,
                created_at TEXT,
                request_data TEXT
            )
        """)

        # Assets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                request_id TEXT,
                asset_type TEXT,
                status TEXT,
                quality_score REAL,
                quality_tier TEXT,
                revenue REAL,
                creation_cost REAL,
                margin REAL,
                assigned_agents TEXT,
                created_at TEXT,
                completed_at TEXT,
                asset_data TEXT
            )
        """)

        # Quality assessments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quality_assessments (
                assessment_id TEXT PRIMARY KEY,
                asset_id TEXT,
                accuracy REAL,
                completeness REAL,
                relevance REAL,
                timeliness REAL,
                clarity REAL,
                actionability REAL,
                innovation REAL,
                depth REAL,
                credibility REAL,
                overall_score REAL,
                feedback TEXT,
                assessed_at TEXT
            )
        """)

        # Revenue tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS asset_revenue (
                asset_id TEXT PRIMARY KEY,
                base_price REAL,
                multipliers TEXT,
                final_price REAL,
                creation_cost REAL,
                margin REAL,
                client_value REAL,
                agent_splits TEXT,
                recorded_at TEXT
            )
        """)

        conn.commit()
        conn.close()

    def _load_quality_standards(self) -> Dict[AssetType, Dict[str, float]]:
        """Load quality standards per asset type"""
        return {
            AssetType.INTELLIGENCE_REPORT: {
                'accuracy': 0.85, 'completeness': 0.80, 'relevance': 0.85,
                'timeliness': 0.75, 'clarity': 0.85, 'actionability': 0.70,
                'innovation': 0.60, 'depth': 0.80, 'credibility': 0.85
            },
            AssetType.PREDICTIVE_MODEL: {
                'accuracy': 0.90, 'completeness': 0.85, 'relevance': 0.80,
                'timeliness': 0.70, 'clarity': 0.75, 'actionability': 0.85,
                'innovation': 0.75, 'depth': 0.90, 'credibility': 0.90
            },
            AssetType.CUSTOM_AI_AGENT: {
                'accuracy': 0.85, 'completeness': 0.90, 'relevance': 0.85,
                'timeliness': 0.60, 'clarity': 0.80, 'actionability': 0.90,
                'innovation': 0.85, 'depth': 0.85, 'credibility': 0.80
            },
            AssetType.AUTOMATION_SOLUTION: {
                'accuracy': 0.80, 'completeness': 0.85, 'relevance': 0.80,
                'timeliness': 0.65, 'clarity': 0.85, 'actionability': 0.90,
                'innovation': 0.70, 'depth': 0.75, 'credibility': 0.75
            },
        }

    def create_asset_request(self, asset_type: AssetType, client_id: str,
                            description: str, urgency: float = 1.0,
                            quality_tier: QualityStandard = QualityStandard.STANDARD,
                            client_tier: str = "mid_market", **kwargs) -> AssetRequest:
        """Create new asset request"""
        request = AssetRequest(
            asset_type=asset_type,
            client_id=client_id,
            description=description,
            urgency_level=urgency,
            required_quality_tier=quality_tier,
            client_tier=client_tier,
            properties=kwargs
        )

        # Store in DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO asset_requests
            (request_id, asset_type, client_id, client_tier, urgency_level,
             quality_tier, created_at, request_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            request.request_id, request.asset_type.value, client_id, client_tier,
            urgency, quality_tier.value, request.created_at.isoformat(),
            json.dumps(asdict(request), default=str)
        ))
        conn.commit()
        conn.close()

        logger.info(f"Asset request created: {request.request_id} ({asset_type.value})")
        return request

    def initiate_asset_creation(self, request: AssetRequest) -> Asset:
        """Start asset creation workflow"""
        asset = Asset(
            request_id=request.request_id,
            asset_type=request.asset_type,
            status=AssetStatus.PLANNING,
            title=f"{request.asset_type.value} for {request.client_id}",
            quality_tier=request.required_quality_tier,
            assigned_agents=[]
        )

        self.asset_registry[asset.asset_id] = asset

        logger.info(f"Asset creation initiated: {asset.asset_id}")
        return asset

    def assess_asset_quality(self, asset_id: str, metrics: AssetQualityMetrics) -> bool:
        """Assess asset quality and determine if it passes standards"""
        if asset_id not in self.asset_registry:
            logger.error(f"Asset not found: {asset_id}")
            return False

        asset = self.asset_registry[asset_id]
        asset.quality_metrics = metrics

        # Check against standards
        asset_standards = self.quality_standards.get(asset.asset_type, {})
        passed = True
        feedback = []

        for dim_name, min_score in asset_standards.items():
            actual = getattr(metrics, dim_name, 0.0)
            if actual < min_score:
                passed = False
                feedback.append(f"{dim_name}: {actual:.2f} (required: {min_score:.2f})")

        if passed and metrics.overall_score >= 0.75:
            asset.status = AssetStatus.APPROVED
            asset.quality_tier = self._determine_quality_tier(metrics.overall_score)
            logger.info(f"Asset approved: {asset_id} (score: {metrics.overall_score:.2f})")
        else:
            asset.status = AssetStatus.REVISION_NEEDED
            asset.revision_count += 1
            logger.warning(f"Asset revision needed: {asset_id} - {', '.join(feedback)}")

        asset.quality_feedback = feedback
        asset.updated_at = datetime.utcnow()

        return passed

    def _determine_quality_tier(self, score: float) -> QualityStandard:
        """Map quality score to tier"""
        if score >= 0.90:
            return QualityStandard.ENTERPRISE
        elif score >= 0.80:
            return QualityStandard.PREMIUM
        else:
            return QualityStandard.STANDARD

    def calculate_asset_value(self, asset_id: str, base_price: float = None,
                             complexity: str = "medium",
                             market_demand: float = 1.0,
                             customer_segment: str = "standard",
                             delivery_speed: str = "standard") -> AssetValueMetrics:
        """Calculate asset value with strategic pricing multipliers"""
        if asset_id not in self.asset_registry:
            logger.error(f"Asset not found: {asset_id}")
            return AssetValueMetrics()

        asset = self.asset_registry[asset_id]
        asset_type = asset.asset_type.value

        # If no base price provided, use standard for asset type
        if base_price is None:
            standard = self.quality_standards.get(asset_type, {})
            base_price = standard.get("base_price", 5000)

        # Use value attribution engine for strategic pricing
        from sincor2.value_standards_framework import value_attribution
        pricing_result = value_attribution.calculate_with_multipliers(
            asset_type=asset_type,
            base_quality_score=asset.quality_metrics.overall_score,
            market_demand=market_demand,
            customer_segment=customer_segment
        )

        final_price = pricing_result.get("final_price", base_price)

        # Costs & margin
        creation_cost = final_price * 0.20  # 20% cost ratio
        margin = final_price - creation_cost
        gross_margin = (margin / final_price) * 100 if final_price > 0 else 0

        # Store value metrics
        value_metrics = AssetValueMetrics(
            total_revenue=final_price,
            base_price=base_price,
            dynamic_multipliers={
                'quality': pricing_result.get("quality_multiplier", 1.0),
                'demand': pricing_result.get("demand_multiplier", 1.0),
                'segment_discount': pricing_result.get("segment_discount", 0),
                'customer_segment': customer_segment,
                'delivery_speed': delivery_speed
            },
            final_price=final_price,
            creation_cost=creation_cost,
            gross_margin=gross_margin,
            client_value_delivered=final_price * 3  # Estimated 3x value delivered to client
        )

        asset.value_metrics = value_metrics

        logger.info(f"Value calculated for {asset_id}: ${final_price:.2f} (margin: {gross_margin:.1f}%) | "
                   f"Segment: {customer_segment}, Demand: {market_demand:.1f}x")
        return value_metrics

    def complete_asset(self, asset_id: str):
        """Mark asset as completed and delivered"""
        if asset_id not in self.asset_registry:
            logger.error(f"Asset not found: {asset_id}")
            return

        asset = self.asset_registry[asset_id]
        asset.status = AssetStatus.DELIVERED
        asset.completion_time = datetime.utcnow()

        # Log to database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO assets
            (asset_id, request_id, asset_type, status, quality_score, quality_tier,
             revenue, creation_cost, margin, created_at, completed_at, asset_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            asset.asset_id, asset.request_id, asset.asset_type.value, asset.status.value,
            asset.quality_metrics.overall_score, asset.quality_tier.value,
            asset.value_metrics.final_price, asset.value_metrics.creation_cost,
            asset.value_metrics.gross_margin, asset.created_at.isoformat(),
            asset.completion_time.isoformat(), json.dumps(asdict(asset), default=str)
        ))
        conn.commit()
        conn.close()

        logger.info(f"Asset delivered: {asset_id}")

    def get_asset_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of all assets in system"""
        assets_list = list(self.asset_registry.values())

        if not assets_list:
            return {'status': 'no_assets'}

        quality_scores = [a.quality_metrics.overall_score for a in assets_list if a.status != AssetStatus.FAILED]
        revenues = [a.value_metrics.final_price for a in assets_list if a.status == AssetStatus.DELIVERED]

        return {
            'total_assets': len(assets_list),
            'pending': sum(1 for a in assets_list if a.status == AssetStatus.REQUESTED),
            'in_progress': sum(1 for a in assets_list if a.status in [AssetStatus.PLANNING, AssetStatus.IN_PROGRESS]),
            'quality_review': sum(1 for a in assets_list if a.status == AssetStatus.QUALITY_REVIEW),
            'approved': sum(1 for a in assets_list if a.status == AssetStatus.APPROVED),
            'delivered': sum(1 for a in assets_list if a.status == AssetStatus.DELIVERED),
            'avg_quality_score': sum(quality_scores) / len(quality_scores) if quality_scores else 0,
            'total_revenue_generated': sum(revenues),
            'avg_revenue_per_asset': sum(revenues) / len(revenues) if revenues else 0,
            'assets_by_type': self._count_by_type(assets_list),
            'asset_list': [asdict(a) for a in assets_list[:10]]  # Last 10
        }

    def _count_by_type(self, assets_list: List[Asset]) -> Dict[str, int]:
        """Count assets by type"""
        counts = {}
        for asset in assets_list:
            asset_type = asset.asset_type.value
            counts[asset_type] = counts.get(asset_type, 0) + 1
        return counts


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

asset_orchestrator = AssetOrchestrationEngine()
