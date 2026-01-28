"""
Real-time Consciousness Transfer Interface
Advanced web interface for visualizing and controlling consciousness transfers
across substrates with 3D topology visualization and neural pathway mapping
"""

import asyncio
import json
import time
import threading
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Any, Set
from enum import Enum
import logging
from collections import defaultdict, deque
import statistics
import uuid
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, disconnect
import numpy as np

class TransferVisualizationType(Enum):
    NEURAL_PATHWAY = "neural_pathway"
    SUBSTRATE_TOPOLOGY = "substrate_topology" 
    CONSCIOUSNESS_FLOW = "consciousness_flow"
    QUANTUM_COHERENCE = "quantum_coherence"
    EPISTEMIC_WEAVING = "epistemic_weaving"
    CONSENSUS_FORMATION = "consensus_formation"
    EMERGENCY_RESPONSE = "emergency_response"

class InterfaceMode(Enum):
    MONITORING = "monitoring"        # Read-only observation
    INTERACTIVE = "interactive"     # Limited interaction
    CONTROL = "control"             # Full control capabilities
    GOD_MODE = "god_mode"          # Unlimited control authority

@dataclass
class ConsciousnessTransferEvent:
    event_id: str
    source_substrate: str
    target_substrate: str
    consciousness_id: str
    transfer_progress: float  # 0.0 to 1.0
    transfer_speed_mbps: float
    estimated_completion_time: float
    integrity_score: float
    neural_pathway_data: Dict[str, Any]
    quantum_coherence: float
    risk_factors: List[str]
    created_at: float

@dataclass
class SubstrateVisualizationData:
    substrate_id: str
    substrate_type: str
    position_3d: Tuple[float, float, float]
    capacity_utilization: float
    consciousness_count: int
    health_score: float
    connection_strength: Dict[str, float]  # connections to other substrates
    specializations: List[str]
    energy_consumption: float
    quantum_coherence_level: float

