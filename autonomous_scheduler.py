"""
SINCOR Autonomous Scheduler
Runs agent orchestration continuously in the background
Generates outputs on schedule without manual intervention
"""

import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any
import threading
import signal
import sys

from agent_orchestrator import AgentOrchestrator
from monetization_engine import MonetizationEngine
import asyncio


class AutonomousScheduler:
    """
    Autonomous scheduler for continuous agent operation

    Features:
    - Continuous task generation
    - Scheduled workflow execution
    - Performance optimization
    - Auto-scaling based on load
    - Self-monitoring and recovery
    """

    def __init__(self, interval_seconds: int = 300):
        self.interval_seconds = interval_seconds
        self.orchestrator = AgentOrchestrator()
        self.monetization_engine = MonetizationEngine()
        self.running = False
        self.thread = None

        # Statistics tracking
        self.stats = {
            'cycles_completed': 0,
            'total_tasks_generated': 0,
            'total_outputs_created': 0,
            'total_revenue_generated': 0.0,
            'total_deals_closed': 0,
            'start_time': None,
            'last_cycle_time': None,
            'errors': 0
        }

        # Task templates for autonomous generation
        self.task_templates = self._load_task_templates()

        print(f"[SCHEDULER] Initialized with {interval_seconds}s interval")

    def _load_task_templates(self) -> List[Dict[str, Any]]:
        """Load task templates for autonomous generation"""
        return [
            # Analysis tasks
            {
                'type': 'analysis',
                'data': {
                    'target': 'market_trends',
                    'timeframe': '24h',
                    'depth': 'comprehensive'
                }
            },
            {
                'type': 'analysis',
                'data': {
                    'target': 'competitor_activity',
                    'metrics': ['pricing', 'features', 'market_share']
                }
            },
            {
                'type': 'analysis',
                'data': {
                    'target': 'customer_behavior',
                    'segments': ['enterprise', 'mid-market', 'startup']
                }
            },

            # Creation tasks
            {
                'type': 'creation',
                'data': {
                    'content_type': 'product_description',
                    'tone': 'professional',
                    'length': 'medium'
                }
            },
            {
                'type': 'creation',
                'data': {
                    'content_type': 'email_campaign',
                    'audience': 'enterprise',
                    'goal': 'engagement'
                }
            },
            {
                'type': 'creation',
                'data': {
                    'content_type': 'social_media_post',
                    'platform': 'linkedin',
                    'purpose': 'thought_leadership'
                }
            },

            # Research tasks
            {
                'type': 'research',
                'data': {
                    'topic': 'AI_automation_trends',
                    'depth': 'deep_dive',
                    'sources': ['industry_reports', 'research_papers']
                }
            },
            {
                'type': 'research',
                'data': {
                    'topic': 'customer_pain_points',
                    'industry': 'SaaS',
                    'focus': 'decision_makers'
                }
            },

            # Coordination tasks
            {
                'type': 'coordination',
                'data': {
                    'teams': ['sales', 'marketing', 'product'],
                    'goal': 'quarterly_planning',
                    'timeline': '30_days'
                }
            },
            {
                'type': 'coordination',
                'data': {
                    'teams': ['support', 'engineering'],
                    'goal': 'issue_resolution',
                    'priority': 'high'
                }
            },

            # Sales tasks
            {
                'type': 'sales',
                'data': {
                    'lead_score': 92,
                    'company_size': 'enterprise',
                    'industry': 'fintech',
                    'stage': 'qualification'
                }
            },
            {
                'type': 'sales',
                'data': {
                    'lead_score': 78,
                    'company_size': 'mid_market',
                    'urgency': 'medium'
                }
            },

            # Maintenance tasks
            {
                'type': 'maintenance',
                'data': {
                    'area': 'system_health',
                    'checks': ['performance', 'security', 'reliability']
                }
            },

            # Integration tasks
            {
                'type': 'integration',
                'data': {
                    'systems': ['crm', 'analytics', 'communication'],
                    'goal': 'data_sync'
                }
            }
        ]

    def start(self):
        """Start autonomous scheduler"""
        if self.running:
            print("[SCHEDULER] Already running")
            return

        self.running = True
        self.stats['start_time'] = datetime.now().isoformat()

        # Start background thread
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

        print(f"[SCHEDULER] Started - Running every {self.interval_seconds}s")
        print(f"[SCHEDULER] Loaded {len(self.task_templates)} task templates")

    def stop(self):
        """Stop autonomous scheduler"""
        if not self.running:
            print("[SCHEDULER] Not running")
            return

        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

        print("[SCHEDULER] Stopped")
        self._save_stats()

    def _run_loop(self):
        """Main execution loop"""
        while self.running:
            try:
                cycle_start = time.time()

                print(f"\n[CYCLE {self.stats['cycles_completed'] + 1}] Starting at {datetime.now().strftime('%H:%M:%S')}")

                # Execute tasks
                results = self._execute_cycle()

                # Update statistics
                self.stats['cycles_completed'] += 1
                self.stats['total_tasks_generated'] += results['tasks_executed']
                self.stats['total_outputs_created'] += results['outputs_generated']
                self.stats['last_cycle_time'] = datetime.now().isoformat()

                cycle_duration = time.time() - cycle_start

                print(f"[CYCLE {self.stats['cycles_completed']}] Completed in {cycle_duration:.2f}s")
                print(f"  Tasks: {results['tasks_executed']}")
                print(f"  Outputs: {results['outputs_generated']}")
                print(f"  Success rate: {results['success_rate']}%")
                print(f"  Revenue: ${results.get('revenue_generated', 0):,.2f}")
                print(f"  Deals: {results.get('deals_closed', 0)}")
                print(f"  Total revenue: ${self.stats['total_revenue_generated']:,.2f}")

                # Sleep until next cycle
                sleep_time = max(0, self.interval_seconds - cycle_duration)
                if sleep_time > 0 and self.running:
                    print(f"[SCHEDULER] Next cycle in {sleep_time:.0f}s")
                    time.sleep(sleep_time)

            except Exception as e:
                self.stats['errors'] += 1
                print(f"[ERROR] Cycle failed: {e}")
                time.sleep(10)  # Wait before retry

    def _execute_cycle(self) -> Dict[str, Any]:
        """Execute one cycle of autonomous tasks"""
        tasks_executed = 0
        outputs_generated = 0
        successful = 0
        revenue_generated = 0.0
        deals_closed = 0

        # Execute monetization strategy first (agents pursue revenue)
        try:
            print("\n  [REVENUE] Executing monetization strategy...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                self.monetization_engine.execute_monetization_strategy(max_concurrent_opportunities=50)
            )
            loop.close()

            revenue_generated = result.get('execution_summary', {}).get('total_revenue', 0.0)
            deals_closed_count = sum(
                stream_data.get('deals_closed', 0)
                for stream_data in result.get('revenue_stream_performance', {}).values()
            )
            deals_closed = deals_closed_count

            self.stats['total_revenue_generated'] += revenue_generated
            self.stats['total_deals_closed'] += deals_closed

            print(f"  [REVENUE] ${revenue_generated:,.2f} generated, {deals_closed} deals closed")
        except Exception as e:
            print(f"  [REVENUE ERROR] {e}")

        # Select tasks for this cycle (rotate through templates)
        cycle_offset = self.stats['cycles_completed'] % len(self.task_templates)
        tasks_to_run = [
            self.task_templates[(cycle_offset + i) % len(self.task_templates)]
            for i in range(min(5, len(self.task_templates)))  # Run 5 tasks per cycle
        ]

        for task_template in tasks_to_run:
            try:
                # Add cycle metadata
                task_data = task_template['data'].copy()
                task_data['cycle'] = self.stats['cycles_completed'] + 1
                task_data['timestamp'] = datetime.now().isoformat()

                # Assign and execute
                task_record = self.orchestrator.assign_task(
                    task_template['type'],
                    task_data
                )

                output_path = self.orchestrator.generate_output(task_record)

                tasks_executed += 1
                outputs_generated += 1
                successful += 1

                print(f"  [OK] {task_template['type']} -> {Path(output_path).name}")

            except Exception as e:
                tasks_executed += 1
                print(f"  [FAIL] {task_template['type']}: {e}")

        success_rate = (successful / tasks_executed * 100) if tasks_executed > 0 else 0

        return {
            'tasks_executed': tasks_executed,
            'outputs_generated': outputs_generated,
            'success_rate': round(success_rate, 1),
            'revenue_generated': revenue_generated,
            'deals_closed': deals_closed
        }

    def _save_stats(self):
        """Save statistics to file"""
        stats_dir = Path(__file__).parent / 'outputs'
        stats_dir.mkdir(exist_ok=True)

        stats_path = stats_dir / 'scheduler_stats.json'

        with open(stats_path, 'w') as f:
            json.dump(self.stats, f, indent=2)

        print(f"[STATS] Saved to {stats_path}")

    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status"""
        uptime = None
        if self.stats['start_time']:
            start = datetime.fromisoformat(self.stats['start_time'])
            uptime_seconds = (datetime.now() - start).total_seconds()
            uptime = self._format_duration(uptime_seconds)

        return {
            'running': self.running,
            'interval_seconds': self.interval_seconds,
            'uptime': uptime,
            'stats': self.stats,
            'task_templates': len(self.task_templates),
            'avg_tasks_per_cycle': (
                self.stats['total_tasks_generated'] / self.stats['cycles_completed']
                if self.stats['cycles_completed'] > 0 else 0
            )
        }

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}h {minutes}m {secs}s"


# Global scheduler instance
_scheduler = None


def get_scheduler() -> AutonomousScheduler:
    """Get global scheduler instance"""
    global _scheduler
    if _scheduler is None:
        _scheduler = AutonomousScheduler(interval_seconds=300)  # 5 minutes
    return _scheduler


def start_scheduler():
    """Start the autonomous scheduler"""
    scheduler = get_scheduler()
    scheduler.start()
    return scheduler


def stop_scheduler():
    """Stop the autonomous scheduler"""
    scheduler = get_scheduler()
    scheduler.stop()


def get_scheduler_status() -> Dict[str, Any]:
    """Get scheduler status"""
    scheduler = get_scheduler()
    return scheduler.get_status()


# Signal handlers for graceful shutdown
def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print("\n[SHUTDOWN] Received signal, stopping scheduler...")
    stop_scheduler()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def main():
    """Run autonomous scheduler in standalone mode"""
    print("="*60)
    print("SINCOR AUTONOMOUS SCHEDULER")
    print("="*60)

    scheduler = start_scheduler()

    print("\nScheduler running. Press Ctrl+C to stop.\n")

    try:
        # Keep main thread alive
        while True:
            time.sleep(60)

            # Print status every minute
            status = scheduler.get_status()
            print(f"\n[STATUS] Cycles: {status['stats']['cycles_completed']} | "
                  f"Tasks: {status['stats']['total_tasks_generated']} | "
                  f"Outputs: {status['stats']['total_outputs_created']} | "
                  f"Uptime: {status['uptime']}")

    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Stopping scheduler...")
        scheduler.stop()
        print("[DONE] Scheduler stopped")


if __name__ == "__main__":
    main()
