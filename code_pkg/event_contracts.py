#!/usr/bin/env python3
"""
SINCOR Event Contracts - Shared across all 7 bots
Based on THISONE.txt alignment system
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
from datetime import datetime
from enum import Enum
import uuid

class EventType(str, Enum):
    DEMO = "demo"
    PURCHASE = "purchase"
    SETUP = "setup"
    DEPLOY = "deploy"
    TUTORIAL = "tutorial"
    SUPPORT = "support"
    UPSELL = "upsell"
    OVERSEER = "overseer"

class EventEnvelope(BaseModel):
    """Shared event envelope - import everywhere"""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    tenant_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payload: Dict[str, Any] = Field(default_factory=dict)

class ResultSchema(BaseModel):
    """Standard result schema - all bots return this"""
    ok: bool
    reason: str = ""
    outputs: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cents: int = 0
    artifacts: List[str] = Field(default_factory=list)

class ROIAnalysis(BaseModel):
    """For UPSELL bot - only trigger if >1.4x ROI"""
    winning_niche: str
    winning_city: str
    expected_roi: float
    rationale: str
    baseline_revenue: float
    projected_revenue: float

class TenantMetrics(BaseModel):
    """For OVERSEER monitoring"""
    tenant_id: str
    budget_used: float
    error_rate: float
    drift_score: float
    campaign_performance: Dict[str, float]
    last_activity: datetime