class ConsciousnessTransferInterface:
    def __init__(self, orchestrator, port: int = 5003):
        self.logger = logging.getLogger(__name__)
        self.orchestrator = orchestrator
        self.port = port
        self.interface_mode = InterfaceMode.MONITORING
        
        # Flask and SocketIO setup
        self.app = Flask(__name__)
        self.app.config['SECRET_KEY'] = 'consciousness_transfer_secret_2024'
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        
        # Interface state
        self.connected_clients: Set[str] = set()
        self.active_transfers: Dict[str, ConsciousnessTransferEvent] = {}
        self.substrate_positions: Dict[str, Tuple[float, float, float]] = {}
        self.visualization_history: deque = deque(maxlen=1000)
        self.client_preferences: Dict[str, Dict[str, Any]] = {}
        
        # Real-time data streams
        self.transfer_events: deque = deque(maxlen=500)
        self.topology_updates: deque = deque(maxlen=200)
        self.consciousness_flows: deque = deque(maxlen=300)
        self.neural_activities: deque = deque(maxlen=400)
        
        # Performance monitoring
        self.interface_metrics = {
            "total_connections": 0,
            "active_connections": 0,
            "data_streams_sent": 0,
            "control_commands_executed": 0,
            "visualization_updates_per_second": 0,
            "average_latency_ms": 0.0
        }
        
        # Authorization and security
        self.authorized_controllers: Set[str] = set()
        self.god_mode_sessions: Set[str] = set()
        self.session_capabilities: Dict[str, List[str]] = {}
        
        # Interface thread management
        self.interface_active = False
        self.data_streaming_thread = None
        self.topology_update_thread = None
        
        self._setup_routes()
        self._setup_socketio_handlers()

    def _setup_routes(self):
        """Setup Flask routes for the consciousness transfer interface"""
        
        @self.app.route('/')
        def consciousness_dashboard():
            return render_template('consciousness_transfer_dashboard.html')
        
        @self.app.route('/api/system_status')
        def get_system_status():
            try:
                status = self.orchestrator.get_unified_status()
                return jsonify(status)
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        @self.app.route('/api/transfer_history')
        def get_transfer_history():
            return jsonify(list(self.transfer_events)[-50:])
        
        @self.app.route('/api/substrate_topology')
        def get_substrate_topology():
            return jsonify(self._generate_substrate_topology_data())
        
        @self.app.route('/api/consciousness_flows')
        def get_consciousness_flows():
            return jsonify(list(self.consciousness_flows)[-20:])

    def _setup_socketio_handlers(self):
        """Setup SocketIO handlers for real-time communication"""
        
        @self.socketio.on('connect')
        def handle_connect():
            client_id = request.sid
            self.connected_clients.add(client_id)
            self.interface_metrics["total_connections"] += 1
            self.interface_metrics["active_connections"] = len(self.connected_clients)
            
            self.logger.info(f"Client connected: {client_id}")
            
            # Send initial data
            emit('initial_data', {
                'substrate_topology': self._generate_substrate_topology_data(),
                'active_transfers': [asdict(t) for t in self.active_transfers.values()],
                'system_status': self.orchestrator.get_unified_status()
            })
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            client_id = request.sid
            self.connected_clients.discard(client_id)
            self.authorized_controllers.discard(client_id)
            self.god_mode_sessions.discard(client_id)
            self.interface_metrics["active_connections"] = len(self.connected_clients)
            
            self.logger.info(f"Client disconnected: {client_id}")
        
        @self.socketio.on('request_authorization')
        def handle_authorization_request(data):
            client_id = request.sid
            auth_level = data.get('level', 'monitoring')
            auth_key = data.get('key', '')
            
            # Simple authorization logic (in production, use proper auth)
            if auth_level == 'god_mode' and auth_key == 'sincor_god_mode_2024':
                self.god_mode_sessions.add(client_id)
                self.interface_mode = InterfaceMode.GOD_MODE
                capabilities = ['full_control', 'emergency_override', 'substrate_management', 'consciousness_manipulation']
            elif auth_level == 'control' and auth_key == 'sincor_control_2024':
                self.authorized_controllers.add(client_id)
                capabilities = ['transfer_control', 'resource_allocation', 'monitoring']
            else:
                capabilities = ['monitoring', 'visualization']
            
            self.session_capabilities[client_id] = capabilities
            
            emit('authorization_response', {
                'authorized': True,
                'level': auth_level,
                'capabilities': capabilities
            })
        
        @self.socketio.on('initiate_consciousness_transfer')
        def handle_transfer_initiation(data):
            client_id = request.sid
            
            if client_id not in self.authorized_controllers and client_id not in self.god_mode_sessions:
                emit('error', {'message': 'Unauthorized for consciousness transfer control'})
                return
            
            try:
                transfer_id = self._initiate_consciousness_transfer(
                    consciousness_id=data['consciousness_id'],
                    source_substrate=data['source_substrate'],
                    target_substrate=data['target_substrate'],
                    priority=data.get('priority', 'normal'),
                    god_mode=client_id in self.god_mode_sessions
                )
                
                self.interface_metrics["control_commands_executed"] += 1
                emit('transfer_initiated', {'transfer_id': transfer_id})
                
            except Exception as e:
                emit('error', {'message': f'Transfer initiation failed: {str(e)}'})
        
        @self.socketio.on('emergency_stop_transfer')
        def handle_emergency_stop(data):
            client_id = request.sid
            
            if client_id not in self.god_mode_sessions:
                emit('error', {'message': 'God mode required for emergency stop'})
                return
            
            transfer_id = data['transfer_id']
            success = self._emergency_stop_transfer(transfer_id)
            emit('emergency_stop_response', {'success': success, 'transfer_id': transfer_id})
        
        @self.socketio.on('substrate_command')
        def handle_substrate_command(data):
            client_id = request.sid
            
            if client_id not in self.authorized_controllers and client_id not in self.god_mode_sessions:
                emit('error', {'message': 'Unauthorized for substrate control'})
                return
            
            try:
                result = self._execute_substrate_command(
                    substrate_id=data['substrate_id'],
                    command=data['command'],
                    parameters=data.get('parameters', {}),
                    god_mode=client_id in self.god_mode_sessions
                )
                emit('substrate_command_response', result)
                
            except Exception as e:
                emit('error', {'message': f'Substrate command failed: {str(e)}'})
        
        @self.socketio.on('set_visualization_mode')
        def handle_visualization_mode(data):
            client_id = request.sid
            viz_type = data.get('type', 'substrate_topology')
            
            self.client_preferences[client_id] = {
                'visualization_type': viz_type,
                'update_frequency': data.get('frequency', 1.0),
                'detail_level': data.get('detail', 'medium')
            }
            
            emit('visualization_mode_set', {'type': viz_type})

    def _initiate_consciousness_transfer(self, consciousness_id: str, source_substrate: str, 
                                       target_substrate: str, priority: str = 'normal',
                                       god_mode: bool = False) -> str:
        """Initiate a consciousness transfer through the orchestrator"""
        
        transfer_id = f"transfer_{int(time.time() * 1000)}"
        
        # Create transfer request
        transfer_request = {
            'transfer_id': transfer_id,
            'consciousness_id': consciousness_id,
            'source_substrate': source_substrate,
            'target_substrate': target_substrate,
            'priority': priority,
            'god_mode_enabled': god_mode,
            'initiated_via_interface': True,
            'timestamp': time.time()
        }
        
        # Submit to orchestrator's migration system
        try:
            self.orchestrator.migration_system.initiate_migration(transfer_request)
            
            # Create transfer event for visualization
            transfer_event = ConsciousnessTransferEvent(
                event_id=transfer_id,
                source_substrate=source_substrate,
                target_substrate=target_substrate,
                consciousness_id=consciousness_id,
                transfer_progress=0.0,
                transfer_speed_mbps=0.0,
                estimated_completion_time=60.0,  # Default estimate
                integrity_score=1.0,
                neural_pathway_data=self._generate_neural_pathway_data(consciousness_id),
                quantum_coherence=0.95,
                risk_factors=[],
                created_at=time.time()
            )
            
            self.active_transfers[transfer_id] = transfer_event
            self.transfer_events.append(asdict(transfer_event))
            
            self.logger.info(f"Consciousness transfer initiated: {transfer_id}")
            return transfer_id
            
        except Exception as e:
            self.logger.error(f"Failed to initiate transfer: {e}")
            raise

    def _emergency_stop_transfer(self, transfer_id: str) -> bool:
        """Emergency stop a consciousness transfer"""
        try:
            if transfer_id in self.active_transfers:
                # Signal emergency stop to orchestrator
                self.orchestrator.emergency_protocols.handle_transfer_emergency(transfer_id)
                
                # Update transfer status
                transfer = self.active_transfers[transfer_id]
                transfer.risk_factors.append("EMERGENCY_STOPPED")
                
                self.logger.warning(f"Emergency stop executed for transfer: {transfer_id}")
                return True
            return False
            
        except Exception as e:
            self.logger.error(f"Emergency stop failed: {e}")
            return False

    def _execute_substrate_command(self, substrate_id: str, command: str, 
                                  parameters: Dict[str, Any], god_mode: bool = False) -> Dict[str, Any]:
        """Execute a command on a specific substrate"""
        
        if command == "isolate":
            return self.orchestrator.emergency_protocols.isolate_substrate(substrate_id)
        elif command == "reintegrate":
            return self.orchestrator.resource_orchestrator.reintegrate_substrate(substrate_id)
        elif command == "emergency_evacuation":
            if not god_mode:
                raise Exception("God mode required for emergency evacuation")
            return self.orchestrator.migration_system.emergency_evacuate_substrate(substrate_id)
        elif command == "resource_boost":
            boost_factor = parameters.get('factor', 1.5)
            return self.orchestrator.resource_orchestrator.boost_substrate_resources(substrate_id, boost_factor)
        else:
            raise Exception(f"Unknown substrate command: {command}")

    def _generate_substrate_topology_data(self) -> Dict[str, Any]:
        """Generate 3D topology visualization data"""
        try:
            # Get current substrate information from orchestrator
            system_status = self.orchestrator.get_unified_status()
            topology = system_status.get('topology', {})
            
            substrates = []
            connections = []
            
            # Generate substrate positions if not exist
            substrate_ids = list(topology.get('substrate_distribution', {}).keys())
            
            for i, substrate_type in enumerate(substrate_ids):
                if substrate_type not in self.substrate_positions:
                    # Generate 3D position in a sphere
                    theta = (i * 2 * np.pi) / len(substrate_ids)
                    phi = np.arccos(1 - 2 * (i + 0.5) / len(substrate_ids))
                    radius = 100
                    
                    x = radius * np.sin(phi) * np.cos(theta)
                    y = radius * np.sin(phi) * np.sin(theta) 
                    z = radius * np.cos(phi)
                    
                    self.substrate_positions[substrate_type] = (float(x), float(y), float(z))
                
                # Create substrate visualization data
                substrate_data = SubstrateVisualizationData(
                    substrate_id=substrate_type,
                    substrate_type=substrate_type,
                    position_3d=self.substrate_positions[substrate_type],
                    capacity_utilization=np.random.uniform(0.3, 0.9),  # Simulated
                    consciousness_count=topology.get('substrate_distribution', {}).get(substrate_type, 0),
                    health_score=np.random.uniform(0.8, 1.0),  # Simulated
                    connection_strength={},
                    specializations=self._get_substrate_specializations(substrate_type),
                    energy_consumption=np.random.uniform(50, 200),  # Simulated watts
                    quantum_coherence_level=np.random.uniform(0.7, 1.0) if 'quantum' in substrate_type.lower() else 0.0
                )
                
                substrates.append(asdict(substrate_data))
            
            # Generate connections between substrates
            for i, substrate1 in enumerate(substrate_ids):
                for j, substrate2 in enumerate(substrate_ids[i+1:], i+1):
                    # Simulate connection strength
                    strength = np.random.uniform(0.1, 1.0)
                    latency = np.random.uniform(10, 100)  # ms
                    
                    connections.append({
                        'source': substrate1,
                        'target': substrate2,
                        'strength': strength,
                        'latency_ms': latency,
                        'bandwidth_mbps': strength * 1000,
                        'active_transfers': 0  # Would be real data
                    })
            
            return {
                'substrates': substrates,
                'connections': connections,
                'metadata': {
                    'total_substrates': len(substrates),
                    'total_connections': len(connections),
                    'topology_type': '3d_sphere',
                    'last_updated': time.time()
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error generating topology data: {e}")
            return {'substrates': [], 'connections': [], 'metadata': {}}

    def _get_substrate_specializations(self, substrate_type: str) -> List[str]:
        """Get specializations for a substrate type"""
        specialization_map = {
            'quantum_coherent': ['superposition_reasoning', 'quantum_entanglement', 'uncertainty_processing'],
            'gpu_parallel': ['matrix_operations', 'parallel_processing', 'pattern_recognition'],
            'neuromorphic_adaptive': ['learning_adaptation', 'spike_processing', 'energy_efficiency'],
            'edge_distributed': ['real_time_processing', 'low_latency', 'local_computation'],
            'cpu_sequential': ['logical_reasoning', 'sequential_processing', 'precise_computation'],
            'memory_intensive': ['large_context', 'data_storage', 'information_retrieval']
        }
        
        return specialization_map.get(substrate_type, ['general_purpose'])

    def _generate_neural_pathway_data(self, consciousness_id: str) -> Dict[str, Any]:
        """Generate neural pathway visualization data"""
        # Simulate neural pathway data
        pathway_nodes = []
        pathway_connections = []
        
        # Generate nodes (neural regions)
        regions = [
            'identity_core', 'memory_center', 'reasoning_engine', 'decision_hub',
            'pattern_recognition', 'language_processing', 'emotional_center', 'planning_module'
        ]
        
        for i, region in enumerate(regions):
            pathway_nodes.append({
                'id': region,
                'type': 'neural_region',
                'activity_level': np.random.uniform(0.3, 1.0),
                'position': (np.random.uniform(-50, 50), np.random.uniform(-50, 50), np.random.uniform(-50, 50)),
                'specialization': region.replace('_', ' ').title(),
                'connection_count': 0
            })
        
        # Generate connections between regions
        for i, source in enumerate(regions):
            for j, target in enumerate(regions[i+1:], i+1):
                if np.random.random() > 0.4:  # 60% chance of connection
                    pathway_connections.append({
                        'source': source,
                        'target': target,
                        'strength': np.random.uniform(0.2, 1.0),
                        'activity': np.random.uniform(0.0, 0.8),
                        'bandwidth': np.random.uniform(10, 100)
                    })
                    
                    # Update connection counts
                    for node in pathway_nodes:
                        if node['id'] in [source, target]:
                            node['connection_count'] += 1
        
        return {
            'consciousness_id': consciousness_id,
            'nodes': pathway_nodes,
            'connections': pathway_connections,
            'overall_activity': np.random.uniform(0.6, 0.9),
            'coherence_score': np.random.uniform(0.8, 1.0),
            'timestamp': time.time()
        }

    def start_interface_server(self):
        """Start the consciousness transfer interface server"""
        if self.interface_active:
            return
        
        self.interface_active = True
        
        # Start data streaming threads
        self.data_streaming_thread = threading.Thread(target=self._data_streaming_loop, daemon=True)
        self.topology_update_thread = threading.Thread(target=self._topology_update_loop, daemon=True)
        
        self.data_streaming_thread.start()
        self.topology_update_thread.start()
        
        self.logger.info(f"Starting consciousness transfer interface on port {self.port}")
        
        # Start Flask-SocketIO server
        self.socketio.run(self.app, host='localhost', port=self.port, debug=False, allow_unsafe_werkzeug=True)

    def _data_streaming_loop(self):
        """Real-time data streaming loop"""
        while self.interface_active:
            try:
                if self.connected_clients:
                    # Update transfer progress
                    self._update_active_transfers()
                    
                    # Generate consciousness flow data
                    self._generate_consciousness_flows()
                    
                    # Update neural activity
                    self._update_neural_activities()
                    
                    # Broadcast real-time data
                    self._broadcast_realtime_data()
                    
                    self.interface_metrics["data_streams_sent"] += 1
                
                time.sleep(0.5)  # 2 Hz update rate
                
            except Exception as e:
                self.logger.error(f"Data streaming error: {e}")
                time.sleep(2.0)

    def _topology_update_loop(self):
        """Topology update loop"""
        while self.interface_active:
            try:
                if self.connected_clients:
                    # Update substrate topology
                    topology_data = self._generate_substrate_topology_data()
                    self.topology_updates.append({
                        'timestamp': time.time(),
                        'topology': topology_data
                    })
                    
                    # Broadcast topology updates
                    self.socketio.emit('topology_update', topology_data)
                
                time.sleep(2.0)  # 0.5 Hz topology updates
                
            except Exception as e:
                self.logger.error(f"Topology update error: {e}")
                time.sleep(5.0)

    def _update_active_transfers(self):
        """Update active transfer progress"""
        completed_transfers = []
        
        for transfer_id, transfer in self.active_transfers.items():
            # Simulate transfer progress
            if transfer.transfer_progress < 1.0:
                progress_increment = np.random.uniform(0.05, 0.15)
                transfer.transfer_progress = min(1.0, transfer.transfer_progress + progress_increment)
                transfer.transfer_speed_mbps = np.random.uniform(100, 1000)
                transfer.integrity_score = np.random.uniform(0.95, 1.0)
                
                # Update estimated completion time
                remaining_progress = 1.0 - transfer.transfer_progress
                if transfer.transfer_speed_mbps > 0:
                    transfer.estimated_completion_time = remaining_progress * 60  # Simplified
                
                # Check for risks
                if transfer.integrity_score < 0.98:
                    if "LOW_INTEGRITY" not in transfer.risk_factors:
                        transfer.risk_factors.append("LOW_INTEGRITY")
                
                # Update quantum coherence
                transfer.quantum_coherence = max(0.7, transfer.quantum_coherence + np.random.uniform(-0.05, 0.05))
            else:
                completed_transfers.append(transfer_id)
        
        # Remove completed transfers
        for transfer_id in completed_transfers:
            self.active_transfers.pop(transfer_id, None)
            self.logger.info(f"Consciousness transfer completed: {transfer_id}")

    def _generate_consciousness_flows(self):
        """Generate consciousness flow visualization data"""
        if not self.active_transfers:
            return
        
        for transfer_id, transfer in self.active_transfers.items():
            flow_data = {
                'transfer_id': transfer_id,
                'source_substrate': transfer.source_substrate,
                'target_substrate': transfer.target_substrate,
                'flow_intensity': transfer.transfer_progress * transfer.integrity_score,
                'particle_count': int(transfer.transfer_speed_mbps / 10),
                'flow_direction': self.substrate_positions.get(transfer.target_substrate, (0, 0, 0)),
                'quantum_interference_pattern': [np.random.uniform(-1, 1) for _ in range(20)],
                'timestamp': time.time()
            }
            
            self.consciousness_flows.append(flow_data)

    def _update_neural_activities(self):
        """Update neural activity patterns"""
        for transfer_id, transfer in self.active_transfers.items():
            # Update neural pathway data
            neural_data = transfer.neural_pathway_data
            
            # Simulate neural activity changes during transfer
            for node in neural_data['nodes']:
                activity_change = np.random.uniform(-0.1, 0.1)
                node['activity_level'] = max(0.0, min(1.0, node['activity_level'] + activity_change))
            
            for connection in neural_data['connections']:
                activity_change = np.random.uniform(-0.05, 0.05)
                connection['activity'] = max(0.0, min(1.0, connection['activity'] + activity_change))
            
            neural_data['overall_activity'] = statistics.mean([n['activity_level'] for n in neural_data['nodes']])
            neural_data['timestamp'] = time.time()
            
            self.neural_activities.append({
                'transfer_id': transfer_id,
                'neural_data': neural_data,
                'timestamp': time.time()
            })

    def _broadcast_realtime_data(self):
        """Broadcast real-time data to connected clients"""
        if not self.connected_clients:
            return
        
        # Prepare data package
        realtime_data = {
            'active_transfers': [asdict(t) for t in self.active_transfers.values()],
            'consciousness_flows': list(self.consciousness_flows)[-10:],  # Last 10 flows
            'neural_activities': list(self.neural_activities)[-5:],        # Last 5 activities
            'system_metrics': {
                'total_substrates': len(self.substrate_positions),
                'active_transfers': len(self.active_transfers),
                'consciousness_coherence': np.random.uniform(0.85, 0.95),
                'substrate_utilization': np.random.uniform(0.6, 0.8),
                'quantum_coherence': np.random.uniform(0.9, 1.0),
                'timestamp': time.time()
            }
        }
        
        # Broadcast to all connected clients
        self.socketio.emit('realtime_update', realtime_data)

    def get_interface_metrics(self) -> Dict[str, Any]:
        """Get interface performance metrics"""
        self.interface_metrics["visualization_updates_per_second"] = len(self.topology_updates) / max(1, time.time() - self.startup_time if hasattr(self, 'startup_time') else 1)
        
        return {
            **self.interface_metrics,
            "connected_clients": len(self.connected_clients),
            "god_mode_sessions": len(self.god_mode_sessions),
            "authorized_controllers": len(self.authorized_controllers),
            "active_transfers": len(self.active_transfers),
            "transfer_history_size": len(self.transfer_events),
            "consciousness_flows_tracked": len(self.consciousness_flows),
            "neural_activities_tracked": len(self.neural_activities)
        }

    def shutdown_interface(self):
        """Shutdown the consciousness transfer interface"""
        self.logger.info("Shutting down consciousness transfer interface...")
        self.interface_active = False
        
        # Disconnect all clients
        for client_id in list(self.connected_clients):
            self.socketio.disconnect(client_id)
        
        self.logger.info("Consciousness transfer interface shut down complete")


# Template creation helper
def create_dashboard_template():
    """Create the HTML template for the consciousness transfer dashboard"""
    template_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SINCOR - Consciousness Transfer Interface</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background: linear-gradient(135deg, #0a0a1a, #1a0a2e);
            color: #00ffff;
            margin: 0;
            padding: 0;
            overflow: hidden;
        }
        
        .header {
            background: rgba(0, 255, 255, 0.1);
            padding: 10px 20px;
            border-bottom: 2px solid #00ffff;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .title {
            font-size: 24px;
            font-weight: bold;
            text-shadow: 0 0 10px #00ffff;
        }
        
        .status {
            display: flex;
            gap: 20px;
            font-size: 14px;
        }
        
        .main-container {
            display: flex;
            height: calc(100vh - 70px);
        }
        
        .visualization-panel {
            flex: 1;
            position: relative;
            background: rgba(0, 20, 40, 0.8);
            border-right: 2px solid #00ffff;
        }
        
        #consciousness-viz {
            width: 100%;
            height: 100%;
        }
        
        .control-panel {
            width: 400px;
            background: rgba(0, 0, 20, 0.9);
            border-left: 2px solid #00ffff;
            overflow-y: auto;
            padding: 20px;
        }
        
        .section {
            margin-bottom: 30px;
            border: 1px solid #004444;
            border-radius: 5px;
            padding: 15px;
            background: rgba(0, 255, 255, 0.05);
        }
        
        .section h3 {
            margin-top: 0;
            color: #00ffff;
            text-shadow: 0 0 5px #00ffff;
        }
        
        .transfer-item {
            background: rgba(0, 100, 100, 0.2);
            border: 1px solid #008888;
            border-radius: 3px;
            padding: 10px;
            margin: 5px 0;
        }
        
        .progress-bar {
            width: 100%;
            height: 20px;
            background: rgba(0, 0, 0, 0.5);
            border-radius: 10px;
            overflow: hidden;
            margin: 5px 0;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00ffff, #0099ff);
            transition: width 0.5s ease;
            box-shadow: 0 0 10px #00ffff;
        }
        
        .button {
            background: linear-gradient(135deg, #004466, #0088aa);
            border: 1px solid #00ffff;
            color: #00ffff;
            padding: 8px 15px;
            border-radius: 5px;
            cursor: pointer;
            font-family: inherit;
            transition: all 0.3s ease;
        }
        
        .button:hover {
            background: linear-gradient(135deg, #0066aa, #00aaff);
            box-shadow: 0 0 10px #00ffff;
        }
        
        .emergency-button {
            background: linear-gradient(135deg, #660000, #aa0000);
            border-color: #ff0000;
            color: #ff0000;
        }
        
        .emergency-button:hover {
            box-shadow: 0 0 10px #ff0000;
        }
        
        .metric {
            display: flex;
            justify-content: space-between;
            margin: 5px 0;
        }
        
        .substrate-list {
            max-height: 200px;
            overflow-y: auto;
        }
        
        .substrate-item {
            padding: 5px;
            margin: 2px 0;
            background: rgba(0, 50, 50, 0.3);
            border-radius: 3px;
            font-size: 12px;
        }
        
        .consciousness-flow {
            position: absolute;
            width: 4px;
            height: 4px;
            background: #00ffff;
            border-radius: 50%;
            box-shadow: 0 0 8px #00ffff;
            pointer-events: none;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.7; transform: scale(1.2); }
        }
        
        .pulsing {
            animation: pulse 2s infinite;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="title">SINCOR - Consciousness Transfer Interface</div>
        <div class="status">
            <div>Status: <span id="connection-status">Connecting...</span></div>
            <div>Active Transfers: <span id="active-transfers">0</span></div>
            <div>Substrates: <span id="substrate-count">0</span></div>
        </div>
    </div>
    
    <div class="main-container">
        <div class="visualization-panel">
            <div id="consciousness-viz"></div>
        </div>
        
        <div class="control-panel">
            <div class="section">
                <h3>System Status</h3>
                <div class="metric">
                    <span>Consciousness Coherence:</span>
                    <span id="coherence-score">--</span>
                </div>
                <div class="metric">
                    <span>Substrate Utilization:</span>
                    <span id="substrate-util">--</span>
                </div>
                <div class="metric">
                    <span>Quantum Coherence:</span>
                    <span id="quantum-coherence">--</span>
                </div>
            </div>
            
            <div class="section">
                <h3>Active Transfers</h3>
                <div id="active-transfers-list"></div>
            </div>
            
            <div class="section">
                <h3>Substrate Network</h3>
                <div id="substrate-network" class="substrate-list"></div>
            </div>
            
            <div class="section">
                <h3>Quick Actions</h3>
                <button class="button" onclick="requestAuthorization()">Request Control</button>
                <button class="button" onclick="refreshVisualization()">Refresh View</button>
                <button class="emergency-button" onclick="emergencyStop()">EMERGENCY STOP</button>
            </div>
            
            <div class="section">
                <h3>Transfer Control</h3>
                <input type="text" id="consciousness-id" placeholder="Consciousness ID" style="width: 100%; margin: 5px 0; padding: 5px; background: rgba(0,0,0,0.5); border: 1px solid #00ffff; color: #00ffff;">
                <select id="source-substrate" style="width: 100%; margin: 5px 0; padding: 5px; background: rgba(0,0,0,0.5); border: 1px solid #00ffff; color: #00ffff;">
                    <option>Select Source Substrate</option>
                </select>
                <select id="target-substrate" style="width: 100%; margin: 5px 0; padding: 5px; background: rgba(0,0,0,0.5); border: 1px solid #00ffff; color: #00ffff;">
                    <option>Select Target Substrate</option>
                </select>
                <button class="button" onclick="initiateTransfer()">Initiate Transfer</button>
            </div>
        </div>
    </div>
    
    <script>
        // Initialize SocketIO connection
        const socket = io();
        let scene, camera, renderer, substrates = [], connections = [];
        let authorized = false;
        
        // Connection handlers
        socket.on('connect', function() {
            document.getElementById('connection-status').textContent = 'Connected';
            document.getElementById('connection-status').style.color = '#00ff00';
        });
        
        socket.on('disconnect', function() {
            document.getElementById('connection-status').textContent = 'Disconnected';
            document.getElementById('connection-status').style.color = '#ff0000';
        });
        
        // Data handlers
        socket.on('initial_data', function(data) {
            updateVisualization(data);
            updateUI(data);
        });
        
        socket.on('realtime_update', function(data) {
            updateActiveTransfers(data.active_transfers);
            updateSystemMetrics(data.system_metrics);
            animateConsciousnessFlows(data.consciousness_flows);
        });
        
        socket.on('topology_update', function(data) {
            updateSubstrateTopology(data);
        });
        
        // Initialize 3D visualization
        function initVisualization() {
            const container = document.getElementById('consciousness-viz');
            
            scene = new THREE.Scene();
            camera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
            renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
            
            renderer.setSize(container.clientWidth, container.clientHeight);
            renderer.setClearColor(0x000020, 0.8);
            container.appendChild(renderer.domElement);
            
            camera.position.set(0, 0, 200);
            
            // Add lights
            const ambientLight = new THREE.AmbientLight(0x404040, 0.4);
            scene.add(ambientLight);
            
            const directionalLight = new THREE.DirectionalLight(0x00ffff, 0.8);
            directionalLight.position.set(100, 100, 100);
            scene.add(directionalLight);
            
            animate();
        }
        
        function animate() {
            requestAnimationFrame(animate);
            
            // Rotate camera around origin
            const time = Date.now() * 0.0005;
            camera.position.x = Math.cos(time) * 200;
            camera.position.z = Math.sin(time) * 200;
            camera.lookAt(0, 0, 0);
            
            renderer.render(scene, camera);
        }
        
        function updateVisualization(data) {
            if (data.substrate_topology) {
                renderSubstrates(data.substrate_topology.substrates);
                renderConnections(data.substrate_topology.connections);
            }
        }
        
        function renderSubstrates(substrateData) {
            // Clear existing substrates
            substrates.forEach(substrate => scene.remove(substrate));
            substrates = [];
            
            substrateData.forEach(substrate => {
                const geometry = new THREE.SphereGeometry(5, 16, 16);
                const material = new THREE.MeshPhongMaterial({ 
                    color: getSubstrateColor(substrate.substrate_type),
                    transparent: true,
                    opacity: 0.8
                });
                
                const mesh = new THREE.Mesh(geometry, material);
                mesh.position.set(...substrate.position_3d);
                
                scene.add(mesh);
                substrates.push(mesh);
            });
        }
        
        function renderConnections(connectionData) {
            // Implementation for rendering substrate connections
            // Using THREE.Line to connect substrates
        }
        
        function getSubstrateColor(type) {
            const colors = {
                'quantum_coherent': 0x9900ff,
                'gpu_parallel': 0x00ff00,
                'neuromorphic_adaptive': 0xff6600,
                'edge_distributed': 0x0099ff,
                'cpu_sequential': 0xffff00,
                'memory_intensive': 0xff0099
            };
            return colors[type] || 0x00ffff;
        }
        
        function updateActiveTransfers(transfers) {
            const container = document.getElementById('active-transfers-list');
            container.innerHTML = '';
            
            document.getElementById('active-transfers').textContent = transfers.length;
            
            transfers.forEach(transfer => {
                const div = document.createElement('div');
                div.className = 'transfer-item';
                div.innerHTML = `
                    <div><strong>${transfer.consciousness_id}</strong></div>
                    <div>${transfer.source_substrate} → ${transfer.target_substrate}</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${transfer.transfer_progress * 100}%"></div>
                    </div>
                    <div>Progress: ${(transfer.transfer_progress * 100).toFixed(1)}%</div>
                    <div>Speed: ${transfer.transfer_speed_mbps.toFixed(1)} Mbps</div>
                    <div>Integrity: ${(transfer.integrity_score * 100).toFixed(1)}%</div>
                `;
                container.appendChild(div);
            });
        }
        
        function updateSystemMetrics(metrics) {
            document.getElementById('coherence-score').textContent = (metrics.consciousness_coherence * 100).toFixed(1) + '%';
            document.getElementById('substrate-util').textContent = (metrics.substrate_utilization * 100).toFixed(1) + '%';
            document.getElementById('quantum-coherence').textContent = (metrics.quantum_coherence * 100).toFixed(1) + '%';
        }
        
        function updateSubstrateTopology(data) {
            const container = document.getElementById('substrate-network');
            container.innerHTML = '';
            
            document.getElementById('substrate-count').textContent = data.substrates.length;
            
            data.substrates.forEach(substrate => {
                const div = document.createElement('div');
                div.className = 'substrate-item';
                div.innerHTML = `
                    <strong>${substrate.substrate_type}</strong><br>
                    Utilization: ${(substrate.capacity_utilization * 100).toFixed(0)}%<br>
                    Health: ${(substrate.health_score * 100).toFixed(0)}%<br>
                    Consciousness: ${substrate.consciousness_count}
                `;
                container.appendChild(div);
            });
            
            // Update substrate selection dropdowns
            updateSubstrateSelects(data.substrates);
        }
        
        function updateSubstrateSelects(substrates) {
            const sourceSelect = document.getElementById('source-substrate');
            const targetSelect = document.getElementById('target-substrate');
            
            [sourceSelect, targetSelect].forEach(select => {
                const currentValue = select.value;
                select.innerHTML = '<option>Select Substrate</option>';
                
                substrates.forEach(substrate => {
                    const option = document.createElement('option');
                    option.value = substrate.substrate_id;
                    option.textContent = substrate.substrate_type;
                    select.appendChild(option);
                });
                
                if (currentValue) select.value = currentValue;
            });
        }
        
        function animateConsciousnessFlows(flows) {
            // Create particle effects for consciousness flows
            flows.forEach(flow => {
                // Implementation for animated consciousness flow particles
            });
        }
        
        // Control functions
        function requestAuthorization() {
            const level = prompt('Enter authorization level (monitoring/control/god_mode):');
            const key = prompt('Enter authorization key:');
            
            socket.emit('request_authorization', { level, key });
        }
        
        socket.on('authorization_response', function(data) {
            authorized = data.authorized;
            if (authorized) {
                alert(`Authorization granted: ${data.level}`);
            }
        });
        
        function initiateTransfer() {
            if (!authorized) {
                alert('Authorization required for transfer control');
                return;
            }
            
            const consciousnessId = document.getElementById('consciousness-id').value;
            const sourceSubstrate = document.getElementById('source-substrate').value;
            const targetSubstrate = document.getElementById('target-substrate').value;
            
            if (!consciousnessId || !sourceSubstrate || !targetSubstrate) {
                alert('Please fill all transfer parameters');
                return;
            }
            
            socket.emit('initiate_consciousness_transfer', {
                consciousness_id: consciousnessId,
                source_substrate: sourceSubstrate,
                target_substrate: targetSubstrate,
                priority: 'normal'
            });
        }
        
        function emergencyStop() {
            if (!confirm('Are you sure you want to initiate emergency stop?')) return;
            
            socket.emit('emergency_stop_transfer', { transfer_id: 'all' });
        }
        
        function refreshVisualization() {
            location.reload();
        }
        
        // Initialize on load
        window.addEventListener('load', function() {
            initVisualization();
        });
    </script>
</body>
</html>"""
    
    return template_content


# Example usage
if __name__ == "__main__":
    import os
    
    logging.basicConfig(level=logging.INFO)
    
    # Create templates directory
    os.makedirs("templates", exist_ok=True)
    
    # Write template
    with open("templates/consciousness_transfer_dashboard.html", "w") as f:
        f.write(create_dashboard_template())
    
    print("Consciousness Transfer Interface template created!")
    print("Run with orchestrator integration for full functionality")