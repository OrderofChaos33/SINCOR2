#!/usr/bin/env python3
"""
SINCOR Advanced Real-Time Agent Health Monitoring System
Built on 2025 industry best practices for multi-agent system observability

Features:
- Real-time health metrics collection
- OpenTelemetry standard compliance
- AgentOps lifecycle management
- Multi-agent collaboration monitoring
- Predictive failure detection
- Resource optimization
"""

import asyncio
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict, deque
import psutil
import statistics
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from contextlib import contextmanager

# OpenTelemetry imports for standardized observability
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    print("OpenTelemetry not available - using basic telemetry")

class AgentStatus(Enum):
    """Agent health status levels"""
    HEALTHY = "healthy"
    WARNING = "warning"  
    CRITICAL = "critical"
    OFFLINE = "offline"
    RECOVERING = "recovering"
    DEGRADED = "degraded"

class MetricType(Enum):
    """Types of metrics we track"""
    PERFORMANCE = "performance"
    RESOURCE = "resource"
    COLLABORATION = "collaboration"
    LIFECYCLE = "lifecycle"
    BUSINESS = "business"

@dataclass
class AgentHealthMetrics:
    """Comprehensive agent health metrics based on 2025 standards"""
    agent_id: str
    timestamp: float
    
    # Core Performance Metrics (2025 Standards)
    task_completion_rate: float = 0.0  # Target: ≥90%
    response_time_ms: float = 0.0      # Target: <500ms
    error_rate: float = 0.0            # Target: <5%
    accuracy_score: float = 0.0        # Target: ≥95%
    
    # Resource Usage Metrics
    cpu_usage: float = 0.0             # Target: <80%
    memory_usage: float = 0.0          # Target: <90%
    api_success_rate: float = 0.0      # Target: ≥95%
    token_usage: int = 0
    
    # Multi-Agent Collaboration Metrics
    collaboration_success_rate: float = 0.0
    task_delegation_accuracy: float = 0.0
    workflow_efficiency: float = 0.0
    inter_agent_latency: float = 0.0
    
    # Lifecycle Metrics
    uptime_seconds: float = 0.0
    restart_count: int = 0
    version: str = "1.0.0"
    last_checkpoint: Optional[str] = None
    
    # Business Metrics
    revenue_generated: float = 0.0
    tasks_processed: int = 0
    customer_satisfaction: float = 0.0
    
    # Health Status
    status: AgentStatus = AgentStatus.HEALTHY
    alerts: List[str] = field(default_factory=list)

@dataclass
class SystemHealthSummary:
    """System-wide health summary for 42-agent swarm"""
    timestamp: float
    total_agents: int = 42
    healthy_agents: int = 0
    warning_agents: int = 0
    critical_agents: int = 0
    offline_agents: int = 0
    
    # Aggregate Metrics
    avg_response_time: float = 0.0
    total_tasks_completed: int = 0
    total_revenue: float = 0.0
    system_load: float = 0.0
    
    # Collaboration Health
    swarm_efficiency: float = 0.0
    coordination_success_rate: float = 0.0
    
    alerts: List[str] = field(default_factory=list)

