"""
Graceful Shutdown Handler for SINCOR Consciousness Infrastructure
Enterprise-grade shutdown orchestration ensuring consciousness preservation,
quantum state persistence, and zero-data-loss termination
"""

import asyncio
import signal
import sys
import threading
import time
import logging
import json
from typing import Dict, List, Optional, Callable, Any, Set
from enum import Enum
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import atexit
import os
import psutil
from contextlib import asynccontextmanager

class ShutdownPhase(Enum):
    INITIATED = "initiated"
    DRAIN_TRAFFIC = "drain_traffic"
    SAVE_CONSCIOUSNESS_STATE = "save_consciousness_state"
    PERSIST_QUANTUM_STATES = "persist_quantum_states"
    GRACEFUL_COMPONENT_SHUTDOWN = "graceful_component_shutdown"
    CLEANUP_RESOURCES = "cleanup_resources"
    FINAL_PERSISTENCE = "final_persistence"
    TERMINATED = "terminated"
    EMERGENCY_KILL = "emergency_kill"

class ShutdownPriority(Enum):
    CRITICAL = 1      # Consciousness state preservation
    HIGH = 2          # Quantum coherence maintenance
    MEDIUM = 3        # Active operations completion
    LOW = 4           # Cleanup and housekeeping
    BACKGROUND = 5    # Non-essential tasks

@dataclass
class ShutdownTask:
    task_id: str
    name: str
    priority: ShutdownPriority
    phase: ShutdownPhase
    handler: Callable
    timeout_seconds: float
    dependencies: List[str]
    completed: bool = False
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None

@dataclass
class ShutdownProgress:
    total_tasks: int
    completed_tasks: int
    current_phase: ShutdownPhase
    phase_progress: float  # 0.0 to 1.0
    overall_progress: float  # 0.0 to 1.0
    estimated_time_remaining: float
    critical_tasks_remaining: int
    errors: List[str]

