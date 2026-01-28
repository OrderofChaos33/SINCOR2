#!/usr/bin/env python3
"""
Nightly ETL with reconciliation for SINCOR
Pulls data from CAD, SINCOR kernel, consolidates metrics, detects anomalies
"""

import asyncio
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
from loguru import logger
import schedule
import time

class NightlyETL:
    def __init__(self):
        self.etl_db = "etl_reconciliation.db"
        self.init_database()
        
    def init_database(self):
        """Initialize ETL tracking database"""
        conn = sqlite3.connect(self.etl_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS etl_runs (
                run_id TEXT PRIMARY KEY,
                run_date TEXT,
                start_time TEXT,
                end_time TEXT,
                status TEXT,
                records_processed INTEGER,
                anomalies_detected INTEGER,
                errors TEXT,
                metrics_snapshot TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reconciled_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                metric_type TEXT,
                source TEXT,
                metric_name TEXT,
                metric_value REAL,
                expected_value REAL,
                variance REAL,
                reconciliation_status TEXT,
                recorded_at TEXT,
                FOREIGN KEY (run_id) REFERENCES etl_runs (run_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anomaly_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                alert_type TEXT,
                severity TEXT,
                description TEXT,
                affected_metrics TEXT,
                threshold_breached REAL,
                actual_value REAL,
                created_at TEXT,
                FOREIGN KEY (run_id) REFERENCES etl_runs (run_id)
            )
        """)
        
        conn.commit()
        conn.close()
        
    async def run_nightly_etl(self):
        """Execute complete nightly ETL process"""
        run_id = f"etl_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        start_time = datetime.now()
        
        logger.info(f"Starting nightly ETL run: {run_id}")
        
        try:
            # Initialize ETL run
            await self.log_etl_start(run_id, start_time)
            
            # Step 1: Extract data from all sources
            cad_data = await self.extract_cad_metrics()
            sincor_data = await self.extract_sincor_metrics()
            bot_data = await self.extract_bot_metrics()
            
            # Step 2: Transform and consolidate
            consolidated = await self.transform_and_consolidate(cad_data, sincor_data, bot_data)
            
            # Step 3: Reconcile against expected values
            reconciliation_results = await self.reconcile_metrics(run_id, consolidated)
            
            # Step 4: Detect anomalies
            anomalies = await self.detect_anomalies(run_id, reconciliation_results)
            
            # Step 5: Generate alerts for critical issues
            alerts_sent = await self.process_anomaly_alerts(run_id, anomalies)
            
            # Complete ETL run
            end_time = datetime.now()
            await self.log_etl_completion(run_id, end_time, len(consolidated), len(anomalies), "success")
            
            logger.success(f"Nightly ETL completed: {len(consolidated)} metrics processed, {len(anomalies)} anomalies detected")
            
        except Exception as e:
            end_time = datetime.now()
            await self.log_etl_completion(run_id, end_time, 0, 0, "failed", str(e))
            logger.error(f"Nightly ETL failed: {e}")
            
    async def extract_cad_metrics(self) -> Dict:
        """Extract metrics from CAD Flask system"""
        try:
            # Connect to CAD database
            cad_db = "../clinton_auto_detailing/cad.db"
            conn = sqlite3.connect(cad_db)
            cursor = conn.cursor()
            
            # Get today's leads
            today = datetime.now().date().isoformat()
            cursor.execute("SELECT COUNT(*) FROM leads WHERE DATE(created_at) = ?", (today,))
            daily_leads = cursor.fetchone()[0]
            
            # Get weekly metrics
            week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
            cursor.execute("SELECT COUNT(*) FROM leads WHERE DATE(created_at) >= ?", (week_ago,))
            weekly_leads = cursor.fetchone()[0]
            
            # Get conversion estimates (placeholder - would track actual bookings)
            estimated_revenue = weekly_leads * 50  # $50 avg per lead
            
            conn.close()
            
            return {
                "source": "cad_flask",
                "metrics": {
                    "daily_leads": daily_leads,
                    "weekly_leads": weekly_leads,
                    "estimated_revenue": estimated_revenue,
                    "conversion_rate": 0.4,  # Placeholder
                    "last_updated": datetime.now().isoformat()
                }
            }
            
        except Exception as e:
            logger.error(f"CAD metrics extraction failed: {e}")
            return {"source": "cad_flask", "metrics": {}, "error": str(e)}
    
    async def extract_sincor_metrics(self) -> Dict:
        """Extract metrics from SINCOR kernel bots"""
        try:
            metrics = {}
            
            # Extract from demo bot
            demo_db = "bots/demo_responses.db"
            try:
                conn = sqlite3.connect(demo_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM demo_responses WHERE DATE(created_at) = ?", 
                             (datetime.now().date().isoformat(),))
                daily_demos = cursor.fetchone()[0]
                metrics["daily_demos"] = daily_demos
                conn.close()
            except:
                metrics["daily_demos"] = 0
                
            # Extract from license bot
            license_db = "bots/license_transactions.db"
            try:
                conn = sqlite3.connect(license_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM license_transactions WHERE payment_status = 'paid'")
                total_licenses = cursor.fetchone()[0]
                metrics["total_licenses"] = total_licenses
                conn.close()
            except:
                metrics["total_licenses"] = 0
                
            # Extract from upsell bot
            upsell_db = "bots/upsell_system.db"
            try:
                conn = sqlite3.connect(upsell_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM upsell_events WHERE DATE(created_at) = ?",
                             (datetime.now().date().isoformat(),))
                daily_upsells = cursor.fetchone()[0]
                metrics["daily_upsells"] = daily_upsells
                conn.close()
            except:
                metrics["daily_upsells"] = 0
                
            return {
                "source": "sincor_kernel",
                "metrics": metrics
            }
            
        except Exception as e:
            logger.error(f"SINCOR metrics extraction failed: {e}")
            return {"source": "sincor_kernel", "metrics": {}, "error": str(e)}
    
    async def extract_bot_metrics(self) -> Dict:
        """Extract performance metrics from individual bots"""
        try:
            bot_metrics = {}
            
            # Check overseer bot health
            overseer_db = "bots/overseer_monitoring.db"
            try:
                conn = sqlite3.connect(overseer_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM anomaly_alerts WHERE DATE(created_at) = ?",
                             (datetime.now().date().isoformat(),))
                daily_anomalies = cursor.fetchone()[0]
                bot_metrics["daily_anomalies"] = daily_anomalies
                conn.close()
            except:
                bot_metrics["daily_anomalies"] = 0
                
            # Check support bot resolution rate
            support_db = "bots/support_tickets.db"
            try:
                conn = sqlite3.connect(support_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM support_tickets WHERE resolution_status = 'resolved'")
                resolved_tickets = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM support_tickets")
                total_tickets = cursor.fetchone()[0]
                bot_metrics["support_resolution_rate"] = resolved_tickets / max(total_tickets, 1)
                conn.close()
            except:
                bot_metrics["support_resolution_rate"] = 1.0
                
            return {
                "source": "bot_performance",
                "metrics": bot_metrics
            }
            
        except Exception as e:
            logger.error(f"Bot metrics extraction failed: {e}")
            return {"source": "bot_performance", "metrics": {}, "error": str(e)}
    
    async def transform_and_consolidate(self, cad_data: Dict, sincor_data: Dict, bot_data: Dict) -> List[Dict]:
        """Transform and consolidate metrics from all sources"""
        consolidated = []
        
        # CAD metrics
        for metric_name, value in cad_data.get("metrics", {}).items():
            consolidated.append({
                "source": "cad_flask",
                "metric_name": metric_name,
                "metric_value": float(value) if isinstance(value, (int, float)) else 0.0,
                "metric_type": "business_kpi"
            })
            
        # SINCOR metrics
        for metric_name, value in sincor_data.get("metrics", {}).items():
            consolidated.append({
                "source": "sincor_kernel", 
                "metric_name": metric_name,
                "metric_value": float(value) if isinstance(value, (int, float)) else 0.0,
                "metric_type": "system_kpi"
            })
            
        # Bot metrics
        for metric_name, value in bot_data.get("metrics", {}).items():
            consolidated.append({
                "source": "bot_performance",
                "metric_name": metric_name,
                "metric_value": float(value) if isinstance(value, (int, float)) else 0.0,
                "metric_type": "operational_kpi"
            })
            
        return consolidated
    
    async def reconcile_metrics(self, run_id: str, consolidated: List[Dict]) -> List[Dict]:
        """Reconcile actual metrics against expected values"""
        expectations = {
            "daily_leads": {"min": 0.5, "max": 3.0, "target": 0.7},  # CAD target: 5 leads/7 days
            "weekly_leads": {"min": 3, "max": 15, "target": 5},
            "estimated_revenue": {"min": 150, "max": 500, "target": 250},
            "daily_demos": {"min": 0, "max": 10, "target": 2},
            "daily_upsells": {"min": 0, "max": 5, "target": 1},
            "support_resolution_rate": {"min": 0.8, "max": 1.0, "target": 0.95}
        }
        
        reconciliation_results = []
        
        for metric in consolidated:
            metric_name = metric["metric_name"]
            actual_value = metric["metric_value"]
            
            if metric_name in expectations:
                expected = expectations[metric_name]["target"]
                variance = abs(actual_value - expected) / max(expected, 0.001)
                
                # Determine reconciliation status
                min_val = expectations[metric_name]["min"]
                max_val = expectations[metric_name]["max"]
                
                if min_val <= actual_value <= max_val:
                    status = "within_range"
                elif actual_value < min_val:
                    status = "below_range"
                else:
                    status = "above_range"
                    
                reconciliation_results.append({
                    **metric,
                    "expected_value": expected,
                    "variance": variance,
                    "reconciliation_status": status
                })
                
                # Log to database
                await self.log_reconciled_metric(run_id, metric, expected, variance, status)
            else:
                reconciliation_results.append({
                    **metric,
                    "expected_value": None,
                    "variance": None,
                    "reconciliation_status": "no_expectation"
                })
                
        return reconciliation_results
    
    async def detect_anomalies(self, run_id: str, reconciled_metrics: List[Dict]) -> List[Dict]:
        """Detect anomalies in reconciled metrics"""
        anomalies = []
        
        for metric in reconciled_metrics:
            if metric["reconciliation_status"] == "below_range":
                severity = "high" if metric["variance"] > 0.5 else "medium"
                anomalies.append({
                    "type": "underperformance",
                    "severity": severity,
                    "metric": metric["metric_name"],
                    "actual": metric["metric_value"],
                    "expected": metric["expected_value"],
                    "variance": metric["variance"]
                })
                
            elif metric["reconciliation_status"] == "above_range":
                # Above range might be good (leads) or bad (errors)
                if "error" in metric["metric_name"] or "anomal" in metric["metric_name"]:
                    severity = "high"
                    anomalies.append({
                        "type": "error_spike",
                        "severity": severity,
                        "metric": metric["metric_name"],
                        "actual": metric["metric_value"],
                        "expected": metric["expected_value"],
                        "variance": metric["variance"]
                    })
                    
        # Check for critical business metrics
        lead_metrics = [m for m in reconciled_metrics if "leads" in m["metric_name"]]
        if lead_metrics:
            avg_leads = sum(m["metric_value"] for m in lead_metrics) / len(lead_metrics)
            if avg_leads < 0.3:  # Critical threshold
                anomalies.append({
                    "type": "critical_underperformance",
                    "severity": "critical",
                    "metric": "average_leads",
                    "actual": avg_leads,
                    "expected": 0.7,
                    "variance": (0.7 - avg_leads) / 0.7
                })
                
        return anomalies
    
    async def process_anomaly_alerts(self, run_id: str, anomalies: List[Dict]) -> int:
        """Process and send alerts for detected anomalies"""
        alerts_sent = 0
        
        for anomaly in anomalies:
            # Log anomaly
            await self.log_anomaly_alert(run_id, anomaly)
            
            # Send critical alerts immediately
            if anomaly["severity"] == "critical":
                await self.send_critical_alert(anomaly)
                alerts_sent += 1
                
            # Log high severity for review
            elif anomaly["severity"] == "high":
                logger.warning(f"High severity anomaly: {anomaly['metric']} - {anomaly['type']}")
                alerts_sent += 1
                
        return alerts_sent
    
    async def send_critical_alert(self, anomaly: Dict):
        """Send critical alert (placeholder - would integrate with actual alerting)"""
        alert_message = f"CRITICAL ANOMALY DETECTED: {anomaly['metric']} - {anomaly['type']}"
        logger.critical(alert_message)
        # In production: send to Slack, email, PagerDuty, etc.
        
    async def log_etl_start(self, run_id: str, start_time: datetime):
        """Log ETL run start"""
        conn = sqlite3.connect(self.etl_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO etl_runs (run_id, run_date, start_time, status)
            VALUES (?, ?, ?, ?)
        """, (run_id, datetime.now().date().isoformat(), start_time.isoformat(), "running"))
        
        conn.commit()
        conn.close()
        
    async def log_etl_completion(self, run_id: str, end_time: datetime, records: int, 
                                anomalies: int, status: str, errors: str = None):
        """Log ETL run completion"""
        conn = sqlite3.connect(self.etl_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE etl_runs 
            SET end_time = ?, status = ?, records_processed = ?, anomalies_detected = ?, errors = ?
            WHERE run_id = ?
        """, (end_time.isoformat(), status, records, anomalies, errors, run_id))
        
        conn.commit()
        conn.close()
        
    async def log_reconciled_metric(self, run_id: str, metric: Dict, expected: float, 
                                  variance: float, status: str):
        """Log individual metric reconciliation"""
        conn = sqlite3.connect(self.etl_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reconciled_metrics 
            (run_id, metric_type, source, metric_name, metric_value, expected_value, variance, reconciliation_status, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, metric["metric_type"], metric["source"], metric["metric_name"],
              metric["metric_value"], expected, variance, status, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        
    async def log_anomaly_alert(self, run_id: str, anomaly: Dict):
        """Log anomaly alert"""
        conn = sqlite3.connect(self.etl_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO anomaly_alerts 
            (run_id, alert_type, severity, description, affected_metrics, threshold_breached, actual_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, anomaly["type"], anomaly["severity"], 
              f"{anomaly['metric']} anomaly detected", anomaly["metric"],
              anomaly.get("expected", 0), anomaly["actual"], datetime.now().isoformat()))
        
        conn.commit()
        conn.close()

# ETL instance
etl_processor = NightlyETL()

def run_scheduler():
    """Run the ETL scheduler"""
    # Schedule nightly ETL at 2 AM
    schedule.every().day.at("02:00").do(lambda: asyncio.run(etl_processor.run_nightly_etl()))
    
    logger.info("ETL scheduler started - running nightly at 2 AM")
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

async def run_etl_now():
    """Run ETL immediately for testing"""
    await etl_processor.run_nightly_etl()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "now":
        asyncio.run(run_etl_now())
    else:
        run_scheduler()