class HealthMonitoringEngine:
    """Advanced real-time agent health monitoring engine"""
    
    def __init__(self, agents_config_path: str = None):
        self.agents: Dict[str, Dict] = {}
        self.metrics_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.current_metrics: Dict[str, AgentHealthMetrics] = {}
        self.alert_rules: List[Dict] = []
        self.running = False
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # Initialize monitoring database
        self.init_database()
        
        # Load agent configurations
        if agents_config_path:
            self.load_agent_configs(agents_config_path)
        else:
            self.generate_default_agents()
            
        # Initialize OpenTelemetry if available
        if OTEL_AVAILABLE:
            self.setup_opentelemetry()
            
        # Setup predictive analytics
        self.anomaly_detector = AnomalyDetector()
        
        # Configure logging
        self.setup_logging()
        
    def init_database(self):
        """Initialize SQLite database for metrics storage"""
        self.db_path = "agent_health_metrics.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    metrics_json TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    agent_id TEXT,
                    resolved BOOLEAN DEFAULT FALSE
                )
            """)
            
    def setup_logging(self):
        """Configure structured logging for monitoring"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('agent_health_monitor.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_opentelemetry(self):
        """Setup OpenTelemetry for standardized observability"""
        if not OTEL_AVAILABLE:
            return
            
        # Initialize tracer and meter providers
        trace.set_tracer_provider(TracerProvider())
        self.tracer = trace.get_tracer(__name__)
        
        # Setup metrics
        reader = PrometheusMetricReader()
        metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
        self.meter = metrics.get_meter(__name__)
        
        # Create custom metrics
        self.agent_response_time = self.meter.create_histogram(
            "agent_response_time_ms",
            description="Agent response time in milliseconds"
        )
        
        self.agent_error_rate = self.meter.create_counter(
            "agent_errors_total",
            description="Total number of agent errors"
        )
        
    def generate_default_agents(self):
        """Generate configuration for 42 SINCOR agents"""
        agent_names = [
            "E-auriga-01", "E-vega-02", "E-rigel-03", "E-altair-04", "E-spica-05",
            "E-deneb-06", "E-capella-07", "E-sirius-08", "E-polaris-09", "E-arcturus-10",
            "E-betelgeuse-11", "E-aldebaran-12", "E-antares-13", "E-procyon-14", "E-canopus-15",
            "E-achernar-16", "E-bellatrix-17", "E-castor-18", "E-pollux-19", "E-regulus-20",
            "E-mizar-21", "E-fomalhaut-22", "E-acrux-23", "E-mimosa-24", "E-gacrux-25",
            "E-shaula-26", "E-kaus-27", "E-alkaid-28", "E-dubhe-29", "E-merak-30",
            "E-phecda-31", "E-megrez-32", "E-alioth-33", "E-meback-34", "E-benetnash-35",
            "E-cor-caroli-36", "E-alphard-37", "E-alpheratz-38", "E-mirach-39", "E-almaak-40",
            "E-hamal-41", "E-sheratan-42", "E-mesarthim-43"
        ]
        
        archetypes = ["Scout", "Builder", "Director", "Negotiator", "Auditor", "Caretaker", "Synthesizer"]
        
        for i, agent_name in enumerate(agent_names):
            self.agents[agent_name] = {
                "id": agent_name,
                "archetype": archetypes[i % len(archetypes)],
                "start_time": time.time(),
                "expected_tasks": ["analysis", "generation", "coordination"],
                "resource_limits": {
                    "max_cpu": 80.0,
                    "max_memory": 90.0,
                    "max_response_time": 500.0
                }
            }
            
    async def collect_agent_metrics(self, agent_id: str) -> AgentHealthMetrics:
        """Collect comprehensive health metrics for a specific agent"""
        current_time = time.time()
        
        # Simulate realistic agent metrics (in production, these would come from actual agents)
        base_performance = 85 + (hash(agent_id + str(int(current_time))) % 15)
        
        metrics = AgentHealthMetrics(
            agent_id=agent_id,
            timestamp=current_time,
            
            # Performance metrics with realistic variation
            task_completion_rate=min(100.0, base_performance + (hash(agent_id) % 10)),
            response_time_ms=200 + (hash(agent_id + "response") % 300),
            error_rate=max(0.0, 5.0 - (hash(agent_id + "errors") % 6)),
            accuracy_score=min(100.0, 92 + (hash(agent_id + "accuracy") % 8)),
            
            # Resource usage
            cpu_usage=30 + (hash(agent_id + "cpu") % 50),
            memory_usage=40 + (hash(agent_id + "memory") % 40),
            api_success_rate=min(100.0, 94 + (hash(agent_id + "api") % 6)),
            token_usage=1000 + (hash(agent_id + "tokens") % 2000),
            
            # Collaboration metrics
            collaboration_success_rate=min(100.0, 88 + (hash(agent_id + "collab") % 12)),
            task_delegation_accuracy=min(100.0, 90 + (hash(agent_id + "delegate") % 10)),
            workflow_efficiency=min(100.0, 85 + (hash(agent_id + "workflow") % 15)),
            inter_agent_latency=10 + (hash(agent_id + "latency") % 90),
            
            # Lifecycle metrics
            uptime_seconds=current_time - self.agents.get(agent_id, {}).get("start_time", current_time),
            restart_count=hash(agent_id + "restarts") % 3,
            version="1.0.0",
            
            # Business metrics
            revenue_generated=100 + (hash(agent_id + "revenue") % 1000),
            tasks_processed=50 + (hash(agent_id + "tasks") % 200),
            customer_satisfaction=min(5.0, 3.5 + (hash(agent_id + "satisfaction") % 15) / 10),
        )
        
        # Determine health status based on metrics
        metrics.status = self.determine_agent_status(metrics)
        
        # Check for alerts
        metrics.alerts = self.check_alert_conditions(metrics)
        
        return metrics
        
    def determine_agent_status(self, metrics: AgentHealthMetrics) -> AgentStatus:
        """Determine agent status based on health metrics"""
        critical_conditions = [
            metrics.error_rate > 10.0,
            metrics.response_time_ms > 1000,
            metrics.cpu_usage > 95.0,
            metrics.memory_usage > 95.0,
            metrics.task_completion_rate < 70.0
        ]
        
        warning_conditions = [
            metrics.error_rate > 5.0,
            metrics.response_time_ms > 500,
            metrics.cpu_usage > 80.0,
            metrics.memory_usage > 90.0,
            metrics.task_completion_rate < 90.0,
            metrics.accuracy_score < 95.0
        ]
        
        if any(critical_conditions):
            return AgentStatus.CRITICAL
        elif any(warning_conditions):
            return AgentStatus.WARNING
        else:
            return AgentStatus.HEALTHY
            
    def check_alert_conditions(self, metrics: AgentHealthMetrics) -> List[str]:
        """Check for alert conditions and generate alerts"""
        alerts = []
        
        if metrics.response_time_ms > 500:
            alerts.append(f"Response time {metrics.response_time_ms:.1f}ms exceeds 500ms threshold")
            
        if metrics.error_rate > 5.0:
            alerts.append(f"Error rate {metrics.error_rate:.1f}% exceeds 5% threshold")
            
        if metrics.cpu_usage > 80.0:
            alerts.append(f"CPU usage {metrics.cpu_usage:.1f}% exceeds 80% threshold")
            
        if metrics.memory_usage > 90.0:
            alerts.append(f"Memory usage {metrics.memory_usage:.1f}% exceeds 90% threshold")
            
        if metrics.task_completion_rate < 90.0:
            alerts.append(f"Task completion rate {metrics.task_completion_rate:.1f}% below 90% threshold")
            
        if metrics.collaboration_success_rate < 85.0:
            alerts.append(f"Collaboration success rate {metrics.collaboration_success_rate:.1f}% below 85% threshold")
            
        return alerts
        
    async def monitor_all_agents(self):
        """Monitor all 42 agents concurrently"""
        tasks = []
        for agent_id in self.agents.keys():
            task = asyncio.create_task(self.collect_agent_metrics(agent_id))
            tasks.append(task)
            
        metrics_results = await asyncio.gather(*tasks)
        
        # Update current metrics
        for metrics in metrics_results:
            self.current_metrics[metrics.agent_id] = metrics
            self.metrics_history[metrics.agent_id].append(metrics)
            
        # Store metrics in database
        self.store_metrics_batch(metrics_results)
        
        return metrics_results
        
    def store_metrics_batch(self, metrics_batch: List[AgentHealthMetrics]):
        """Store metrics batch in database efficiently"""
        with sqlite3.connect(self.db_path) as conn:
            for metrics in metrics_batch:
                conn.execute(
                    "INSERT INTO agent_metrics (agent_id, timestamp, metrics_json, status) VALUES (?, ?, ?, ?)",
                    (metrics.agent_id, metrics.timestamp, json.dumps(asdict(metrics)), metrics.status.value)
                )
                
                # Store alerts
                for alert in metrics.alerts:
                    conn.execute(
                        "INSERT INTO system_alerts (timestamp, severity, message, agent_id) VALUES (?, ?, ?, ?)",
                        (metrics.timestamp, metrics.status.value, alert, metrics.agent_id)
                    )
                    
    def generate_system_summary(self) -> SystemHealthSummary:
        """Generate system-wide health summary"""
        if not self.current_metrics:
            return SystemHealthSummary(timestamp=time.time())
            
        current_time = time.time()
        status_counts = defaultdict(int)
        total_response_time = 0
        total_tasks = 0
        total_revenue = 0
        
        for metrics in self.current_metrics.values():
            status_counts[metrics.status] += 1
            total_response_time += metrics.response_time_ms
            total_tasks += metrics.tasks_processed
            total_revenue += metrics.revenue_generated
            
        num_agents = len(self.current_metrics)
        
        # Calculate system-wide metrics
        summary = SystemHealthSummary(
            timestamp=current_time,
            total_agents=num_agents,
            healthy_agents=status_counts[AgentStatus.HEALTHY],
            warning_agents=status_counts[AgentStatus.WARNING],
            critical_agents=status_counts[AgentStatus.CRITICAL],
            offline_agents=status_counts[AgentStatus.OFFLINE],
            
            avg_response_time=total_response_time / num_agents if num_agents > 0 else 0,
            total_tasks_completed=total_tasks,
            total_revenue=total_revenue,
            system_load=psutil.cpu_percent(),
            
            swarm_efficiency=self.calculate_swarm_efficiency(),
            coordination_success_rate=self.calculate_coordination_success()
        )
        
        # Generate system-level alerts
        summary.alerts = self.generate_system_alerts(summary)
        
        return summary
        
    def calculate_swarm_efficiency(self) -> float:
        """Calculate overall swarm efficiency"""
        if not self.current_metrics:
            return 0.0
            
        efficiency_scores = []
        for metrics in self.current_metrics.values():
            # Combine multiple efficiency factors
            efficiency = (
                metrics.task_completion_rate * 0.3 +
                (100 - metrics.error_rate) * 0.2 +
                metrics.workflow_efficiency * 0.3 +
                metrics.collaboration_success_rate * 0.2
            )
            efficiency_scores.append(efficiency)
            
        return statistics.mean(efficiency_scores)
        
    def calculate_coordination_success(self) -> float:
        """Calculate inter-agent coordination success rate"""
        if not self.current_metrics:
            return 0.0
            
        coordination_scores = [m.collaboration_success_rate for m in self.current_metrics.values()]
        return statistics.mean(coordination_scores)
        
    def generate_system_alerts(self, summary: SystemHealthSummary) -> List[str]:
        """Generate system-level alerts"""
        alerts = []
        
        if summary.critical_agents > 0:
            alerts.append(f"CRITICAL: {summary.critical_agents} agents in critical state")
            
        if summary.offline_agents > 3:
            alerts.append(f"WARNING: {summary.offline_agents} agents offline")
            
        if summary.avg_response_time > 750:
            alerts.append(f"WARNING: Average response time {summary.avg_response_time:.1f}ms is high")
            
        if summary.swarm_efficiency < 80:
            alerts.append(f"WARNING: Swarm efficiency {summary.swarm_efficiency:.1f}% below optimal")
            
        if summary.system_load > 85:
            alerts.append(f"WARNING: System CPU load {summary.system_load:.1f}% is high")
            
        return alerts
        
    async def start_monitoring(self, interval_seconds: int = 5):
        """Start continuous monitoring of all agents"""
        self.running = True
        self.logger.info(f"Starting health monitoring for {len(self.agents)} agents")
        
        try:
            while self.running:
                start_time = time.time()
                
                # Monitor all agents
                await self.monitor_all_agents()
                
                # Generate system summary
                system_summary = self.generate_system_summary()
                
                # Log system status
                self.logger.info(
                    f"System Health: {system_summary.healthy_agents}H "
                    f"{system_summary.warning_agents}W {system_summary.critical_agents}C "
                    f"| Avg Response: {system_summary.avg_response_time:.1f}ms "
                    f"| Efficiency: {system_summary.swarm_efficiency:.1f}%"
                )
                
                # Log alerts
                for alert in system_summary.alerts:
                    self.logger.warning(f"ALERT: {alert}")
                    
                # Calculate sleep time to maintain interval
                elapsed = time.time() - start_time
                sleep_time = max(0, interval_seconds - elapsed)
                await asyncio.sleep(sleep_time)
                
        except KeyboardInterrupt:
            self.logger.info("Monitoring stopped by user")
        except Exception as e:
            self.logger.error(f"Monitoring error: {e}")
        finally:
            self.running = False
            
    def stop_monitoring(self):
        """Stop the monitoring system"""
        self.running = False
        
    def get_agent_health_report(self, agent_id: str, hours: int = 24) -> Dict:
        """Get comprehensive health report for specific agent"""
        cutoff_time = time.time() - (hours * 3600)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT timestamp, metrics_json FROM agent_metrics WHERE agent_id = ? AND timestamp > ? ORDER BY timestamp",
                (agent_id, cutoff_time)
            )
            
            historical_data = []
            for row in cursor:
                metrics_data = json.loads(row[1])
                historical_data.append(metrics_data)
                
        current_metrics = self.current_metrics.get(agent_id)
        
        return {
            "agent_id": agent_id,
            "current_status": current_metrics.status.value if current_metrics else "unknown",
            "current_metrics": asdict(current_metrics) if current_metrics else None,
            "historical_data": historical_data,
            "trends": self.analyze_agent_trends(historical_data),
            "recommendations": self.generate_recommendations(agent_id, historical_data)
        }
        
    def analyze_agent_trends(self, historical_data: List[Dict]) -> Dict:
        """Analyze trends in agent performance"""
        if len(historical_data) < 2:
            return {"trend": "insufficient_data"}
            
        # Calculate trends for key metrics
        response_times = [d["response_time_ms"] for d in historical_data]
        error_rates = [d["error_rate"] for d in historical_data]
        cpu_usage = [d["cpu_usage"] for d in historical_data]
        
        return {
            "response_time_trend": self.calculate_trend(response_times),
            "error_rate_trend": self.calculate_trend(error_rates),
            "cpu_usage_trend": self.calculate_trend(cpu_usage),
            "overall_health_trend": self.calculate_overall_trend(historical_data)
        }
        
    def calculate_trend(self, values: List[float]) -> str:
        """Calculate trend direction for a metric"""
        if len(values) < 2:
            return "stable"
            
        recent_avg = statistics.mean(values[-5:])  # Last 5 values
        older_avg = statistics.mean(values[:5])    # First 5 values
        
        change_percent = ((recent_avg - older_avg) / older_avg) * 100
        
        if change_percent > 10:
            return "increasing"
        elif change_percent < -10:
            return "decreasing"
        else:
            return "stable"
            
    def calculate_overall_trend(self, historical_data: List[Dict]) -> str:
        """Calculate overall health trend"""
        if len(historical_data) < 2:
            return "stable"
            
        # Create composite health score
        health_scores = []
        for data in historical_data:
            score = (
                data["task_completion_rate"] * 0.25 +
                (100 - data["error_rate"]) * 0.25 +
                data["accuracy_score"] * 0.25 +
                (100 - data["cpu_usage"]) * 0.25
            )
            health_scores.append(score)
            
        return self.calculate_trend(health_scores)
        
    def generate_recommendations(self, agent_id: str, historical_data: List[Dict]) -> List[str]:
        """Generate optimization recommendations for agent"""
        recommendations = []
        
        if not historical_data:
            return ["Insufficient data for recommendations"]
            
        current_metrics = self.current_metrics.get(agent_id)
        if not current_metrics:
            return ["Agent not currently active"]
            
        # Performance recommendations
        if current_metrics.response_time_ms > 500:
            recommendations.append("Consider optimizing response time - current average exceeds 500ms threshold")
            
        if current_metrics.error_rate > 5.0:
            recommendations.append("Review error handling - error rate exceeds 5% threshold")
            
        if current_metrics.cpu_usage > 80.0:
            recommendations.append("Monitor CPU usage - consider resource optimization or scaling")
            
        if current_metrics.collaboration_success_rate < 85.0:
            recommendations.append("Improve inter-agent collaboration protocols")
            
        if current_metrics.task_completion_rate < 90.0:
            recommendations.append("Analyze task failure patterns and optimize processing logic")
            
        # Trend-based recommendations
        trends = self.analyze_agent_trends(historical_data)
        if trends.get("response_time_trend") == "increasing":
            recommendations.append("Response time trending upward - investigate performance degradation")
            
        if trends.get("error_rate_trend") == "increasing":
            recommendations.append("Error rate increasing - review recent changes and error logs")
            
        return recommendations if recommendations else ["Agent performing within optimal parameters"]


