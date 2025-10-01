"""
SINCOR Full Orchestration Controller
Coordinates all engines, agents, and modules in a unified system
"""

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import time

# Import agent orchestrator
from agent_orchestrator import AgentOrchestrator


class OrchestrationController:
    """
    Master controller coordinating all SINCOR systems:
    - 43 AI agents (E-series)
    - 7 archetypes
    - Output generation
    - Task routing
    - Analytics tracking
    """

    def __init__(self):
        self.start_time = datetime.now()
        self.orchestrator = AgentOrchestrator()

        # Tracking metrics
        self.metrics = {
            'tasks_processed': 0,
            'outputs_generated': 0,
            'errors_encountered': 0,
            'total_processing_time_ms': 0
        }

        # Performance tracking
        self.performance_log = []

        print("[CONTROLLER] Full Orchestration Controller initialized")

    def route_task(self, task_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Intelligent task routing to appropriate agent/engine

        Args:
            task_request: {
                'type': str - Task type (analysis, creation, research, etc)
                'priority': str - Priority level (low, medium, high, critical)
                'data': dict - Task-specific data
                'timeout_ms': int - Optional timeout
            }

        Returns:
            Task result with output path and metrics
        """
        start_time = time.time()

        try:
            task_type = task_request.get('type', 'analysis')
            task_data = task_request.get('data', {})
            priority = task_request.get('priority', 'medium')

            # Add priority to task data
            task_data['priority'] = priority
            task_data['routed_at'] = datetime.now().isoformat()

            # Assign to agent
            task_record = self.orchestrator.assign_task(task_type, task_data)

            # Generate output
            output_path = self.orchestrator.generate_output(task_record)

            # Calculate metrics
            processing_time = int((time.time() - start_time) * 1000)

            # Update metrics
            self.metrics['tasks_processed'] += 1
            self.metrics['outputs_generated'] += 1
            self.metrics['total_processing_time_ms'] += processing_time

            # Log performance
            self.performance_log.append({
                'task_id': task_record['task_id'],
                'type': task_type,
                'priority': priority,
                'processing_time_ms': processing_time,
                'agent': task_record['assigned_agent'],
                'timestamp': datetime.now().isoformat()
            })

            return {
                'success': True,
                'task_record': task_record,
                'output_path': output_path,
                'processing_time_ms': processing_time
            }

        except Exception as e:
            self.metrics['errors_encountered'] += 1
            return {
                'success': False,
                'error': str(e),
                'processing_time_ms': int((time.time() - start_time) * 1000)
            }

    def batch_process(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process multiple tasks in batch

        Args:
            tasks: List of task request dictionaries

        Returns:
            Batch processing results with statistics
        """
        print(f"\n[BATCH] Processing {len(tasks)} tasks...")

        results = []
        failed = []
        total_time = 0

        for i, task in enumerate(tasks):
            result = self.route_task(task)

            if result['success']:
                results.append(result)
                print(f"  [{i+1}/{len(tasks)}] SUCCESS - {task.get('type', 'unknown')}")
            else:
                failed.append(result)
                print(f"  [{i+1}/{len(tasks)}] FAILED - {result.get('error', 'unknown error')}")

            total_time += result.get('processing_time_ms', 0)

        return {
            'total_tasks': len(tasks),
            'successful': len(results),
            'failed': len(failed),
            'total_processing_time_ms': total_time,
            'avg_processing_time_ms': total_time // len(tasks) if tasks else 0,
            'results': results,
            'failures': failed
        }

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""

        uptime_seconds = (datetime.now() - self.start_time).total_seconds()

        return {
            'status': 'operational',
            'uptime_seconds': uptime_seconds,
            'uptime_human': self._format_uptime(uptime_seconds),
            'agents': {
                'total': len(self.orchestrator.agents),
                'active': len([t for t in self.orchestrator.active_tasks if t['status'] == 'assigned']),
                'list': list(self.orchestrator.agents.keys())
            },
            'archetypes': {
                'total': len(self.orchestrator.archetypes),
                'list': list(self.orchestrator.archetypes.keys())
            },
            'metrics': self.metrics,
            'performance': {
                'avg_task_time_ms': (
                    self.metrics['total_processing_time_ms'] // self.metrics['tasks_processed']
                    if self.metrics['tasks_processed'] > 0 else 0
                ),
                'success_rate': (
                    (self.metrics['tasks_processed'] - self.metrics['errors_encountered']) /
                    self.metrics['tasks_processed'] * 100
                    if self.metrics['tasks_processed'] > 0 else 100.0
                )
            }
        }

    def get_agent_utilization(self) -> Dict[str, Any]:
        """Get agent utilization statistics"""

        agent_stats = {}

        for task in self.orchestrator.active_tasks:
            agent = task.get('assigned_agent')
            if agent:
                if agent not in agent_stats:
                    agent_stats[agent] = {
                        'tasks_assigned': 0,
                        'task_types': []
                    }
                agent_stats[agent]['tasks_assigned'] += 1
                agent_stats[agent]['task_types'].append(task.get('task_type'))

        # Calculate utilization percentage
        total_agents = len(self.orchestrator.agents)
        active_agents = len(agent_stats)
        utilization_pct = (active_agents / total_agents * 100) if total_agents > 0 else 0

        return {
            'total_agents': total_agents,
            'active_agents': active_agents,
            'idle_agents': total_agents - active_agents,
            'utilization_percentage': round(utilization_pct, 2),
            'agent_details': agent_stats
        }

    def get_performance_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report"""

        if not self.performance_log:
            return {
                'message': 'No performance data available yet',
                'tasks_processed': 0
            }

        # Calculate statistics
        processing_times = [p['processing_time_ms'] for p in self.performance_log]

        # Task type distribution
        task_types = {}
        for p in self.performance_log:
            task_type = p['type']
            task_types[task_type] = task_types.get(task_type, 0) + 1

        # Priority distribution
        priorities = {}
        for p in self.performance_log:
            priority = p.get('priority', 'unknown')
            priorities[priority] = priorities.get(priority, 0) + 1

        return {
            'total_tasks': len(self.performance_log),
            'processing_times': {
                'min_ms': min(processing_times),
                'max_ms': max(processing_times),
                'avg_ms': sum(processing_times) // len(processing_times),
                'total_ms': sum(processing_times)
            },
            'task_type_distribution': task_types,
            'priority_distribution': priorities,
            'recent_tasks': self.performance_log[-10:]  # Last 10 tasks
        }

    def export_full_report(self) -> str:
        """Export comprehensive system report to JSON"""

        report = {
            'report_id': f"full_system_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            'generated_at': datetime.now().isoformat(),
            'system_status': self.get_system_status(),
            'agent_utilization': self.get_agent_utilization(),
            'performance_report': self.get_performance_report(),
            'orchestrator_status': self.orchestrator.get_agent_status()
        }

        outputs_dir = Path(__file__).parent / 'outputs'
        outputs_dir.mkdir(exist_ok=True)

        report_path = outputs_dir / f"full_system_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)

        return str(report_path)

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime in human-readable format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours}h {minutes}m {secs}s"

    def run_system_test(self) -> Dict[str, Any]:
        """Run comprehensive system test"""

        print("\n" + "="*60)
        print("SINCOR FULL SYSTEM TEST")
        print("="*60)

        # Test 1: Basic task routing
        print("\n[TEST 1] Basic Task Routing...")
        test_task = {
            'type': 'analysis',
            'priority': 'high',
            'data': {
                'target': 'system_performance',
                'metrics': ['cpu', 'memory', 'disk']
            }
        }
        result1 = self.route_task(test_task)
        print(f"  Result: {'PASS' if result1['success'] else 'FAIL'}")

        # Test 2: Batch processing
        print("\n[TEST 2] Batch Processing...")
        batch_tasks = [
            {'type': 'analysis', 'priority': 'medium', 'data': {'test': 1}},
            {'type': 'creation', 'priority': 'high', 'data': {'test': 2}},
            {'type': 'research', 'priority': 'low', 'data': {'test': 3}}
        ]
        result2 = self.batch_process(batch_tasks)
        print(f"  Result: {result2['successful']}/{result2['total_tasks']} successful")

        # Test 3: System status
        print("\n[TEST 3] System Status...")
        status = self.get_system_status()
        print(f"  Agents: {status['agents']['total']}")
        print(f"  Archetypes: {status['archetypes']['total']}")
        print(f"  Tasks Processed: {status['metrics']['tasks_processed']}")

        # Test 4: Agent utilization
        print("\n[TEST 4] Agent Utilization...")
        utilization = self.get_agent_utilization()
        print(f"  Active Agents: {utilization['active_agents']}/{utilization['total_agents']}")
        print(f"  Utilization: {utilization['utilization_percentage']}%")

        # Test 5: Performance report
        print("\n[TEST 5] Performance Report...")
        perf = self.get_performance_report()
        print(f"  Total Tasks: {perf['total_tasks']}")
        if perf['total_tasks'] > 0:
            print(f"  Avg Processing Time: {perf['processing_times']['avg_ms']}ms")

        # Test 6: Full report export
        print("\n[TEST 6] Full Report Export...")
        report_path = self.export_full_report()
        print(f"  Report saved: {report_path}")

        print("\n" + "="*60)
        print("SYSTEM TEST COMPLETE")
        print("="*60)

        return {
            'test_1_basic_routing': result1['success'],
            'test_2_batch_processing': result2['successful'] == len(batch_tasks),
            'test_3_system_status': bool(status),
            'test_4_agent_utilization': utilization['utilization_percentage'] > 0,
            'test_5_performance_report': perf['total_tasks'] > 0,
            'test_6_report_export': os.path.exists(report_path),
            'all_tests_passed': all([
                result1['success'],
                result2['successful'] == len(batch_tasks),
                bool(status),
                utilization['utilization_percentage'] > 0,
                perf['total_tasks'] > 0,
                os.path.exists(report_path)
            ])
        }


def main():
    """Run orchestration controller"""

    print("="*60)
    print("SINCOR FULL ORCHESTRATION CONTROLLER")
    print("="*60)

    controller = OrchestrationController()

    # Run system test
    test_results = controller.run_system_test()

    print("\n[FINAL RESULTS]")
    for test_name, passed in test_results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status} {test_name}")

    if test_results['all_tests_passed']:
        print("\n[SUCCESS] All tests passed - system operational")
    else:
        print("\n[WARNING] Some tests failed - review logs")


if __name__ == "__main__":
    main()
