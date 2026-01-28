#!/usr/bin/env python3
"""
SINCOR UPSELL BOT - Full Builder Blueprint Implementation
Transforms one-time buyers into long-term, high-value subscribers
Stage ladder: Media Pack → Business Services → Membership → Premium
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from commons.event_contracts import EventEnvelope, ResultSchema
from loguru import logger
import sqlite3
import json

class UpsellBot:
    def __init__(self):
        self.db_path = "upsell_system.db"
        self.max_upsells_per_week = 1  # Guardrail: Max 1 upsell per tenant per 7 days
        self.roi_suppression_threshold = 1.0  # Suppress if ROI below baseline
        self.init_database()
        self.init_stage_system()
    
    def init_database(self):
        """Initialize comprehensive upsell tracking system"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Tenant subscription stages
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenant_stages (
                tenant_id TEXT PRIMARY KEY,
                current_stage TEXT DEFAULT 'media_pack',
                stage_started_at TEXT,
                last_upsell_at TEXT,
                metrics TEXT DEFAULT '{}',
                context TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        
        # Upsell event log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS upsell_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT,
                event_type TEXT,
                from_stage TEXT,
                to_stage TEXT,
                trigger_reason TEXT,
                dialogue_used TEXT,
                action_schema TEXT,
                conversion_result TEXT,
                created_at TEXT,
                FOREIGN KEY (tenant_id) REFERENCES tenant_stages (tenant_id)
            )
        """)
        
        # Passive nudge tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS passive_nudges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT,
                nudge_type TEXT,
                trigger_condition TEXT,
                nudge_content TEXT,
                action_taken TEXT,
                nudge_at TEXT,
                FOREIGN KEY (tenant_id) REFERENCES tenant_stages (tenant_id)
            )
        """)
        
        # Metrics and performance tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS upsell_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT,
                stage TEXT,
                leads_count INTEGER,
                reviews_count INTEGER,
                roi_percentage REAL,
                baseline_revenue REAL,
                current_revenue REAL,
                recorded_at TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def init_stage_system(self):
        """Initialize stage progression and dialogue templates"""
        self.stages = {
            "media_pack": {
                "next_stage": "business_services",
                "progression_triggers": ["after_media_pack_download"],
                "min_duration_days": 0
            },
            "business_services": {
                "next_stage": "membership",
                "progression_triggers": ["14_days_recurring", "threshold_leads"],
                "min_duration_days": 14
            },
            "membership": {
                "next_stage": "premium",
                "progression_triggers": ["60_days_active", "group_activity"],
                "min_duration_days": 60
            },
            "premium": {
                "next_stage": None,
                "progression_triggers": [],
                "min_duration_days": 0
            }
        }
        
        self.dialogue_templates = {
            "stage_1_to_2": {
                "template": """Your media pack is live. Want it working every week without lifting a finger?
I can auto-launch new ads, route leads, and track reviews for you.
[ Activate Weekly Automation ]""",
                "action": "action://upsell.add?target=business_services"
            },
            "stage_2_to_3": {
                "template": """You've got {leads_count} new leads and {reviews_count} fresh reviews this month.
Most businesses here run better with a membership:
• New media drops each month
• Seasonal packs auto-deployed
• Access to the insider vault + group chat

[ Join Membership ]""",
                "action": "action://upgrade_plan?tier=membership"
            },
            "stage_3_to_4": {
                "template": """Your campaigns are performing {roi_percentage}% above baseline.
I can open Premium access for you:
• White-glove monthly swarm packs tuned to your KPIs
• Priority concierge — type your idea, I build it same-day
• Private executive group with early feature drops

[ Unlock Premium ]""",
                "action": "action://upgrade_plan?tier=premium"
            }
        }
        
        self.passive_nudge_templates = {
            "idle_seasonal": {
                "template": "This month's seasonal pack is ready. Deploy it live?\n[ Deploy Pack ]",
                "action": "action://deploy.pack?id={seasonal_id}",
                "trigger_condition": "idle_7_days"
            },
            "roi_spike": {
                "template": "That campaign performed {roi_multiplier}x baseline ROI.\nWant me to replicate it in {target_location}?\n[ Add Pack ]",
                "action": "action://upsell.add?target={target_expansion}",
                "trigger_condition": "roi_above_120_percent"
            },
            "seasonal_window": {
                "template": "I've queued a holiday campaign. Activate it?\n[ Deploy Pack ]",
                "action": "action://deploy.pack?id={holiday_pack_id}",
                "trigger_condition": "seasonal_trigger"
            }
        }
    
    async def handle_upsell_check(self, envelope: EventEnvelope) -> ResultSchema:
        """Main upsell handler - determines if upsell should trigger"""
        tenant_id = envelope.tenant_id
        if not tenant_id:
            return ResultSchema(
                ok=False,
                reason="No tenant_id provided for upsell check"
            )
        
        # Check guardrails first
        guardrail_check = await self.check_upsell_guardrails(tenant_id)
        if not guardrail_check["allowed"]:
            return ResultSchema(
                ok=True,
                reason=f"Upsell suppressed: {guardrail_check['reason']}",
                outputs={"upsell_available": False, "suppression_reason": guardrail_check['reason']}
            )
        
        # Determine upsell type
        trigger_type = envelope.payload.get("trigger_type", "stage_progression")
        
        if trigger_type == "passive_nudge":
            return await self.handle_passive_nudge(tenant_id, envelope.payload)
        else:
            return await self.handle_stage_progression(tenant_id, envelope.payload)
    
    async def check_upsell_guardrails(self, tenant_id: str) -> Dict:
        """Check all upsell guardrails before proceeding"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check max upsell frequency (1 per 7 days)
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        cursor.execute("""
            SELECT COUNT(*) FROM upsell_events 
            WHERE tenant_id = ? AND created_at > ?
        """, (tenant_id, week_ago))
        
        recent_upsells = cursor.fetchone()[0]
        if recent_upsells >= self.max_upsells_per_week:
            conn.close()
            return {"allowed": False, "reason": "Max 1 upsell per 7 days exceeded"}
        
        # Check ROI suppression
        cursor.execute("""
            SELECT roi_percentage FROM upsell_metrics 
            WHERE tenant_id = ? ORDER BY recorded_at DESC LIMIT 1
        """, (tenant_id,))
        
        roi_result = cursor.fetchone()
        if roi_result and roi_result[0] < self.roi_suppression_threshold:
            conn.close()
            return {"allowed": False, "reason": f"ROI {roi_result[0]:.1%} below baseline"}
        
        # Check churn risk (placeholder - would integrate with actual churn detection)
        # churn_risk = await self.check_churn_risk(tenant_id)
        # if churn_risk:
        #     return {"allowed": False, "reason": "Churn risk flagged"}
        
        conn.close()
        return {"allowed": True, "reason": "All guardrails passed"}
    
    async def handle_stage_progression(self, tenant_id: str, payload: Dict) -> ResultSchema:
        """Handle stage-based upsell progression"""
        # Get current stage
        current_stage_info = await self.get_tenant_stage(tenant_id)
        current_stage = current_stage_info["stage"]
        
        if current_stage == "premium":
            return ResultSchema(
                ok=True,
                reason="Tenant already at premium tier",
                outputs={"upsell_available": False, "current_stage": "premium"}
            )
        
        # Check if progression is warranted
        progression_check = await self.check_stage_progression_readiness(tenant_id, current_stage, payload)
        
        if not progression_check["ready"]:
            return ResultSchema(
                ok=True,
                reason=f"Stage progression not ready: {progression_check['reason']}",
                outputs={"upsell_available": False, "progression_reason": progression_check['reason']}
            )
        
        # Generate stage progression upsell
        next_stage = self.stages[current_stage]["next_stage"]
        upsell_content = await self.generate_stage_upsell(tenant_id, current_stage, next_stage, payload)
        
        # Log upsell event
        await self.log_upsell_event(
            tenant_id, 
            "stage_progression", 
            current_stage, 
            next_stage, 
            progression_check['reason'],
            upsell_content["dialogue"],
            upsell_content["action"]
        )
        
        return ResultSchema(
            ok=True,
            reason="Stage progression upsell generated",
            outputs={
                "upsell_available": True,
                "current_stage": current_stage,
                "target_stage": next_stage,
                "dialogue": upsell_content["dialogue"],
                "action_schema": upsell_content["action"],
                "progression_reason": progression_check['reason']
            }
        )
    
    async def handle_passive_nudge(self, tenant_id: str, payload: Dict) -> ResultSchema:
        """Handle passive nudge upsells (idle, ROI spike, seasonal)"""
        nudge_type = payload.get("nudge_type", "idle_seasonal")
        nudge_data = payload.get("nudge_data", {})
        
        # Generate passive nudge content
        nudge_content = await self.generate_passive_nudge(tenant_id, nudge_type, nudge_data)
        
        # Log passive nudge
        await self.log_passive_nudge(tenant_id, nudge_type, nudge_content)
        
        return ResultSchema(
            ok=True,
            reason=f"Passive nudge generated: {nudge_type}",
            outputs={
                "upsell_available": True,
                "nudge_type": nudge_type,
                "dialogue": nudge_content["dialogue"],
                "action_schema": nudge_content["action"]
            }
        )
    
    async def check_stage_progression_readiness(self, tenant_id: str, current_stage: str, payload: Dict) -> Dict:
        """Check if tenant is ready for stage progression"""
        stage_config = self.stages[current_stage]
        
        # Check minimum duration in current stage
        stage_info = await self.get_tenant_stage(tenant_id)
        stage_started = datetime.fromisoformat(stage_info["started_at"])
        days_in_stage = (datetime.now() - stage_started).days
        
        if days_in_stage < stage_config["min_duration_days"]:
            return {
                "ready": False, 
                "reason": f"Only {days_in_stage} days in {current_stage}, need {stage_config['min_duration_days']}"
            }
        
        # Check stage-specific progression triggers
        triggers = stage_config["progression_triggers"]
        
        if current_stage == "media_pack":
            # Trigger after media pack download
            if "media_pack_downloaded" in payload:
                return {"ready": True, "reason": "Media pack download completed"}
        
        elif current_stage == "business_services":
            # Trigger after 14 days or threshold metrics
            if days_in_stage >= 14:
                metrics = await self.get_tenant_metrics(tenant_id)
                if metrics["leads_count"] >= 5 or metrics["reviews_count"] >= 3:
                    return {"ready": True, "reason": f"{metrics['leads_count']} leads, {metrics['reviews_count']} reviews"}
        
        elif current_stage == "membership":
            # Trigger after 60 days or high engagement
            if days_in_stage >= 60:
                return {"ready": True, "reason": "60+ days in membership"}
            
            # Check for high engagement/group activity
            engagement_score = payload.get("engagement_score", 0)
            if engagement_score > 0.8:
                return {"ready": True, "reason": "High engagement score"}
        
        return {"ready": False, "reason": "Progression triggers not met"}
    
    async def generate_stage_upsell(self, tenant_id: str, from_stage: str, to_stage: str, payload: Dict) -> Dict:
        """Generate stage progression upsell dialogue and action"""
        
        # Get metrics for personalization
        metrics = await self.get_tenant_metrics(tenant_id)
        tenant_context = await self.get_tenant_context(tenant_id)
        
        if from_stage == "media_pack" and to_stage == "business_services":
            template_key = "stage_1_to_2"
        elif from_stage == "business_services" and to_stage == "membership":
            template_key = "stage_2_to_3"
        elif from_stage == "membership" and to_stage == "premium":
            template_key = "stage_3_to_4"
        else:
            # Fallback template
            return {
                "dialogue": f"Your {from_stage} is performing well. Ready for {to_stage}?",
                "action": f"action://upgrade_plan?tier={to_stage}"
            }
        
        template_config = self.dialogue_templates[template_key]
        
        # Personalize dialogue with metrics
        dialogue = template_config["template"].format(
            leads_count=metrics.get("leads_count", 0),
            reviews_count=metrics.get("reviews_count", 0),
            roi_percentage=int(metrics.get("roi_percentage", 100)),
            city=tenant_context.get("city", "your market"),
            niche=tenant_context.get("niche", "your niche")
        )
        
        return {
            "dialogue": dialogue,
            "action": template_config["action"]
        }
    
    async def generate_passive_nudge(self, tenant_id: str, nudge_type: str, nudge_data: Dict) -> Dict:
        """Generate passive nudge content"""
        template_config = self.passive_nudge_templates.get(nudge_type, self.passive_nudge_templates["idle_seasonal"])
        
        # Get context for personalization
        tenant_context = await self.get_tenant_context(tenant_id)
        
        # Personalize nudge content
        if nudge_type == "roi_spike":
            dialogue = template_config["template"].format(
                roi_multiplier=nudge_data.get("roi_multiplier", "1.6"),
                target_location=nudge_data.get("target_location", "nearby markets")
            )
            action = template_config["action"].format(
                target_expansion=nudge_data.get("target_expansion", "geographic_expansion")
            )
        elif nudge_type == "seasonal_window":
            dialogue = template_config["template"]
            action = template_config["action"].format(
                holiday_pack_id=nudge_data.get("holiday_pack_id", "holiday_2024")
            )
        else:  # idle_seasonal
            dialogue = template_config["template"]
            action = template_config["action"].format(
                seasonal_id=nudge_data.get("seasonal_id", "seasonal_current")
            )
        
        return {
            "dialogue": dialogue,
            "action": action
        }
    
    async def get_tenant_stage(self, tenant_id: str) -> Dict:
        """Get tenant's current subscription stage"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT current_stage, stage_started_at, metrics, context 
            FROM tenant_stages WHERE tenant_id = ?
        """, (tenant_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            # Initialize new tenant at media_pack stage
            await self.initialize_tenant_stage(tenant_id)
            return {
                "stage": "media_pack",
                "started_at": datetime.now().isoformat(),
                "metrics": {},
                "context": {}
            }
        
        return {
            "stage": result[0],
            "started_at": result[1],
            "metrics": json.loads(result[2]) if result[2] else {},
            "context": json.loads(result[3]) if result[3] else {}
        }
    
    async def initialize_tenant_stage(self, tenant_id: str):
        """Initialize new tenant at media_pack stage"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO tenant_stages 
            (tenant_id, current_stage, stage_started_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (tenant_id, "media_pack", now, now, now))
        
        conn.commit()
        conn.close()
    
    async def get_tenant_metrics(self, tenant_id: str) -> Dict:
        """Get latest tenant metrics for personalization"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT leads_count, reviews_count, roi_percentage, current_revenue
            FROM upsell_metrics 
            WHERE tenant_id = ? ORDER BY recorded_at DESC LIMIT 1
        """, (tenant_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            # Default metrics for new tenants
            return {
                "leads_count": 0,
                "reviews_count": 0, 
                "roi_percentage": 100,
                "current_revenue": 0
            }
        
        return {
            "leads_count": result[0],
            "reviews_count": result[1],
            "roi_percentage": result[2],
            "current_revenue": result[3]
        }
    
    async def get_tenant_context(self, tenant_id: str) -> Dict:
        """Get tenant context for personalization"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT context FROM tenant_stages WHERE tenant_id = ?
        """, (tenant_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result[0]:
            return {"city": "your market", "niche": "your business"}
        
        return json.loads(result[0])
    
    async def log_upsell_event(self, tenant_id: str, event_type: str, from_stage: str, 
                              to_stage: str, trigger_reason: str, dialogue: str, action: str):
        """Log upsell event for tracking and analytics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO upsell_events 
            (tenant_id, event_type, from_stage, to_stage, trigger_reason, dialogue_used, action_schema, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (tenant_id, event_type, from_stage, to_stage, trigger_reason, dialogue, action, datetime.now().isoformat()))
        
        # Update last upsell timestamp
        cursor.execute("""
            UPDATE tenant_stages 
            SET last_upsell_at = ?, updated_at = ?
            WHERE tenant_id = ?
        """, (datetime.now().isoformat(), datetime.now().isoformat(), tenant_id))
        
        conn.commit()
        conn.close()
    
    async def log_passive_nudge(self, tenant_id: str, nudge_type: str, content: Dict):
        """Log passive nudge for tracking"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO passive_nudges 
            (tenant_id, nudge_type, trigger_condition, nudge_content, nudge_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            tenant_id, 
            nudge_type, 
            self.passive_nudge_templates[nudge_type]["trigger_condition"],
            json.dumps(content),
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()

# Bot instance
upsell_bot = UpsellBot()

async def handle_event(envelope: EventEnvelope) -> ResultSchema:
    """Handle upsell events"""
    try:
        return await upsell_bot.handle_upsell_check(envelope)
    except Exception as e:
        logger.error(f"Upsell bot error: {e}")
        return ResultSchema(
            ok=False,
            reason=f"Upsell analysis failed: {e}"
        )