class AnomalyDetector:
    """Predictive anomaly detection for agent health"""
    
    def __init__(self):
        self.baseline_metrics = {}
        self.anomaly_thresholds = {
            "response_time_ms": 2.0,  # Standard deviations
            "error_rate": 2.5,
            "cpu_usage": 2.0,
            "memory_usage": 2.0
        }
        
    def update_baselines(self, metrics_history: Dict[str, deque]):
        """Update baseline metrics for anomaly detection"""
        for agent_id, history in metrics_history.items():
            if len(history) < 10:  # Need minimum data for baseline
                continue
                
            recent_metrics = list(history)[-50:]  # Last 50 data points
            
            self.baseline_metrics[agent_id] = {
                "response_time_ms": {
                    "mean": statistics.mean([m.response_time_ms for m in recent_metrics]),
                    "stdev": statistics.stdev([m.response_time_ms for m in recent_metrics])
                },
                "error_rate": {
                    "mean": statistics.mean([m.error_rate for m in recent_metrics]),
                    "stdev": statistics.stdev([m.error_rate for m in recent_metrics])
                },
                "cpu_usage": {
                    "mean": statistics.mean([m.cpu_usage for m in recent_metrics]),
                    "stdev": statistics.stdev([m.cpu_usage for m in recent_metrics])
                }
            }
            
    def detect_anomalies(self, current_metrics: AgentHealthMetrics) -> List[str]:
        """Detect anomalies in current metrics compared to baseline"""
        anomalies = []
        agent_id = current_metrics.agent_id
        
        if agent_id not in self.baseline_metrics:
            return []  # No baseline yet
            
        baseline = self.baseline_metrics[agent_id]
        
        for metric_name, threshold in self.anomaly_thresholds.items():
            if metric_name not in baseline:
                continue
                
            current_value = getattr(current_metrics, metric_name, 0)
            mean = baseline[metric_name]["mean"]
            stdev = baseline[metric_name]["stdev"]
            
            if stdev > 0:  # Avoid division by zero
                z_score = abs((current_value - mean) / stdev)
                if z_score > threshold:
                    anomalies.append(
                        f"Anomaly detected in {metric_name}: {current_value:.2f} "
                        f"(baseline: {mean:.2f}±{stdev:.2f}, z-score: {z_score:.2f})"
                    )
                    
        return anomalies


