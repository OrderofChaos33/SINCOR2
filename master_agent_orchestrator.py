#!/usr/bin/env python3
"""
SINCOR Master Agent Orchestrator

Comprehensive orchestration system that connects ALL SINCOR agent components:
- DAE Business Agents (12 agents)
- Gazette Compliance Agents  
- Marketing & Content Agents
- Intelligence & Business Agents
- Oversight & Coordination Agents
- Syndication & Distribution System
- DAO Management Agents

This system provides true agent-to-agent coordination and workflow automation.
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum

# Add paths for all agent modules
sys.path.append(str(Path(__file__).parent))
sys.path.append(str(Path(__file__).parent / "agents"))

@dataclass
class AgentTask:
    """Represents a task to be executed by an agent."""
    task_id: str
    agent_name: str
    task_type: str
    payload: Dict[str, Any]
    priority: int = 5  # 1-10, 10 is highest
    created_at: datetime = None
    scheduled_for: datetime = None
    dependencies: List[str] = None
    timeout: int = 300  # seconds
    retry_count: int = 0
    max_retries: int = 3

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.dependencies is None:
            self.dependencies = []

@dataclass
class AgentResult:
    """Represents the result of an agent task execution."""
    task_id: str
    agent_name: str
    success: bool
    result: Any
    error: str = None
    execution_time: float = 0
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

class AgentStatus(Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    ERROR = "error"
    MAINTENANCE = "maintenance"

class MasterOrchestrator:
    """Master orchestrator for all SINCOR agent systems."""
    
    def __init__(self):
        self.root_path = Path(__file__).parent
        self.agents = {}
        self.agent_status = {}
        self.task_queue = deque()
        self.active_tasks = {}
        self.completed_tasks = {}
        self.agent_workflows = {}
        
        # Coordination metrics
        self.coordination_score = 0
        self.total_tasks_processed = 0
        self.successful_tasks = 0
        self.failed_tasks = 0
        
        # Agent registry with their capabilities
        self.agent_registry = {
            # DAE Business Agents
            'board_agent': {'capabilities': ['governance', 'strategy', 'oversight'], 'module': 'dae_agents.board_agent'},
            'cfo_agent': {'capabilities': ['financial_analysis', 'budgeting', 'reporting'], 'module': 'dae_agents.cfo_agent'},
            'customer_agent': {'capabilities': ['customer_service', 'feedback', 'support'], 'module': 'dae_agents.customer_agent'},
            'data_agent': {'capabilities': ['data_analysis', 'reporting', 'insights'], 'module': 'dae_agents.data_agent'},
            'hr_agent': {'capabilities': ['recruitment', 'employee_management', 'training'], 'module': 'dae_agents.hr_agent'},
            'it_agent': {'capabilities': ['system_management', 'security', 'infrastructure'], 'module': 'dae_agents.it_agent'},
            'legal_agent': {'capabilities': ['compliance', 'contracts', 'risk_assessment'], 'module': 'dae_agents.legal_agent'},
            'marketing_agent': {'capabilities': ['campaign_management', 'branding', 'promotion'], 'module': 'dae_agents.marketing_agent'},
            'operations_agent': {'capabilities': ['process_management', 'optimization', 'coordination'], 'module': 'dae_agents.operations_agent'},
            'product_agent': {'capabilities': ['product_development', 'features', 'roadmap'], 'module': 'dae_agents.product_agent'},
            'sales_agent': {'capabilities': ['lead_generation', 'conversion', 'revenue'], 'module': 'dae_agents.sales_agent'},
            'strategy_agent': {'capabilities': ['planning', 'market_analysis', 'growth'], 'module': 'dae_agents.strategy_agent'},
            
            # Gazette Compliance Agents
            'aml_agent': {'capabilities': ['anti_money_laundering', 'compliance', 'monitoring'], 'module': 'agents.gazette.aml_agent'},
            'kyc_agent': {'capabilities': ['identity_verification', 'customer_onboarding', 'compliance'], 'module': 'agents.gazette.kyc_agent'},
            'sec_watchdog': {'capabilities': ['regulatory_compliance', 'sec_monitoring', 'reporting'], 'module': 'agents.gazette.sec_watchdog'},
            
            # Marketing & Content Agents
            'content_gen_agent': {'capabilities': ['content_creation', 'copywriting', 'seo'], 'module': 'agents.marketing.content_gen_agent'},
            'campaign_automation_agent': {'capabilities': ['campaign_automation', 'email_marketing', 'scheduling'], 'module': 'agents.marketing.campaign_automation_agent'},
            'profile_sync_agent': {'capabilities': ['profile_management', 'social_media', 'synchronization'], 'module': 'agents.marketing.profile_sync_agent'},
            'stem_clip_agent': {'capabilities': ['video_editing', 'content_clipping', 'media_processing'], 'module': 'agents.marketing.stem_clip_agent'},
            
            # Intelligence & Business Agents  
            'business_intel_agent': {'capabilities': ['business_discovery', 'market_research', 'lead_generation'], 'module': 'agents.intelligence.business_intel_agent'},
            'industry_expansion_agent': {'capabilities': ['market_expansion', 'industry_analysis', 'growth_opportunities'], 'module': 'agents.intelligence.industry_expansion_agent'},
            'template_engine': {'capabilities': ['template_generation', 'content_templates', 'automation'], 'module': 'agents.intelligence.template_engine'},
            
            # Oversight & Coordination Agents
            'oversight_agent': {'capabilities': ['system_monitoring', 'quality_control', 'coordination'], 'module': 'agents.oversight.oversight_agent'},
            'build_coordination_agent': {'capabilities': ['build_management', 'deployment', 'coordination'], 'module': 'agents.oversight.build_coordination_agent'},
            
            # Task Processing Agents
            'daetime_scheduler': {'capabilities': ['task_scheduling', 'automation', 'workflow'], 'module': 'agents.daetime.scheduler_harness'},
            'taskpool_dispatcher': {'capabilities': ['task_distribution', 'load_balancing', 'coordination'], 'module': 'agents.taskpool.taskpool_dispatcher'},
            
            # Syndication System
            'syncore_syndicator': {'capabilities': ['content_syndication', 'distribution', 'publishing'], 'module': 'syncore_syndicator_mvp.agents.syndicator'}
        }
        
        # Initialize logging
        self.setup_logging()
        
        # Agent coordination workflows
        self.setup_workflows()
    
    def setup_logging(self):
        """Set up comprehensive logging system."""
        log_dir = self.root_path / "logs" / "orchestrator"
        log_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / "orchestrator.log"),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger("MasterOrchestrator")
        self.logger.info("Master Agent Orchestrator initialized")
    
    def setup_workflows(self):
        """Define agent coordination workflows."""
        self.agent_workflows = {
            # Lead Generation & Sales Workflow
            'lead_generation_workflow': {
                'agents': ['business_intel_agent', 'sales_agent', 'marketing_agent', 'customer_agent'],
                'steps': [
                    {'agent': 'business_intel_agent', 'task': 'discover_businesses', 'dependencies': []},
                    {'agent': 'sales_agent', 'task': 'qualify_leads', 'dependencies': ['discover_businesses']},
                    {'agent': 'marketing_agent', 'task': 'create_campaign', 'dependencies': ['qualify_leads']},
                    {'agent': 'customer_agent', 'task': 'follow_up', 'dependencies': ['create_campaign']}
                ]
            },
            
            # Content Creation & Syndication Workflow
            'content_syndication_workflow': {
                'agents': ['content_gen_agent', 'stem_clip_agent', 'syncore_syndicator', 'campaign_automation_agent'],
                'steps': [
                    {'agent': 'content_gen_agent', 'task': 'generate_content', 'dependencies': []},
                    {'agent': 'stem_clip_agent', 'task': 'process_media', 'dependencies': ['generate_content']},
                    {'agent': 'syncore_syndicator', 'task': 'syndicate_content', 'dependencies': ['process_media']},
                    {'agent': 'campaign_automation_agent', 'task': 'automate_distribution', 'dependencies': ['syndicate_content']}
                ]
            },
            
            # Business Operations Workflow
            'business_operations_workflow': {
                'agents': ['operations_agent', 'data_agent', 'oversight_agent', 'board_agent'],
                'steps': [
                    {'agent': 'data_agent', 'task': 'collect_metrics', 'dependencies': []},
                    {'agent': 'operations_agent', 'task': 'analyze_operations', 'dependencies': ['collect_metrics']},
                    {'agent': 'oversight_agent', 'task': 'quality_review', 'dependencies': ['analyze_operations']},
                    {'agent': 'board_agent', 'task': 'strategic_review', 'dependencies': ['quality_review']}
                ]
            },
            
            # Compliance & Legal Workflow
            'compliance_workflow': {
                'agents': ['aml_agent', 'kyc_agent', 'legal_agent', 'sec_watchdog'],
                'steps': [
                    {'agent': 'kyc_agent', 'task': 'verify_customer', 'dependencies': []},
                    {'agent': 'aml_agent', 'task': 'check_compliance', 'dependencies': ['verify_customer']},
                    {'agent': 'legal_agent', 'task': 'legal_review', 'dependencies': ['check_compliance']},
                    {'agent': 'sec_watchdog', 'task': 'regulatory_check', 'dependencies': ['legal_review']}
                ]
            }
        }
    
    def discover_available_agents(self):
        """Discover which agents are actually available and functional."""
        available_agents = {}
        
        for agent_name, config in self.agent_registry.items():
            try:
                # Check if agent module/file exists
                module_path = config.get('module', '')
                agent_status = {
                    'name': agent_name,
                    'status': AgentStatus.OFFLINE,
                    'capabilities': config.get('capabilities', []),
                    'module_path': module_path,
                    'last_seen': None,
                    'task_count': 0,
                    'success_rate': 0.0,
                    'available': False
                }
                
                # Try to determine if agent is available
                if 'dae_agents' in module_path:
                    # Check DAE agents directory
                    dae_path = self.root_path.parent / "dae_agents_gui" / "dae_agents" / agent_name.replace('_agent', '_agent')
                    if dae_path.exists():
                        agent_status['available'] = True
                        agent_status['status'] = AgentStatus.READY
                        
                elif module_path.startswith('agents.'):
                    # Check local agents directory
                    agent_file = self.root_path / module_path.replace('.', '/') + '.py'
                    if agent_file.exists():
                        agent_status['available'] = True
                        agent_status['status'] = AgentStatus.READY
                        
                elif 'syncore' in module_path:
                    # Check syndication system
                    sync_path = self.root_path.parent / "syncore_syndicator_mvp"
                    if sync_path.exists():
                        agent_status['available'] = True
                        agent_status['status'] = AgentStatus.READY
                
                available_agents[agent_name] = agent_status
                
            except Exception as e:
                self.logger.error(f"Error discovering agent {agent_name}: {e}")
                available_agents[agent_name] = {
                    'name': agent_name,
                    'status': AgentStatus.ERROR,
                    'error': str(e),
                    'available': False
                }
        
        self.agent_status = available_agents
        return available_agents
    
    def submit_task(self, agent_name: str, task_type: str, payload: Dict[str, Any], 
                   priority: int = 5, dependencies: List[str] = None) -> str:
        """Submit a task to the orchestration queue."""
        task_id = f"{agent_name}_{task_type}_{int(time.time())}_{len(self.task_queue)}"
        
        task = AgentTask(
            task_id=task_id,
            agent_name=agent_name,
            task_type=task_type,
            payload=payload,
            priority=priority,
            dependencies=dependencies or []
        )
        
        self.task_queue.append(task)
        self.logger.info(f"Task submitted: {task_id} for {agent_name}")
        
        return task_id
    
    def execute_workflow(self, workflow_name: str, context: Dict[str, Any] = None) -> str:
        """Execute a predefined agent workflow."""
        if workflow_name not in self.agent_workflows:
            raise ValueError(f"Unknown workflow: {workflow_name}")
        
        workflow = self.agent_workflows[workflow_name]
        workflow_id = f"workflow_{workflow_name}_{int(time.time())}"
        context = context or {}
        
        self.logger.info(f"Starting workflow: {workflow_name} (ID: {workflow_id})")
        
        # Submit all workflow tasks with proper dependencies
        task_ids = []
        for step in workflow['steps']:
            task_id = self.submit_task(
                agent_name=step['agent'],
                task_type=step['task'],
                payload={'workflow_id': workflow_id, 'context': context},
                dependencies=step.get('dependencies', [])
            )
            task_ids.append(task_id)
        
        return workflow_id
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        available_agents = list(self.agent_status.keys())
        active_agents = [name for name, status in self.agent_status.items() 
                        if status.get('status') == AgentStatus.READY]
        
        coordination_score = self.calculate_coordination_score()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'total_agents': len(available_agents),
            'active_agents': len(active_agents),
            'available_agents': available_agents,
            'active_agents_list': active_agents,
            'queued_tasks': len(self.task_queue),
            'active_tasks': len(self.active_tasks),
            'completed_tasks': len(self.completed_tasks),
            'coordination_score': coordination_score,
            'success_rate': (self.successful_tasks / max(1, self.total_tasks_processed)) * 100,
            'agent_capabilities': {name: status.get('capabilities', []) 
                                 for name, status in self.agent_status.items()},
            'available_workflows': list(self.agent_workflows.keys()),
            'system_health': 'excellent' if coordination_score > 90 else 
                           'good' if coordination_score > 70 else 
                           'fair' if coordination_score > 50 else 'poor'
        }
    
    def calculate_coordination_score(self) -> float:
        """Calculate system-wide coordination score."""
        if not self.agent_status:
            return 0.0
        
        # Base score from available agents
        total_agents = len(self.agent_status)
        available_agents = len([s for s in self.agent_status.values() if s.get('available', False)])
        base_score = (available_agents / total_agents) * 100 if total_agents > 0 else 0
        
        # Bonus for task processing
        if self.total_tasks_processed > 0:
            success_bonus = (self.successful_tasks / self.total_tasks_processed) * 20
            base_score += success_bonus
        
        # Bonus for workflow capabilities
        workflow_bonus = len(self.agent_workflows) * 2
        base_score += workflow_bonus
        
        # Penalty for failed tasks
        if self.failed_tasks > 0:
            failure_penalty = (self.failed_tasks / max(1, self.total_tasks_processed)) * 30
            base_score -= failure_penalty
        
        return max(0, min(100, base_score))
    
    def start_orchestration(self):
        """Start the master orchestration system."""
        self.logger.info("Starting Master Agent Orchestration System...")
        
        # Discover available agents
        self.discover_available_agents()
        
        # Log system status
        status = self.get_system_status()
        self.logger.info(f"System initialized with {status['total_agents']} agents")
        self.logger.info(f"Available agents: {status['active_agents']} active")
        self.logger.info(f"Coordination score: {status['coordination_score']:.1f}/100")
        
        # Start background task processor
        processor_thread = threading.Thread(target=self.task_processor_loop, daemon=True)
        processor_thread.start()
        
        # Start monitoring thread  
        monitor_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
        monitor_thread.start()
        
        self.logger.info("Master Orchestrator is now running...")
        return status
    
    def task_processor_loop(self):
        """Background task processing loop."""
        while True:
            try:
                if self.task_queue:
                    task = self.task_queue.popleft()
                    self.process_task(task)
                else:
                    time.sleep(1)  # Sleep when no tasks
            except Exception as e:
                self.logger.error(f"Task processor error: {e}")
                time.sleep(5)
    
    def process_task(self, task: AgentTask):
        """Process an individual agent task."""
        start_time = time.time()
        
        try:
            # Simulate task processing (replace with actual agent execution)
            self.logger.info(f"Processing task {task.task_id} for {task.agent_name}")
            
            # Mark task as active
            self.active_tasks[task.task_id] = task
            
            # Simulate work (replace with actual agent call)
            time.sleep(0.1)  # Simulate processing time
            
            # Create successful result
            result = AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                success=True,
                result={'status': 'completed', 'task_type': task.task_type},
                execution_time=time.time() - start_time
            )
            
            # Move to completed
            self.completed_tasks[task.task_id] = result
            del self.active_tasks[task.task_id]
            
            # Update metrics
            self.total_tasks_processed += 1
            self.successful_tasks += 1
            
            self.logger.info(f"Task {task.task_id} completed successfully")
            
        except Exception as e:
            # Handle task failure
            result = AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                success=False,
                result=None,
                error=str(e),
                execution_time=time.time() - start_time
            )
            
            self.completed_tasks[task.task_id] = result
            if task.task_id in self.active_tasks:
                del self.active_tasks[task.task_id]
            
            # Update metrics
            self.total_tasks_processed += 1
            self.failed_tasks += 1
            
            self.logger.error(f"Task {task.task_id} failed: {e}")
    
    def monitoring_loop(self):
        """Background monitoring and health check loop."""
        while True:
            try:
                # Update coordination score
                self.coordination_score = self.calculate_coordination_score()
                
                # Log system health periodically
                if int(time.time()) % 300 == 0:  # Every 5 minutes
                    status = self.get_system_status()
                    self.logger.info(f"System health: {status['system_health']} "
                                   f"(Score: {status['coordination_score']:.1f}/100)")
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Monitoring error: {e}")
                time.sleep(60)


def main():
    """Main entry point for the Master Agent Orchestrator."""
    orchestrator = MasterOrchestrator()
    
    try:
        # Start orchestration
        status = orchestrator.start_orchestration()
        
        print("=" * 60)
        print("SINCOR MASTER AGENT ORCHESTRATOR")
        print("=" * 60)
        print(f"Total Agents Discovered: {status['total_agents']}")
        print(f"Active Agents: {status['active_agents']}")
        print(f"Available Workflows: {len(status['available_workflows'])}")
        print(f"Coordination Score: {status['coordination_score']:.1f}/100")
        print(f"System Health: {status['system_health'].upper()}")
        print()
        print("Available Agent Capabilities:")
        for agent, capabilities in status['agent_capabilities'].items():
            if capabilities:
                print(f"  • {agent}: {', '.join(capabilities)}")
        print()
        print("Available Workflows:")
        for workflow in status['available_workflows']:
            print(f"  • {workflow}")
        print()
        
        # Demonstrate workflow execution
        print("Demonstrating Lead Generation Workflow...")
        workflow_id = orchestrator.execute_workflow('lead_generation_workflow', {
            'target_industry': 'auto_detailing',
            'location': 'local_market'
        })
        print(f"Workflow started: {workflow_id}")
        print()
        print("Master Orchestrator is running. Press Ctrl+C to stop.")
        
        # Keep running
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nMaster Orchestrator stopped by user.")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()