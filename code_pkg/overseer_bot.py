#!/usr/bin/env python3
"""
OVERSEER BOT - Prevents system drift and maintains alignment at scale
Watches budgets, error rates, drift, and security anomalies
Never lets system spend beyond daily ceiling
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from commons.event_contracts import EventEnvelope, ResultSchema, TenantMetrics
from loguru import logger
import sqlite3
import json

class OverseerBot:
    def __init__(self):
        self.db_path = "overseer_monitoring.db"
        self.daily_budget_ceiling = 500.00  # Maximum daily spend per tenant
        self.error_rate_threshold = 0.05    # 5% error rate triggers pause
        self.drift_score_threshold = 0.3    # 30% drift triggers review
        self.init_database()
    
    def init_database(self):
        """Initialize oversight monitoring database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tenant_monitoring (
                tenant_id TEXT PRIMARY KEY,
                daily_budget_used REAL DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                last_activity TEXT,
                status TEXT DEFAULT 'active',
                drift_indicators TEXT DEFAULT '{}',
                alerts_sent INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anomaly_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT,
                anomaly_type TEXT,
                severity TEXT,
                description TEXT,
                metrics_snapshot TEXT,
                action_taken TEXT,
                created_at TEXT,
                resolved_at TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_name TEXT,
                metric_value REAL,
                threshold_value REAL,
                status TEXT,
                recorded_at TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    async def handle_oversight_check(self, envelope: EventEnvelope) -> ResultSchema:
        """Perform comprehensive oversight check"""
        tenant_id = envelope.tenant_id
        check_type = envelope.payload.get("check_type", "full")
        
        if check_type == "budget":
            return await self.check_budget_compliance(tenant_id)
        elif check_type == "drift":
            return await self.check_system_drift(tenant_id)
        elif check_type == "anomaly":
            return await self.detect_anomalies(tenant_id)
        else:
            return await self.full_oversight_check(tenant_id)
    
    async def full_oversight_check(self, tenant_id: str) -> ResultSchema:
        """Comprehensive oversight check for tenant"""
        results = {
            "tenant_id": tenant_id,
            "timestamp": datetime.now().isoformat(),
            "checks_performed": [],
            "alerts_generated": [],
            "actions_taken": []
        }
        
        # 1. Budget compliance check
        budget_result = await self.check_budget_compliance(tenant_id)
        results["checks_performed"].append("budget_compliance")
        if not budget_result.ok:
            results["alerts_generated"].append(budget_result.reason)
        
        # 2. Error rate monitoring
        error_result = await self.check_error_rates(tenant_id)
        results["checks_performed"].append("error_rate_monitoring")
        if not error_result.ok:
            results["alerts_generated"].append(error_result.reason)
        
        # 3. Drift detection
        drift_result = await self.check_system_drift(tenant_id)
        results["checks_performed"].append("drift_detection")
        if not drift_result.ok:
            results["alerts_generated"].append(drift_result.reason)
        
        # 4. Anomaly detection
        anomaly_result = await self.detect_anomalies(tenant_id)
        results["checks_performed"].append("anomaly_detection")
        if not anomaly_result.ok:
            results["alerts_generated"].append(anomaly_result.reason)
        
        # Determine overall system health
        overall_status = "healthy"
        if results["alerts_generated"]:
            overall_status = "monitoring" if len(results["alerts_generated"]) <= 2 else "critical"
        
        return ResultSchema(
            ok=overall_status != "critical",
            reason=f"Oversight check completed: {overall_status}",
            outputs=results
        )
    
    async def check_budget_compliance(self, tenant_id: str) -> ResultSchema:
        """Check if tenant is within daily budget ceiling"""
        metrics = await self.get_tenant_metrics(tenant_id)
        
        if metrics.budget_used > self.daily_budget_ceiling:
            # EMERGENCY: Pause all campaigns immediately
            await self.pause_all_tenant_activities(tenant_id)
            await self.log_alert(
                tenant_id,
                "budget_exceeded",
                "critical",
                f"Daily budget exceeded: ${metrics.budget_used:.2f} > ${self.daily_budget_ceiling:.2f}",
                metrics.model_dump(),
                "paused_all_activities"
            )
            
            return ResultSchema(
                ok=False,
                reason=f"CRITICAL: Budget ceiling exceeded - all activities paused",
                outputs={"budget_used": metrics.budget_used, "ceiling": self.daily_budget_ceiling}
            )
        
        # Warning at 80% of budget
        if metrics.budget_used > (self.daily_budget_ceiling * 0.8):
            await self.log_alert(
                tenant_id,
                "budget_warning",
                "warning", 
                f"Budget warning: ${metrics.budget_used:.2f} (80% of ${self.daily_budget_ceiling:.2f})",
                metrics.model_dump(),
                "monitoring"
            )
            
            return ResultSchema(
                ok=True,
                reason=f"Budget warning: ${metrics.budget_used:.2f} approaching ceiling",
                outputs={"budget_used": metrics.budget_used, "ceiling": self.daily_budget_ceiling}
            )
        
        return ResultSchema(
            ok=True,
            reason="Budget compliance OK",
            outputs={"budget_used": metrics.budget_used, "ceiling": self.daily_budget_ceiling}
        )
    
    async def check_error_rates(self, tenant_id: str) -> ResultSchema:
        """Monitor error rates and pause if threshold exceeded"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT error_count, total_requests FROM tenant_monitoring 
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result or result[1] == 0:
            return ResultSchema(ok=True, reason="Insufficient data for error rate analysis")
        
        error_count, total_requests = result
        error_rate = error_count / total_requests if total_requests > 0 else 0
        
        if error_rate > self.error_rate_threshold:
            await self.pause_all_tenant_activities(tenant_id)
            await self.log_alert(
                tenant_id,
                "high_error_rate",
                "critical",
                f"Error rate {error_rate:.2%} exceeds threshold {self.error_rate_threshold:.2%}",
                {"error_rate": error_rate, "error_count": error_count, "total_requests": total_requests},
                "paused_all_activities"
            )
            
            return ResultSchema(
                ok=False,
                reason=f"CRITICAL: High error rate {error_rate:.2%} - activities paused",
                outputs={"error_rate": error_rate, "threshold": self.error_rate_threshold}
            )
        
        return ResultSchema(
            ok=True,
            reason=f"Error rate OK: {error_rate:.2%}",
            outputs={"error_rate": error_rate, "threshold": self.error_rate_threshold}
        )
    
    async def check_system_drift(self, tenant_id: str) -> ResultSchema:
        """Detect system drift in outputs and behavior"""
        # Simplified drift detection - compare recent outputs against baseline
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT drift_indicators FROM tenant_monitoring 
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return ResultSchema(ok=True, reason="No drift data available")
        
        drift_data = json.loads(result[0]) if result[0] else {}
        current_drift_score = drift_data.get("drift_score", 0.0)
        
        if current_drift_score > self.drift_score_threshold:
            # Snapshot current state and alert
            snapshot_uri = await self.snapshot_tenant_state(tenant_id)
            await self.alert_court(f"System drift detected for {tenant_id}: {current_drift_score:.2%}")
            await self.log_alert(
                tenant_id,
                "system_drift",
                "high",
                f"Drift score {current_drift_score:.2%} exceeds threshold {self.drift_score_threshold:.2%}",
                drift_data,
                f"snapshot_created:{snapshot_uri}"
            )
            
            return ResultSchema(
                ok=False,
                reason=f"System drift detected: {current_drift_score:.2%}",
                outputs={"drift_score": current_drift_score, "snapshot": snapshot_uri}
            )
        
        return ResultSchema(
            ok=True,
            reason=f"Drift score OK: {current_drift_score:.2%}",
            outputs={"drift_score": current_drift_score}
        )
    
    async def detect_anomalies(self, tenant_id: str) -> ResultSchema:
        """Detect security and performance anomalies"""
        anomalies = []
        
        # Check for unusual spending patterns
        metrics = await self.get_tenant_metrics(tenant_id)
        if metrics.budget_used > 0:
            # Look for spending spikes (simplified)
            daily_average = 50.0  # Expected daily average
            if metrics.budget_used > (daily_average * 3):
                anomalies.append({
                    "type": "spending_spike",
                    "description": f"Spending ${metrics.budget_used:.2f} vs expected ${daily_average:.2f}",
                    "severity": "medium"
                })
        
        # Check campaign performance anomalies
        if len(metrics.campaign_performance) > 0:
            for campaign, performance in metrics.campaign_performance.items():
                if performance < 0.1:  # Very low performance
                    anomalies.append({
                        "type": "campaign_underperformance", 
                        "description": f"Campaign {campaign} performing at {performance:.2%}",
                        "severity": "low"
                    })
        
        if anomalies:
            for anomaly in anomalies:
                await self.log_alert(
                    tenant_id,
                    anomaly["type"],
                    anomaly["severity"],
                    anomaly["description"],
                    metrics.model_dump(),
                    "monitoring"
                )
            
            return ResultSchema(
                ok=True,  # Anomalies detected but not critical
                reason=f"Anomalies detected: {len(anomalies)} issues",
                outputs={"anomalies": anomalies}
            )
        
        return ResultSchema(
            ok=True,
            reason="No anomalies detected",
            outputs={"anomalies": []}
        )
    
    async def pause_all_tenant_activities(self, tenant_id: str):
        """Emergency pause all tenant campaigns and spending"""
        # Update tenant status to paused
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE tenant_monitoring 
            SET status = 'paused', updated_at = ?
            WHERE tenant_id = ?
        """, (datetime.now().isoformat(), tenant_id))
        
        conn.commit()
        conn.close()
        
        logger.critical(f"EMERGENCY: All activities paused for tenant {tenant_id}")
    
    async def snapshot_tenant_state(self, tenant_id: str) -> str:
        """Create snapshot of tenant state for analysis"""
        snapshot_id = f"snapshot_{tenant_id}_{int(time.time())}"
        snapshot_uri = f"s3://sincor-snapshots/{snapshot_id}.json"
        
        # Collect current state
        metrics = await self.get_tenant_metrics(tenant_id)
        snapshot_data = {
            "tenant_id": tenant_id,
            "snapshot_id": snapshot_id,
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics.model_dump(),
            "system_version": "1.0.0"
        }
        
        # In production, save to actual S3/MinIO
        # For now, log the snapshot
        logger.info(f"Snapshot created: {snapshot_uri}", extra=snapshot_data)
        
        return snapshot_uri
    
    async def alert_court(self, summary: str):
        """Alert the Court (human oversight) of critical issues"""
        alert_data = {
            "alert_type": "court_escalation",
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
            "severity": "requires_human_review"
        }
        
        logger.critical(f"COURT ALERT: {summary}", extra=alert_data)
        
        # In production, this would send to actual alerting system
        # (PagerDuty, Slack, email, etc.)
    
    async def get_tenant_metrics(self, tenant_id: str) -> TenantMetrics:
        """Get current tenant metrics for oversight"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT daily_budget_used, error_count, total_requests, last_activity, status
            FROM tenant_monitoring 
            WHERE tenant_id = ?
        """, (tenant_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            # Create default metrics
            return TenantMetrics(
                tenant_id=tenant_id,
                budget_used=0.0,
                error_rate=0.0,
                drift_score=0.0,
                campaign_performance={},
                last_activity=datetime.now()
            )
        
        budget_used, error_count, total_requests, last_activity, status = result
        error_rate = error_count / max(total_requests, 1)
        
        return TenantMetrics(
            tenant_id=tenant_id,
            budget_used=budget_used,
            error_rate=error_rate,
            drift_score=0.0,  # Simplified for now
            campaign_performance={"default": 0.15},  # Simplified
            last_activity=datetime.fromisoformat(last_activity) if last_activity else datetime.now()
        )
    
    async def log_alert(self, tenant_id: str, anomaly_type: str, severity: str, 
                       description: str, metrics_snapshot: Dict, action_taken: str):
        """Log oversight alerts for audit trail"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO anomaly_alerts 
            (tenant_id, anomaly_type, severity, description, metrics_snapshot, action_taken, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            tenant_id,
            anomaly_type,
            severity,
            description,
            json.dumps(metrics_snapshot),
            action_taken,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()

# Bot instance
overseer_bot = OverseerBot()

async def handle_event(envelope: EventEnvelope) -> ResultSchema:
    """Handle oversight events"""
    try:
        return await overseer_bot.handle_oversight_check(envelope)
    except Exception as e:
        logger.error(f"Overseer bot error: {e}")
        return ResultSchema(
            ok=False,
            reason=f"Oversight check failed: {e}"
        )