# CLI and API interfaces
class HealthMonitorAPI:
    """REST API interface for health monitoring system"""
    
    def __init__(self, monitor: HealthMonitoringEngine):
        self.monitor = monitor
        
    def get_system_status(self) -> Dict:
        """Get current system status"""
        summary = self.monitor.generate_system_summary()
        return asdict(summary)
        
    def get_agent_status(self, agent_id: str) -> Dict:
        """Get status for specific agent"""
        if agent_id not in self.monitor.current_metrics:
            return {"error": "Agent not found"}
            
        metrics = self.monitor.current_metrics[agent_id]
        return asdict(metrics)
        
    def get_health_report(self, agent_id: str, hours: int = 24) -> Dict:
        """Get comprehensive health report"""
        return self.monitor.get_agent_health_report(agent_id, hours)


async def main():
    """Main function to run the health monitoring system"""
    print("🚀 Starting SINCOR Advanced Agent Health Monitoring System")
    print("Built with 2025 industry best practices\n")
    
    # Initialize monitoring system
    monitor = HealthMonitoringEngine()
    
    # Start monitoring
    try:
        await monitor.start_monitoring(interval_seconds=3)
    except KeyboardInterrupt:
        print("\n👋 Monitoring system stopped")
    except Exception as e:
        print(f"❌ Error: {e}")
        
if __name__ == "__main__":
    asyncio.run(main())