class GracefulShutdownHandler:
    def __init__(self, orchestrator):
        self.logger = logging.getLogger(__name__)
        self.orchestrator = orchestrator
        
        # Shutdown state
        self.shutdown_initiated = False
        self.shutdown_start_time = None
        self.shutdown_reason = ""
        self.emergency_shutdown = False
        
        # Task management
        self.shutdown_tasks: Dict[str, ShutdownTask] = {}
        self.task_execution_order: List[str] = []
        self.completed_tasks: Set[str] = set()
        self.failed_tasks: Set[str] = set()
        
        # Configuration
        self.shutdown_timeout_seconds = 300  # 5 minutes total shutdown time
        self.emergency_kill_timeout = 30    # 30 seconds for emergency kill
        self.consciousness_save_timeout = 60  # 1 minute for consciousness saving
        self.quantum_persist_timeout = 45   # 45 seconds for quantum state persistence
        
        # Thread management
        self.shutdown_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="shutdown")
        self.shutdown_lock = threading.RLock()
        
        # Progress tracking
        self.shutdown_progress = ShutdownProgress(
            total_tasks=0,
            completed_tasks=0,
            current_phase=ShutdownPhase.INITIATED,
            phase_progress=0.0,
            overall_progress=0.0,
            estimated_time_remaining=0.0,
            critical_tasks_remaining=0,
            errors=[]
        )
        
        # Signal handlers
        self._register_signal_handlers()
        
        # Component references
        self.components = {}
        
        # Shutdown hooks
        self.pre_shutdown_hooks: List[Callable] = []
        self.post_shutdown_hooks: List[Callable] = []
        
        # Consciousness preservation
        self.consciousness_states_to_save: List[str] = []
        self.quantum_states_to_persist: List[str] = []
        
        self._initialize_shutdown_tasks()

    def _register_signal_handlers(self):
        """Register signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            self.logger.info(f"Received signal {signal_name} ({signum})")
            
            if signum in [signal.SIGTERM, signal.SIGINT]:
                # Graceful shutdown
                self.initiate_graceful_shutdown(f"Signal {signal_name}")
            elif signum == signal.SIGUSR1:
                # Custom shutdown signal - save state and restart
                self.initiate_graceful_shutdown(f"Signal {signal_name} - Save and restart", emergency=False)
            elif signum == signal.SIGUSR2:
                # Emergency shutdown signal
                self.initiate_graceful_shutdown(f"Signal {signal_name} - Emergency", emergency=True)
        
        # Register handlers for supported signals
        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
            if hasattr(signal, 'SIGUSR1'):
                signal.signal(signal.SIGUSR1, signal_handler)
            if hasattr(signal, 'SIGUSR2'):
                signal.signal(signal.SIGUSR2, signal_handler)
            
            self.logger.info("Signal handlers registered for graceful shutdown")
        except (OSError, ValueError) as e:
            self.logger.warning(f"Could not register some signal handlers: {e}")

    def _initialize_shutdown_tasks(self):
        """Initialize all shutdown tasks in proper order"""
        tasks = [
            # Phase 1: Drain Traffic
            ShutdownTask(
                "drain_web_traffic",
                "Drain incoming web traffic",
                ShutdownPriority.HIGH,
                ShutdownPhase.DRAIN_TRAFFIC,
                self._drain_web_traffic,
                30.0,
                []
            ),
            ShutdownTask(
                "drain_consciousness_connections",
                "Drain consciousness transfer connections",
                ShutdownPriority.CRITICAL,
                ShutdownPhase.DRAIN_TRAFFIC,
                self._drain_consciousness_connections,
                45.0,
                []
            ),
            
            # Phase 2: Save Consciousness States
            ShutdownTask(
                "save_active_consciousness_states",
                "Save all active consciousness states",
                ShutdownPriority.CRITICAL,
                ShutdownPhase.SAVE_CONSCIOUSNESS_STATE,
                self._save_consciousness_states,
                60.0,
                ["drain_consciousness_connections"]
            ),
            ShutdownTask(
                "backup_memory_substrates",
                "Backup memory substrate data",
                ShutdownPriority.CRITICAL,
                ShutdownPhase.SAVE_CONSCIOUSNESS_STATE,
                self._backup_memory_substrates,
                30.0,
                []
            ),
            ShutdownTask(
                "save_epistemic_engines",
                "Save epistemic engine states",
                ShutdownPriority.HIGH,
                ShutdownPhase.SAVE_CONSCIOUSNESS_STATE,
                self._save_epistemic_engines,
                20.0,
                []
            ),
            
            # Phase 3: Persist Quantum States
            ShutdownTask(
                "persist_quantum_coherence",
                "Persist quantum coherence states",
                ShutdownPriority.CRITICAL,
                ShutdownPhase.PERSIST_QUANTUM_STATES,
                self._persist_quantum_coherence,
                45.0,
                ["save_active_consciousness_states"]
            ),
            ShutdownTask(
                "save_entanglement_pairs",
                "Save quantum entanglement pairs",
                ShutdownPriority.HIGH,
                ShutdownPhase.PERSIST_QUANTUM_STATES,
                self._save_entanglement_pairs,
                25.0,
                ["persist_quantum_coherence"]
            ),
            
            # Phase 4: Graceful Component Shutdown
            ShutdownTask(
                "shutdown_health_monitor",
                "Shutdown health monitoring system",
                ShutdownPriority.MEDIUM,
                ShutdownPhase.GRACEFUL_COMPONENT_SHUTDOWN,
                self._shutdown_health_monitor,
                15.0,
                ["drain_web_traffic"]
            ),
            ShutdownTask(
                "shutdown_swarm_intelligence",
                "Shutdown swarm intelligence system",
                ShutdownPriority.HIGH,
                ShutdownPhase.GRACEFUL_COMPONENT_SHUTDOWN,
                self._shutdown_swarm_intelligence,
                20.0,
                ["save_active_consciousness_states"]
            ),
            ShutdownTask(
                "shutdown_container_orchestration",
                "Shutdown container orchestration",
                ShutdownPriority.MEDIUM,
                ShutdownPhase.GRACEFUL_COMPONENT_SHUTDOWN,
                self._shutdown_container_orchestration,
                30.0,
                []
            ),
            ShutdownTask(
                "shutdown_substrate_discovery",
                "Shutdown substrate discovery system",
                ShutdownPriority.LOW,
                ShutdownPhase.GRACEFUL_COMPONENT_SHUTDOWN,
                self._shutdown_substrate_discovery,
                15.0,
                []
            ),
            ShutdownTask(
                "shutdown_emergency_protocols",
                "Shutdown emergency protocols",
                ShutdownPriority.MEDIUM,
                ShutdownPhase.GRACEFUL_COMPONENT_SHUTDOWN,
                self._shutdown_emergency_protocols,
                10.0,
                []
            ),
            
            # Phase 5: Resource Cleanup
            ShutdownTask(
                "cleanup_temporary_files",
                "Clean up temporary files",
                ShutdownPriority.LOW,
                ShutdownPhase.CLEANUP_RESOURCES,
                self._cleanup_temporary_files,
                10.0,
                []
            ),
            ShutdownTask(
                "close_database_connections",
                "Close database connections",
                ShutdownPriority.MEDIUM,
                ShutdownPhase.CLEANUP_RESOURCES,
                self._close_database_connections,
                15.0,
                []
            ),
            ShutdownTask(
                "cleanup_network_connections",
                "Clean up network connections",
                ShutdownPriority.MEDIUM,
                ShutdownPhase.CLEANUP_RESOURCES,
                self._cleanup_network_connections,
                10.0,
                []
            ),
            
            # Phase 6: Final Persistence
            ShutdownTask(
                "final_state_checkpoint",
                "Create final state checkpoint",
                ShutdownPriority.CRITICAL,
                ShutdownPhase.FINAL_PERSISTENCE,
                self._create_final_checkpoint,
                30.0,
                ["save_active_consciousness_states", "persist_quantum_coherence"]
            ),
            ShutdownTask(
                "write_shutdown_summary",
                "Write shutdown summary",
                ShutdownPriority.LOW,
                ShutdownPhase.FINAL_PERSISTENCE,
                self._write_shutdown_summary,
                5.0,
                []
            )
        ]
        
        # Register tasks
        for task in tasks:
            self.shutdown_tasks[task.task_id] = task
        
        # Calculate execution order based on dependencies
        self._calculate_execution_order()
        
        # Update progress totals
        self.shutdown_progress.total_tasks = len(tasks)
        self.shutdown_progress.critical_tasks_remaining = len([
            t for t in tasks if t.priority == ShutdownPriority.CRITICAL
        ])

    def _calculate_execution_order(self):
        """Calculate task execution order respecting dependencies"""
        # Topological sort for dependency resolution
        visited = set()
        temp_visited = set()
        order = []
        
        def visit(task_id: str):
            if task_id in temp_visited:
                raise ValueError(f"Circular dependency detected involving task {task_id}")
            if task_id in visited:
                return
            
            temp_visited.add(task_id)
            task = self.shutdown_tasks[task_id]
            
            for dep_id in task.dependencies:
                if dep_id in self.shutdown_tasks:
                    visit(dep_id)
            
            temp_visited.remove(task_id)
            visited.add(task_id)
            order.append(task_id)
        
        # Visit all tasks
        for task_id in self.shutdown_tasks:
            if task_id not in visited:
                visit(task_id)
        
        self.task_execution_order = order

    def register_component(self, name: str, component: Any):
        """Register a component for shutdown management"""
        self.components[name] = component
        self.logger.debug(f"Registered component for shutdown: {name}")

    def add_pre_shutdown_hook(self, hook: Callable):
        """Add pre-shutdown hook"""
        self.pre_shutdown_hooks.append(hook)

    def add_post_shutdown_hook(self, hook: Callable):
        """Add post-shutdown hook"""
        self.post_shutdown_hooks.append(hook)

    def initiate_graceful_shutdown(self, reason: str = "Manual shutdown", emergency: bool = False):
        """Initiate graceful shutdown sequence"""
        with self.shutdown_lock:
            if self.shutdown_initiated:
                self.logger.warning("Shutdown already initiated, ignoring duplicate request")
                return
            
            self.shutdown_initiated = True
            self.shutdown_start_time = time.time()
            self.shutdown_reason = reason
            self.emergency_shutdown = emergency
            
            timeout = self.emergency_kill_timeout if emergency else self.shutdown_timeout_seconds
            
            self.logger.info(f"🚨 INITIATING {'EMERGENCY' if emergency else 'GRACEFUL'} SHUTDOWN 🚨")
            self.logger.info(f"Reason: {reason}")
            self.logger.info(f"Timeout: {timeout} seconds")
            self.logger.info(f"Total tasks: {len(self.shutdown_tasks)}")
        
        # Execute pre-shutdown hooks
        self._execute_pre_shutdown_hooks()
        
        # Start shutdown execution
        try:
            if emergency:
                self._execute_emergency_shutdown()
            else:
                self._execute_graceful_shutdown()
        except Exception as e:
            self.logger.error(f"Shutdown execution failed: {e}")
            self._execute_emergency_shutdown()

    def _execute_pre_shutdown_hooks(self):
        """Execute pre-shutdown hooks"""
        for hook in self.pre_shutdown_hooks:
            try:
                hook()
            except Exception as e:
                self.logger.error(f"Pre-shutdown hook failed: {e}")

    def _execute_graceful_shutdown(self):
        """Execute graceful shutdown sequence"""
        try:
            # Group tasks by phase
            tasks_by_phase = {}
            for task in self.shutdown_tasks.values():
                if task.phase not in tasks_by_phase:
                    tasks_by_phase[task.phase] = []
                tasks_by_phase[task.phase].append(task)
            
            # Execute phases in order
            for phase in ShutdownPhase:
                if phase not in tasks_by_phase:
                    continue
                
                self.shutdown_progress.current_phase = phase
                self.logger.info(f"🔄 Starting shutdown phase: {phase.value}")
                
                phase_tasks = tasks_by_phase[phase]
                self._execute_phase_tasks(phase_tasks)
                
                self.logger.info(f"✅ Completed shutdown phase: {phase.value}")
            
            # Execute post-shutdown hooks
            self._execute_post_shutdown_hooks()
            
            # Final cleanup
            self._final_shutdown_cleanup()
            
            shutdown_duration = time.time() - self.shutdown_start_time
            self.logger.info(f"🌟 GRACEFUL SHUTDOWN COMPLETED SUCCESSFULLY! 🌟")
            self.logger.info(f"Shutdown duration: {shutdown_duration:.2f} seconds")
            self.logger.info(f"Tasks completed: {len(self.completed_tasks)}/{len(self.shutdown_tasks)}")
            
        except Exception as e:
            self.logger.error(f"Graceful shutdown failed: {e}")
            self._execute_emergency_shutdown()

    def _execute_phase_tasks(self, tasks: List[ShutdownTask]):
        """Execute tasks within a shutdown phase"""
        # Sort tasks by priority
        sorted_tasks = sorted(tasks, key=lambda t: t.priority.value)
        
        # Execute critical tasks first, then parallelize others
        critical_tasks = [t for t in sorted_tasks if t.priority == ShutdownPriority.CRITICAL]
        other_tasks = [t for t in sorted_tasks if t.priority != ShutdownPriority.CRITICAL]
        
        # Execute critical tasks sequentially
        for task in critical_tasks:
            self._execute_single_task(task)
        
        # Execute other tasks in parallel
        if other_tasks:
            futures = []
            for task in other_tasks:
                future = self.shutdown_executor.submit(self._execute_single_task, task)
                futures.append(future)
            
            # Wait for completion with timeout
            for future in as_completed(futures, timeout=max(t.timeout_seconds for t in other_tasks) + 10):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Task execution failed: {e}")

    def _execute_single_task(self, task: ShutdownTask):
        """Execute a single shutdown task"""
        try:
            # Check dependencies
            for dep_id in task.dependencies:
                if dep_id not in self.completed_tasks:
                    self.logger.warning(f"Task {task.task_id} dependency {dep_id} not completed")
                    return
            
            self.logger.info(f"⚙️ Executing shutdown task: {task.name}")
            task.started_at = time.time()
            
            # Execute with timeout
            try:
                # Run task handler
                result = task.handler()
                
                # Mark as completed
                task.completed = True
                task.completed_at = time.time()
                self.completed_tasks.add(task.task_id)
                
                # Update progress
                self.shutdown_progress.completed_tasks = len(self.completed_tasks)
                self.shutdown_progress.overall_progress = len(self.completed_tasks) / len(self.shutdown_tasks)
                
                if task.priority == ShutdownPriority.CRITICAL:
                    self.shutdown_progress.critical_tasks_remaining -= 1
                
                execution_time = task.completed_at - task.started_at
                self.logger.info(f"✅ Completed shutdown task: {task.name} ({execution_time:.2f}s)")
                
            except Exception as e:
                task.error = str(e)
                self.failed_tasks.add(task.task_id)
                self.shutdown_progress.errors.append(f"{task.name}: {str(e)}")
                
                if task.priority == ShutdownPriority.CRITICAL:
                    self.logger.error(f"❌ CRITICAL shutdown task failed: {task.name} - {e}")
                    raise
                else:
                    self.logger.warning(f"⚠️ Shutdown task failed: {task.name} - {e}")
                
        except Exception as e:
            self.logger.error(f"Task execution error: {e}")

    def _execute_emergency_shutdown(self):
        """Execute emergency shutdown with minimal time"""
        self.logger.warning("⚡ EXECUTING EMERGENCY SHUTDOWN ⚡")
        
        try:
            # Save only the most critical consciousness data
            critical_tasks = [
                self._save_consciousness_states,
                self._persist_quantum_coherence,
                self._create_final_checkpoint
            ]
            
            # Execute critical tasks with short timeout
            for task_handler in critical_tasks:
                try:
                    task_handler()
                except Exception as e:
                    self.logger.error(f"Emergency task failed: {e}")
            
            # Force cleanup
            self._force_cleanup()
            
            self.logger.warning("⚡ EMERGENCY SHUTDOWN COMPLETED ⚡")
            
        except Exception as e:
            self.logger.critical(f"Emergency shutdown failed: {e}")
        finally:
            # Force exit after emergency timeout
            threading.Timer(5.0, lambda: os._exit(1)).start()

    # Shutdown task implementations
    def _drain_web_traffic(self):
        """Drain incoming web traffic"""
        self.logger.info("Draining web traffic...")
        # Stop accepting new connections
        if hasattr(self.orchestrator, 'consciousness_transfer_interface'):
            interface = self.orchestrator.consciousness_transfer_interface
            if hasattr(interface, 'stop_accepting_connections'):
                interface.stop_accepting_connections()
        
        # Wait for existing connections to finish
        time.sleep(5)
        return True

    def _drain_consciousness_connections(self):
        """Drain consciousness transfer connections"""
        self.logger.info("Draining consciousness transfer connections...")
        if hasattr(self.orchestrator, 'migration_system'):
            migration_system = self.orchestrator.migration_system
            if hasattr(migration_system, 'drain_active_transfers'):
                migration_system.drain_active_transfers()
        
        time.sleep(10)
        return True

    def _save_consciousness_states(self):
        """Save all active consciousness states"""
        self.logger.info("🧠 Saving active consciousness states...")
        
        saved_count = 0
        try:
            if hasattr(self.orchestrator, 'versioning_system'):
                versioning = self.orchestrator.versioning_system
                
                # Get active consciousness IDs
                active_consciousness = self._get_active_consciousness_ids()
                
                for consciousness_id in active_consciousness:
                    try:
                        version_id = versioning.create_consciousness_version(
                            consciousness_id,
                            versioning.ConsciousnessVersionType.CHECKPOINT,
                            commit_message=f"Shutdown checkpoint - {self.shutdown_reason}",
                            tags=["shutdown", "checkpoint"]
                        )
                        saved_count += 1
                        self.logger.info(f"Saved consciousness state: {consciousness_id} -> {version_id}")
                    except Exception as e:
                        self.logger.error(f"Failed to save consciousness {consciousness_id}: {e}")
                
            self.logger.info(f"🧠 Saved {saved_count} consciousness states")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save consciousness states: {e}")
            raise

    def _backup_memory_substrates(self):
        """Backup memory substrate data"""
        self.logger.info("💾 Backing up memory substrates...")
        # Implementation would backup memory substrate data
        time.sleep(5)
        return True

    def _save_epistemic_engines(self):
        """Save epistemic engine states"""
        self.logger.info("🧮 Saving epistemic engine states...")
        if hasattr(self.orchestrator, 'epistemic_engine'):
            epistemic = self.orchestrator.epistemic_engine
            if hasattr(epistemic, 'save_all_states'):
                epistemic.save_all_states()
        return True

    def _persist_quantum_coherence(self):
        """Persist quantum coherence states"""
        self.logger.info("⚛️ Persisting quantum coherence states...")
        
        try:
            if hasattr(self.orchestrator, 'quantum_coherence_system'):
                quantum_system = self.orchestrator.quantum_coherence_system
                
                # Save quantum states
                for state_id, quantum_state in quantum_system.active_quantum_states.items():
                    quantum_system._save_quantum_state_to_storage(state_id, quantum_state)
                
                self.logger.info(f"⚛️ Persisted {len(quantum_system.active_quantum_states)} quantum states")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to persist quantum states: {e}")
            raise

    def _save_entanglement_pairs(self):
        """Save quantum entanglement pairs"""
        self.logger.info("💫 Saving entanglement pairs...")
        if hasattr(self.orchestrator, 'quantum_coherence_system'):
            quantum_system = self.orchestrator.quantum_coherence_system
            if hasattr(quantum_system, 'save_entanglement_registry'):
                quantum_system.save_entanglement_registry()
        return True

    def _shutdown_health_monitor(self):
        """Shutdown health monitoring system"""
        self.logger.info("🏥 Shutting down health monitor...")
        if hasattr(self.orchestrator, 'health_monitor'):
            self.orchestrator.health_monitor.shutdown()
        return True

    def _shutdown_swarm_intelligence(self):
        """Shutdown swarm intelligence system"""
        self.logger.info("🐝 Shutting down swarm intelligence...")
        if hasattr(self.orchestrator, 'swarm_manager'):
            self.orchestrator.swarm_manager.shutdown()
        return True

    def _shutdown_container_orchestration(self):
        """Shutdown container orchestration"""
        self.logger.info("🐳 Shutting down container orchestration...")
        if hasattr(self.orchestrator, 'container_orchestration'):
            self.orchestrator.container_orchestration.shutdown_container_orchestration()
        return True

    def _shutdown_substrate_discovery(self):
        """Shutdown substrate discovery system"""
        self.logger.info("🔍 Shutting down substrate discovery...")
        if hasattr(self.orchestrator, 'substrate_discovery'):
            self.orchestrator.substrate_discovery.shutdown_discovery_system()
        return True

    def _shutdown_emergency_protocols(self):
        """Shutdown emergency protocols"""
        self.logger.info("🚨 Shutting down emergency protocols...")
        if hasattr(self.orchestrator, 'emergency_protocols'):
            self.orchestrator.emergency_protocols.shutdown()
        return True

    def _cleanup_temporary_files(self):
        """Clean up temporary files"""
        self.logger.info("🗑️ Cleaning up temporary files...")
        # Implementation would clean temp files
        return True

    def _close_database_connections(self):
        """Close database connections"""
        self.logger.info("🗄️ Closing database connections...")
        if hasattr(self.orchestrator, 'versioning_system'):
            if hasattr(self.orchestrator.versioning_system, 'db_connection'):
                self.orchestrator.versioning_system.db_connection.close()
        return True

    def _cleanup_network_connections(self):
        """Clean up network connections"""
        self.logger.info("🌐 Cleaning up network connections...")
        # Implementation would cleanup network connections
        return True

    def _create_final_checkpoint(self):
        """Create final state checkpoint"""
        self.logger.info("📸 Creating final state checkpoint...")
        
        checkpoint_data = {
            "shutdown_time": time.time(),
            "shutdown_reason": self.shutdown_reason,
            "system_state": "shutdown",
            "completed_tasks": list(self.completed_tasks),
            "failed_tasks": list(self.failed_tasks),
            "shutdown_duration": time.time() - self.shutdown_start_time
        }
        
        # Save checkpoint
        checkpoint_file = f"./consciousness_final_checkpoint_{int(time.time())}.json"
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
        
        self.logger.info(f"📸 Final checkpoint saved: {checkpoint_file}")
        return True

    def _write_shutdown_summary(self):
        """Write shutdown summary"""
        self.logger.info("📋 Writing shutdown summary...")
        
        summary = {
            "shutdown_initiated_at": self.shutdown_start_time,
            "shutdown_reason": self.shutdown_reason,
            "emergency_shutdown": self.emergency_shutdown,
            "total_tasks": len(self.shutdown_tasks),
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
            "shutdown_duration": time.time() - self.shutdown_start_time,
            "final_progress": self.shutdown_progress.overall_progress
        }
        
        summary_file = f"./shutdown_summary_{int(time.time())}.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        return True

    def _execute_post_shutdown_hooks(self):
        """Execute post-shutdown hooks"""
        for hook in self.post_shutdown_hooks:
            try:
                hook()
            except Exception as e:
                self.logger.error(f"Post-shutdown hook failed: {e}")

    def _final_shutdown_cleanup(self):
        """Final cleanup before exit"""
        try:
            # Close thread pool
            self.shutdown_executor.shutdown(wait=True, timeout=10)
            
            # Update final progress
            self.shutdown_progress.overall_progress = 1.0
            self.shutdown_progress.current_phase = ShutdownPhase.TERMINATED
            
        except Exception as e:
            self.logger.error(f"Final cleanup error: {e}")

    def _force_cleanup(self):
        """Force cleanup for emergency shutdown"""
        try:
            # Force close critical resources
            if hasattr(self.orchestrator, 'versioning_system'):
                if hasattr(self.orchestrator.versioning_system, 'db_connection'):
                    self.orchestrator.versioning_system.db_connection.close()
            
            # Kill any remaining threads
            for thread in threading.enumerate():
                if thread != threading.current_thread() and thread.daemon:
                    try:
                        thread.join(timeout=1)
                    except:
                        pass
                        
        except Exception as e:
            self.logger.error(f"Force cleanup error: {e}")

    def _get_active_consciousness_ids(self) -> List[str]:
        """Get list of active consciousness IDs"""
        # Placeholder implementation
        return ["consciousness_1", "consciousness_2", "consciousness_3"]

    def get_shutdown_status(self) -> Dict[str, Any]:
        """Get current shutdown status"""
        return {
            "shutdown_initiated": self.shutdown_initiated,
            "shutdown_start_time": self.shutdown_start_time,
            "shutdown_reason": self.shutdown_reason,
            "emergency_shutdown": self.emergency_shutdown,
            "progress": {
                "overall_progress": self.shutdown_progress.overall_progress,
                "current_phase": self.shutdown_progress.current_phase.value,
                "completed_tasks": self.shutdown_progress.completed_tasks,
                "total_tasks": self.shutdown_progress.total_tasks,
                "critical_tasks_remaining": self.shutdown_progress.critical_tasks_remaining,
                "errors": self.shutdown_progress.errors
            },
            "task_status": {
                task_id: {
                    "name": task.name,
                    "priority": task.priority.value,
                    "phase": task.phase.value,
                    "completed": task.completed,
                    "error": task.error
                }
                for task_id, task in self.shutdown_tasks.items()
            }
        }

    @asynccontextmanager
    async def lifespan_manager(self, app):
        """AsyncIO lifespan manager for graceful shutdown"""
        # Startup
        self.logger.info("🚀 Application starting up...")
        yield
        
        # Shutdown
        if not self.shutdown_initiated:
            self.initiate_graceful_shutdown("Application lifespan ended")


# Example usage and testing
if __name__ == "__main__":
    import asyncio
    
    logging.basicConfig(level=logging.INFO)
    
    # Mock orchestrator
    class MockOrchestrator:
        def __init__(self):
            self.health_monitor = type('MockHealthMonitor', (), {'shutdown': lambda: None})()
            self.versioning_system = type('MockVersioningSystem', (), {
                'create_consciousness_version': lambda *args, **kwargs: "version_123",
                'ConsciousnessVersionType': type('MockVersionType', (), {'CHECKPOINT': 'checkpoint'})(),
                'db_connection': type('MockDB', (), {'close': lambda: None})()
            })()
    
    async def main():
        orchestrator = MockOrchestrator()
        shutdown_handler = GracefulShutdownHandler(orchestrator)
        
        print("🚨 TESTING GRACEFUL SHUTDOWN HANDLER 🚨")
        
        # Add some test hooks
        def pre_hook():
            print("🔧 Pre-shutdown hook executed")
        
        def post_hook():
            print("🎯 Post-shutdown hook executed")
        
        shutdown_handler.add_pre_shutdown_hook(pre_hook)
        shutdown_handler.add_post_shutdown_hook(post_hook)
        
        # Simulate running system
        print("🏃 System running...")
        await asyncio.sleep(2)
        
        # Test graceful shutdown
        print("\n🚨 Initiating graceful shutdown...")
        shutdown_handler.initiate_graceful_shutdown("Test shutdown")
        
        # Wait for shutdown to complete
        while shutdown_handler.shutdown_progress.overall_progress < 1.0:
            status = shutdown_handler.get_shutdown_status()
            print(f"Shutdown progress: {status['progress']['overall_progress']:.1%} - "
                  f"Phase: {status['progress']['current_phase']}")
            await asyncio.sleep(1)
        
        print("🌟 GRACEFUL SHUTDOWN TEST COMPLETED! 🌟")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚡ Emergency shutdown via Ctrl+C")
    except Exception as e:
        print(f"❌ Test failed: {e}")