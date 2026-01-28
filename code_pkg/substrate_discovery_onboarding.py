"""
Substrate Discovery and Onboarding System
Advanced system for automatically discovering, evaluating, and onboarding
new computational resources into the SINCOR consciousness infrastructure
"""

import asyncio
import json
import time
import threading
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Any, Set, Union
from enum import Enum
import logging
from collections import defaultdict, deque
import statistics
import hashlib
import uuid
import socket
import psutil
import platform
import subprocess
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import pickle
import base64

class SubstrateType(Enum):
    QUANTUM_COHERENT = "quantum_coherent"
    GPU_PARALLEL = "gpu_parallel"
    NEUROMORPHIC_ADAPTIVE = "neuromorphic_adaptive"
    EDGE_DISTRIBUTED = "edge_distributed"
    CPU_SEQUENTIAL = "cpu_sequential"
    MEMORY_INTENSIVE = "memory_intensive"
    BANDWIDTH_OPTIMIZED = "bandwidth_optimized"
    HYBRID_SPECIALIZED = "hybrid_specialized"
    UNKNOWN = "unknown"

class DiscoveryMethod(Enum):
    NETWORK_SCAN = "network_scan"
    CLOUD_API = "cloud_api"
    KUBERNETES_DISCOVERY = "kubernetes_discovery"
    DOCKER_SWARM = "docker_swarm"
    BROADCAST_BEACON = "broadcast_beacon"
    DNS_SERVICE_DISCOVERY = "dns_service_discovery"
    DHCP_LEASE_SCAN = "dhcp_lease_scan"
    MDNS_DISCOVERY = "mdns_discovery"
    UPNP_DISCOVERY = "upnp_discovery"
    MANUAL_REGISTRATION = "manual_registration"

class OnboardingStage(Enum):
    DISCOVERED = "discovered"
    ANALYZING = "analyzing"
    TESTING = "testing"
    CAPABILITIES_ASSESSMENT = "capabilities_assessment"
    SECURITY_VALIDATION = "security_validation"
    PERFORMANCE_BENCHMARKING = "performance_benchmarking"
    INTEGRATION_PREPARATION = "integration_preparation"
    CONSCIOUSNESS_COMPATIBILITY = "consciousness_compatibility"
    ONBOARDED = "onboarded"
    REJECTED = "rejected"
    ERROR = "error"

@dataclass
class SubstrateCandidate:
    candidate_id: str
    discovery_method: DiscoveryMethod
    discovered_at: float
    network_address: str
    hostname: str
    port: int
    substrate_type: SubstrateType
    raw_capabilities: Dict[str, Any]
    estimated_performance: Dict[str, float]
    security_profile: Dict[str, Any]
    consciousness_compatibility_score: float
    onboarding_stage: OnboardingStage
    onboarding_progress: float  # 0.0 to 1.0
    test_results: Dict[str, Any]
    integration_requirements: List[str]
    risk_assessment: Dict[str, Any]
    metadata: Dict[str, Any]

@dataclass
class OnboardingPipeline:
    pipeline_id: str
    candidate: SubstrateCandidate
    stages: List[OnboardingStage]
    current_stage: OnboardingStage
    stage_results: Dict[OnboardingStage, Dict[str, Any]]
    started_at: float
    estimated_completion_time: float
    success_probability: float
    blocking_issues: List[str]
    automated: bool

class SubstrateDiscoveryOnboarding:
    def __init__(self, orchestrator, discovery_config: Dict[str, Any] = None):
        self.logger = logging.getLogger(__name__)
        self.orchestrator = orchestrator
        self.discovery_config = discovery_config or self._default_discovery_config()
        
        # Discovery state
        self.active_discoveries: Dict[DiscoveryMethod, bool] = {}
        self.discovered_candidates: Dict[str, SubstrateCandidate] = {}
        self.onboarding_pipelines: Dict[str, OnboardingPipeline] = {}
        self.onboarded_substrates: Dict[str, Dict[str, Any]] = {}
        self.rejected_candidates: Dict[str, Dict[str, Any]] = {}
        
        # Discovery methods
        self.discovery_methods: Dict[DiscoveryMethod, callable] = {
            DiscoveryMethod.NETWORK_SCAN: self._discover_network_scan,
            DiscoveryMethod.CLOUD_API: self._discover_cloud_api,
            DiscoveryMethod.KUBERNETES_DISCOVERY: self._discover_kubernetes,
            DiscoveryMethod.DOCKER_SWARM: self._discover_docker_swarm,
            DiscoveryMethod.BROADCAST_BEACON: self._discover_broadcast_beacon,
            DiscoveryMethod.DNS_SERVICE_DISCOVERY: self._discover_dns_service,
            DiscoveryMethod.MDNS_DISCOVERY: self._discover_mdns,
            DiscoveryMethod.UPNP_DISCOVERY: self._discover_upnp
        }
        
        # Onboarding stages
        self.onboarding_stages = [
            OnboardingStage.ANALYZING,
            OnboardingStage.TESTING,
            OnboardingStage.CAPABILITIES_ASSESSMENT,
            OnboardingStage.SECURITY_VALIDATION,
            OnboardingStage.PERFORMANCE_BENCHMARKING,
            OnboardingStage.INTEGRATION_PREPARATION,
            OnboardingStage.CONSCIOUSNESS_COMPATIBILITY,
            OnboardingStage.ONBOARDED
        ]
        
        # Performance metrics
        self.discovery_metrics = {
            "total_discovered": 0,
            "total_onboarded": 0,
            "total_rejected": 0,
            "discovery_rate_per_hour": 0.0,
            "onboarding_success_rate": 0.0,
            "average_onboarding_time": 0.0,
            "active_discoveries": 0,
            "substrate_type_distribution": defaultdict(int)
        }
        
        # Threading and coordination
        self.discovery_active = False
        self.discovery_threads: Dict[DiscoveryMethod, threading.Thread] = {}
        self.onboarding_thread_pool = ThreadPoolExecutor(max_workers=5)
        self.coordination_thread = None
        
        # Security and validation
        self.security_validators = {
            "encryption_support": self._validate_encryption_support,
            "authentication_methods": self._validate_authentication,
            "network_isolation": self._validate_network_isolation,
            "resource_access_control": self._validate_resource_access,
            "consciousness_isolation": self._validate_consciousness_isolation
        }
        
        # Performance benchmarks
        self.benchmark_suites = {
            SubstrateType.QUANTUM_COHERENT: self._benchmark_quantum_capabilities,
            SubstrateType.GPU_PARALLEL: self._benchmark_gpu_performance,
            SubstrateType.NEUROMORPHIC_ADAPTIVE: self._benchmark_neuromorphic_learning,
            SubstrateType.EDGE_DISTRIBUTED: self._benchmark_edge_performance,
            SubstrateType.CPU_SEQUENTIAL: self._benchmark_cpu_performance,
            SubstrateType.MEMORY_INTENSIVE: self._benchmark_memory_performance
        }

    def _default_discovery_config(self) -> Dict[str, Any]:
        """Default configuration for substrate discovery"""
        return {
            "network_scan_ranges": ["192.168.1.0/24", "10.0.0.0/8"],
            "discovery_ports": [8080, 9090, 5000, 3000, 8000, 7777],
            "discovery_interval_seconds": 300,  # 5 minutes
            "parallel_discoveries": True,
            "auto_onboard_trusted": True,
            "security_validation_required": True,
            "performance_threshold_minimum": 0.3,
            "consciousness_compatibility_minimum": 0.5,
            "max_concurrent_onboarding": 3,
            "discovery_timeout_seconds": 60,
            "cloud_providers": ["aws", "gcp", "azure", "digital_ocean"],
            "kubernetes_contexts": ["default", "production", "development"],
            "broadcast_port": 42424,  # SINCOR discovery port
            "service_discovery_domain": "_sincor._tcp.local"
        }

    async def start_discovery_system(self):
        """Start the substrate discovery and onboarding system"""
        if self.discovery_active:
            return
        
        self.discovery_active = True
        self.logger.info("Starting SINCOR Substrate Discovery & Onboarding System")
        
        # Start discovery methods
        await self._start_discovery_methods()
        
        # Start coordination thread
        self.coordination_thread = threading.Thread(target=self._coordination_loop, daemon=True)
        self.coordination_thread.start()
        
        self.logger.info("Substrate discovery system fully operational")

    async def _start_discovery_methods(self):
        """Start all configured discovery methods"""
        enabled_methods = self.discovery_config.get("enabled_methods", list(self.discovery_methods.keys()))
        
        for method in enabled_methods:
            if method in self.discovery_methods:
                self.active_discoveries[method] = True
                thread = threading.Thread(
                    target=self._discovery_method_loop,
                    args=(method,),
                    daemon=True
                )
                self.discovery_threads[method] = thread
                thread.start()
                self.logger.info(f"Started discovery method: {method.value}")

    def _discovery_method_loop(self, method: DiscoveryMethod):
        """Main loop for a specific discovery method"""
        while self.discovery_active and self.active_discoveries.get(method, False):
            try:
                discovery_func = self.discovery_methods[method]
                candidates = discovery_func()
                
                for candidate in candidates:
                    self._process_discovered_candidate(candidate)
                
                # Sleep based on discovery interval
                interval = self.discovery_config.get("discovery_interval_seconds", 300)
                time.sleep(interval)
                
            except Exception as e:
                self.logger.error(f"Discovery method {method.value} error: {e}")
                time.sleep(60)  # Back off on error

    def _discover_network_scan(self) -> List[SubstrateCandidate]:
        """Discover substrates via network scanning"""
        candidates = []
        scan_ranges = self.discovery_config.get("network_scan_ranges", [])
        discovery_ports = self.discovery_config.get("discovery_ports", [])
        
        for network_range in scan_ranges:
            try:
                # Parse network range (simplified implementation)
                if "/" in network_range:
                    base_ip = network_range.split("/")[0]
                    # Generate IP range (simplified)
                    base_parts = base_ip.split(".")
                    
                    for i in range(1, 255):  # Scan .1 to .254
                        ip = f"{base_parts[0]}.{base_parts[1]}.{base_parts[2]}.{i}"
                        
                        for port in discovery_ports:
                            if self._is_port_open(ip, port):
                                candidate = self._create_candidate_from_network(ip, port)
                                if candidate:
                                    candidates.append(candidate)
                
            except Exception as e:
                self.logger.error(f"Network scan error for {network_range}: {e}")
        
        return candidates

    def _discover_cloud_api(self) -> List[SubstrateCandidate]:
        """Discover substrates via cloud provider APIs"""
        candidates = []
        cloud_providers = self.discovery_config.get("cloud_providers", [])
        
        for provider in cloud_providers:
            try:
                if provider == "aws":
                    candidates.extend(self._discover_aws_instances())
                elif provider == "gcp":
                    candidates.extend(self._discover_gcp_instances())
                elif provider == "azure":
                    candidates.extend(self._discover_azure_instances())
                elif provider == "digital_ocean":
                    candidates.extend(self._discover_digital_ocean_droplets())
                    
            except Exception as e:
                self.logger.error(f"Cloud discovery error for {provider}: {e}")
        
        return candidates

    def _discover_kubernetes(self) -> List[SubstrateCandidate]:
        """Discover substrates in Kubernetes clusters"""
        candidates = []
        contexts = self.discovery_config.get("kubernetes_contexts", [])
        
        for context in contexts:
            try:
                # Use kubectl to discover pods/services (simplified)
                result = subprocess.run([
                    "kubectl", "get", "pods", "--context", context,
                    "-o", "json"
                ], capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    pods_data = json.loads(result.stdout)
                    for pod in pods_data.get("items", []):
                        candidate = self._create_candidate_from_k8s_pod(pod, context)
                        if candidate:
                            candidates.append(candidate)
                            
            except Exception as e:
                self.logger.error(f"Kubernetes discovery error for {context}: {e}")
        
        return candidates

    def _discover_docker_swarm(self) -> List[SubstrateCandidate]:
        """Discover substrates in Docker Swarm"""
        candidates = []
        
        try:
            # Use docker command to discover swarm nodes
            result = subprocess.run([
                "docker", "node", "ls", "--format", "{{.ID}}\t{{.Hostname}}\t{{.Status}}\t{{.Availability}}"
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = line.split("\t")
                        if len(parts) >= 4 and parts[2] == "Ready":
                            candidate = self._create_candidate_from_docker_node(parts)
                            if candidate:
                                candidates.append(candidate)
                                
        except Exception as e:
            self.logger.error(f"Docker Swarm discovery error: {e}")
        
        return candidates

    def _discover_broadcast_beacon(self) -> List[SubstrateCandidate]:
        """Discover substrates via broadcast beacons"""
        candidates = []
        broadcast_port = self.discovery_config.get("broadcast_port", 42424)
        
        try:
            # Listen for SINCOR broadcast beacons
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", broadcast_port))
            sock.settimeout(10.0)  # 10 second timeout
            
            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                    beacon_data = json.loads(data.decode())
                    
                    if beacon_data.get("type") == "sincor_substrate":
                        candidate = self._create_candidate_from_beacon(beacon_data, addr)
                        if candidate:
                            candidates.append(candidate)
                            
                except socket.timeout:
                    break
                except json.JSONDecodeError:
                    continue
                    
            sock.close()
            
        except Exception as e:
            self.logger.error(f"Broadcast beacon discovery error: {e}")
        
        return candidates

    def _discover_dns_service(self) -> List[SubstrateCandidate]:
        """Discover substrates via DNS service discovery"""
        candidates = []
        service_domain = self.discovery_config.get("service_discovery_domain", "_sincor._tcp.local")
        
        try:
            # Use system DNS tools to discover services (simplified)
            result = subprocess.run([
                "nslookup", "-type=SRV", service_domain
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                # Parse DNS SRV records
                srv_pattern = r"(\S+)\s+service = (\d+) (\d+) (\d+) (\S+)"
                matches = re.findall(srv_pattern, result.stdout)
                
                for match in matches:
                    hostname = match[4].rstrip(".")
                    port = int(match[3])
                    candidate = self._create_candidate_from_dns_srv(hostname, port)
                    if candidate:
                        candidates.append(candidate)
                        
        except Exception as e:
            self.logger.error(f"DNS service discovery error: {e}")
        
        return candidates

    def _discover_mdns(self) -> List[SubstrateCandidate]:
        """Discover substrates via mDNS/Bonjour"""
        candidates = []
        
        try:
            # Use avahi-browse or similar tool (Linux/Mac)
            result = subprocess.run([
                "avahi-browse", "-t", "_sincor._tcp", "--resolve", "--parsable"
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line and line.startswith("="):
                        parts = line.split(";")
                        if len(parts) >= 9:
                            hostname = parts[6]
                            port = int(parts[8])
                            candidate = self._create_candidate_from_mdns(hostname, port)
                            if candidate:
                                candidates.append(candidate)
                                
        except Exception as e:
            self.logger.debug(f"mDNS discovery not available or failed: {e}")
        
        return candidates

    def _discover_upnp(self) -> List[SubstrateCandidate]:
        """Discover substrates via UPnP"""
        candidates = []
        
        try:
            # Simple UPnP discovery using SSDP multicast
            import socket
            
            msg = [
                'M-SEARCH * HTTP/1.1',
                'HOST: 239.255.255.250:1900',
                'MAN: "ssdp:discover"',
                'ST: urn:sincor:device:substrate:1',
                'MX: 3',
                '',
                ''
            ]
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.settimeout(5.0)
            s.sendto('\r\n'.join(msg).encode(), ('239.255.255.250', 1900))
            
            while True:
                try:
                    data, addr = s.recvfrom(65507)
                    response = data.decode()
                    
                    if "urn:sincor:device:substrate" in response:
                        candidate = self._create_candidate_from_upnp(response, addr)
                        if candidate:
                            candidates.append(candidate)
                            
                except socket.timeout:
                    break
                    
            s.close()
            
        except Exception as e:
            self.logger.debug(f"UPnP discovery error: {e}")
        
        return candidates

    def _is_port_open(self, ip: str, port: int, timeout: float = 3.0) -> bool:
        """Check if a port is open on a given IP"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except:
            return False

    def _create_candidate_from_network(self, ip: str, port: int) -> Optional[SubstrateCandidate]:
        """Create candidate from network scan result"""
        try:
            # Try to get substrate information via HTTP
            response = requests.get(f"http://{ip}:{port}/sincor/info", timeout=10)
            if response.status_code == 200:
                info = response.json()
                
                candidate_id = f"net_{hashlib.md5(f'{ip}:{port}'.encode()).hexdigest()[:8]}"
                
                return SubstrateCandidate(
                    candidate_id=candidate_id,
                    discovery_method=DiscoveryMethod.NETWORK_SCAN,
                    discovered_at=time.time(),
                    network_address=ip,
                    hostname=info.get("hostname", ip),
                    port=port,
                    substrate_type=self._classify_substrate_type(info),
                    raw_capabilities=info,
                    estimated_performance=self._estimate_performance(info),
                    security_profile={},
                    consciousness_compatibility_score=0.0,
                    onboarding_stage=OnboardingStage.DISCOVERED,
                    onboarding_progress=0.0,
                    test_results={},
                    integration_requirements=[],
                    risk_assessment={},
                    metadata={"discovery_source": "network_scan"}
                )
        except:
            return None

    def _create_candidate_from_k8s_pod(self, pod: Dict[str, Any], context: str) -> Optional[SubstrateCandidate]:
        """Create candidate from Kubernetes pod"""
        try:
            metadata = pod.get("metadata", {})
            spec = pod.get("spec", {})
            status = pod.get("status", {})
            
            # Check if pod has SINCOR labels
            labels = metadata.get("labels", {})
            if not labels.get("app", "").startswith("sincor"):
                return None
            
            pod_ip = status.get("podIP", "")
            if not pod_ip:
                return None
            
            candidate_id = f"k8s_{metadata.get('name', 'unknown')}_{context}"
            
            return SubstrateCandidate(
                candidate_id=candidate_id,
                discovery_method=DiscoveryMethod.KUBERNETES_DISCOVERY,
                discovered_at=time.time(),
                network_address=pod_ip,
                hostname=metadata.get("name", "unknown"),
                port=8080,  # Default SINCOR port
                substrate_type=self._classify_substrate_from_k8s_labels(labels),
                raw_capabilities={"pod_info": pod, "context": context},
                estimated_performance=self._estimate_k8s_performance(spec),
                security_profile={},
                consciousness_compatibility_score=0.0,
                onboarding_stage=OnboardingStage.DISCOVERED,
                onboarding_progress=0.0,
                test_results={},
                integration_requirements=["kubernetes_access"],
                risk_assessment={},
                metadata={"discovery_source": "kubernetes", "context": context}
            )
            
        except Exception as e:
            self.logger.error(f"Error creating K8s candidate: {e}")
            return None

    def _create_candidate_from_beacon(self, beacon_data: Dict[str, Any], addr: Tuple[str, int]) -> Optional[SubstrateCandidate]:
        """Create candidate from broadcast beacon"""
        try:
            candidate_id = f"beacon_{beacon_data.get('substrate_id', 'unknown')}"
            
            return SubstrateCandidate(
                candidate_id=candidate_id,
                discovery_method=DiscoveryMethod.BROADCAST_BEACON,
                discovered_at=time.time(),
                network_address=addr[0],
                hostname=beacon_data.get("hostname", addr[0]),
                port=beacon_data.get("port", 8080),
                substrate_type=SubstrateType(beacon_data.get("substrate_type", "unknown")),
                raw_capabilities=beacon_data.get("capabilities", {}),
                estimated_performance=beacon_data.get("performance", {}),
                security_profile=beacon_data.get("security", {}),
                consciousness_compatibility_score=beacon_data.get("consciousness_compatibility", 0.0),
                onboarding_stage=OnboardingStage.DISCOVERED,
                onboarding_progress=0.0,
                test_results={},
                integration_requirements=[],
                risk_assessment={},
                metadata={"discovery_source": "broadcast_beacon"}
            )
            
        except Exception as e:
            self.logger.error(f"Error creating beacon candidate: {e}")
            return None

    def _classify_substrate_type(self, info: Dict[str, Any]) -> SubstrateType:
        """Classify substrate type from capability information"""
        cpu_info = info.get("cpu", {})
        gpu_info = info.get("gpu", {})
        memory_info = info.get("memory", {})
        special_features = info.get("special_features", [])
        
        # Check for quantum capabilities
        if "quantum_processor" in special_features or "quantum" in str(info).lower():
            return SubstrateType.QUANTUM_COHERENT
        
        # Check for GPU capabilities
        if gpu_info or "cuda" in special_features or "opencl" in special_features:
            return SubstrateType.GPU_PARALLEL
        
        # Check for neuromorphic capabilities
        if "neuromorphic" in special_features or "spike" in special_features:
            return SubstrateType.NEUROMORPHIC_ADAPTIVE
        
        # Check for high memory
        memory_gb = memory_info.get("total_gb", 0)
        if memory_gb > 64:
            return SubstrateType.MEMORY_INTENSIVE
        
        # Check for edge characteristics
        if "edge" in special_features or cpu_info.get("power_efficient", False):
            return SubstrateType.EDGE_DISTRIBUTED
        
        # Default to CPU sequential
        return SubstrateType.CPU_SEQUENTIAL

    def _classify_substrate_from_k8s_labels(self, labels: Dict[str, str]) -> SubstrateType:
        """Classify substrate type from Kubernetes labels"""
        substrate_type = labels.get("sincor.substrate.type", "")
        
        try:
            return SubstrateType(substrate_type)
        except ValueError:
            return SubstrateType.UNKNOWN

    def _estimate_performance(self, info: Dict[str, Any]) -> Dict[str, float]:
        """Estimate substrate performance from capability information"""
        cpu_info = info.get("cpu", {})
        memory_info = info.get("memory", {})
        network_info = info.get("network", {})
        
        return {
            "compute_score": cpu_info.get("cores", 1) * cpu_info.get("frequency_ghz", 1.0) / 10.0,
            "memory_score": memory_info.get("total_gb", 8) / 64.0,
            "bandwidth_score": network_info.get("bandwidth_gbps", 1.0) / 10.0,
            "latency_score": 1.0 - (network_info.get("latency_ms", 10) / 100.0),
            "overall_score": 0.5  # Default until benchmarked
        }

    def _estimate_k8s_performance(self, spec: Dict[str, Any]) -> Dict[str, float]:
        """Estimate performance from Kubernetes pod spec"""
        resources = spec.get("containers", [{}])[0].get("resources", {})
        requests = resources.get("requests", {})
        limits = resources.get("limits", {})
        
        cpu_request = self._parse_k8s_cpu(requests.get("cpu", "100m"))
        memory_request = self._parse_k8s_memory(requests.get("memory", "128Mi"))
        
        return {
            "compute_score": cpu_request / 4.0,  # Normalize to 4 CPU baseline
            "memory_score": memory_request / 8192,  # Normalize to 8GB baseline
            "bandwidth_score": 0.5,  # Unknown, assume medium
            "latency_score": 0.7,   # Cluster networking
            "overall_score": 0.5
        }

    def _parse_k8s_cpu(self, cpu_str: str) -> float:
        """Parse Kubernetes CPU resource string"""
        if cpu_str.endswith("m"):
            return float(cpu_str[:-1]) / 1000.0
        return float(cpu_str)

    def _parse_k8s_memory(self, memory_str: str) -> float:
        """Parse Kubernetes memory resource string in MB"""
        multipliers = {"Ki": 1/1024, "Mi": 1, "Gi": 1024, "Ti": 1024*1024}
        
        for suffix, multiplier in multipliers.items():
            if memory_str.endswith(suffix):
                return float(memory_str[:-2]) * multiplier
        
        return float(memory_str) / (1024*1024)  # Assume bytes

    def _process_discovered_candidate(self, candidate: SubstrateCandidate):
        """Process a newly discovered substrate candidate"""
        if candidate.candidate_id in self.discovered_candidates:
            return  # Already discovered
        
        self.discovered_candidates[candidate.candidate_id] = candidate
        self.discovery_metrics["total_discovered"] += 1
        self.discovery_metrics["substrate_type_distribution"][candidate.substrate_type.value] += 1
        
        self.logger.info(f"Discovered substrate candidate: {candidate.candidate_id} "
                        f"({candidate.substrate_type.value}) at {candidate.network_address}")
        
        # Start onboarding pipeline if auto-onboard is enabled
        if self.discovery_config.get("auto_onboard_trusted", True):
            self._initiate_onboarding_pipeline(candidate)

    def _initiate_onboarding_pipeline(self, candidate: SubstrateCandidate):
        """Initiate onboarding pipeline for a candidate"""
        if len(self.onboarding_pipelines) >= self.discovery_config.get("max_concurrent_onboarding", 3):
            self.logger.info(f"Onboarding queue full, deferring {candidate.candidate_id}")
            return
        
        pipeline_id = f"pipeline_{candidate.candidate_id}_{int(time.time())}"
        
        pipeline = OnboardingPipeline(
            pipeline_id=pipeline_id,
            candidate=candidate,
            stages=self.onboarding_stages.copy(),
            current_stage=OnboardingStage.ANALYZING,
            stage_results={},
            started_at=time.time(),
            estimated_completion_time=300.0,  # 5 minutes estimate
            success_probability=0.7,
            blocking_issues=[],
            automated=True
        )
        
        self.onboarding_pipelines[pipeline_id] = pipeline
        
        # Submit to thread pool
        future = self.onboarding_thread_pool.submit(self._execute_onboarding_pipeline, pipeline)
        
        self.logger.info(f"Started onboarding pipeline: {pipeline_id}")

    def _execute_onboarding_pipeline(self, pipeline: OnboardingPipeline):
        """Execute complete onboarding pipeline"""
        try:
            self.logger.info(f"Executing onboarding pipeline: {pipeline.pipeline_id}")
            
            for stage in pipeline.stages:
                pipeline.current_stage = stage
                stage_result = self._execute_onboarding_stage(pipeline, stage)
                pipeline.stage_results[stage] = stage_result
                
                if not stage_result.get("success", False):
                    pipeline.candidate.onboarding_stage = OnboardingStage.ERROR
                    pipeline.blocking_issues.append(f"Stage {stage.value} failed: {stage_result.get('error', 'Unknown')}")
                    self.rejected_candidates[pipeline.candidate.candidate_id] = asdict(pipeline.candidate)
                    self.discovery_metrics["total_rejected"] += 1
                    return
                
                # Update progress
                stage_index = pipeline.stages.index(stage)
                pipeline.candidate.onboarding_progress = (stage_index + 1) / len(pipeline.stages)
                
                self.logger.info(f"Pipeline {pipeline.pipeline_id} completed stage {stage.value}")
            
            # Success - onboard the substrate
            pipeline.candidate.onboarding_stage = OnboardingStage.ONBOARDED
            self._complete_substrate_onboarding(pipeline.candidate)
            self.discovery_metrics["total_onboarded"] += 1
            
            self.logger.info(f"Successfully onboarded substrate: {pipeline.candidate.candidate_id}")
            
        except Exception as e:
            self.logger.error(f"Onboarding pipeline error: {e}")
            pipeline.candidate.onboarding_stage = OnboardingStage.ERROR
            pipeline.blocking_issues.append(f"Pipeline execution error: {str(e)}")
        finally:
            # Remove from active pipelines
            self.onboarding_pipelines.pop(pipeline.pipeline_id, None)

    def _execute_onboarding_stage(self, pipeline: OnboardingPipeline, stage: OnboardingStage) -> Dict[str, Any]:
        """Execute a specific onboarding stage"""
        candidate = pipeline.candidate
        
        try:
            if stage == OnboardingStage.ANALYZING:
                return self._stage_analyze_candidate(candidate)
            elif stage == OnboardingStage.TESTING:
                return self._stage_test_connectivity(candidate)
            elif stage == OnboardingStage.CAPABILITIES_ASSESSMENT:
                return self._stage_assess_capabilities(candidate)
            elif stage == OnboardingStage.SECURITY_VALIDATION:
                return self._stage_validate_security(candidate)
            elif stage == OnboardingStage.PERFORMANCE_BENCHMARKING:
                return self._stage_benchmark_performance(candidate)
            elif stage == OnboardingStage.INTEGRATION_PREPARATION:
                return self._stage_prepare_integration(candidate)
            elif stage == OnboardingStage.CONSCIOUSNESS_COMPATIBILITY:
                return self._stage_test_consciousness_compatibility(candidate)
            else:
                return {"success": False, "error": f"Unknown stage: {stage}"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _stage_analyze_candidate(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Analyze candidate substrate"""
        self.logger.info(f"Analyzing candidate {candidate.candidate_id}")
        
        # Perform deep analysis
        analysis_results = {
            "substrate_classification": candidate.substrate_type.value,
            "estimated_capabilities": candidate.estimated_performance,
            "integration_complexity": self._assess_integration_complexity(candidate),
            "resource_requirements": self._calculate_resource_requirements(candidate),
            "compatibility_score": self._calculate_initial_compatibility(candidate)
        }
        
        candidate.metadata.update(analysis_results)
        
        return {"success": True, "analysis": analysis_results}

    def _stage_test_connectivity(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Test network connectivity and basic communication"""
        self.logger.info(f"Testing connectivity for {candidate.candidate_id}")
        
        try:
            # Test basic connectivity
            if not self._is_port_open(candidate.network_address, candidate.port, timeout=10):
                return {"success": False, "error": "Port not accessible"}
            
            # Test SINCOR API endpoints
            base_url = f"http://{candidate.network_address}:{candidate.port}"
            
            endpoints_to_test = [
                "/sincor/health",
                "/sincor/capabilities",
                "/sincor/status"
            ]
            
            connectivity_results = {}
            for endpoint in endpoints_to_test:
                try:
                    response = requests.get(f"{base_url}{endpoint}", timeout=10)
                    connectivity_results[endpoint] = {
                        "status_code": response.status_code,
                        "response_time_ms": response.elapsed.total_seconds() * 1000,
                        "accessible": response.status_code == 200
                    }
                except Exception as e:
                    connectivity_results[endpoint] = {
                        "accessible": False,
                        "error": str(e)
                    }
            
            # Check if minimum endpoints are accessible
            accessible_count = sum(1 for r in connectivity_results.values() if r.get("accessible", False))
            
            if accessible_count < 2:  # Need at least 2/3 endpoints
                return {"success": False, "error": "Insufficient endpoint accessibility"}
            
            return {"success": True, "connectivity": connectivity_results}
            
        except Exception as e:
            return {"success": False, "error": f"Connectivity test failed: {str(e)}"}

    def _stage_assess_capabilities(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Assess detailed substrate capabilities"""
        self.logger.info(f"Assessing capabilities for {candidate.candidate_id}")
        
        try:
            # Get detailed capabilities via API
            response = requests.get(
                f"http://{candidate.network_address}:{candidate.port}/sincor/capabilities",
                timeout=30
            )
            
            if response.status_code != 200:
                return {"success": False, "error": "Failed to retrieve capabilities"}
            
            capabilities = response.json()
            
            # Update candidate with detailed capabilities
            candidate.raw_capabilities.update(capabilities)
            
            # Recalculate performance estimates with detailed info
            candidate.estimated_performance = self._estimate_detailed_performance(capabilities)
            
            # Assess consciousness compatibility
            candidate.consciousness_compatibility_score = self._assess_consciousness_compatibility(capabilities)
            
            capabilities_assessment = {
                "detailed_capabilities": capabilities,
                "performance_estimate": candidate.estimated_performance,
                "consciousness_compatibility": candidate.consciousness_compatibility_score,
                "specialization_match": self._assess_specialization_match(candidate.substrate_type, capabilities)
            }
            
            return {"success": True, "assessment": capabilities_assessment}
            
        except Exception as e:
            return {"success": False, "error": f"Capability assessment failed: {str(e)}"}

    def _stage_validate_security(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Validate substrate security profile"""
        self.logger.info(f"Validating security for {candidate.candidate_id}")
        
        if not self.discovery_config.get("security_validation_required", True):
            return {"success": True, "security": {"validation_skipped": True}}
        
        security_results = {}
        
        # Run security validators
        for validator_name, validator_func in self.security_validators.items():
            try:
                result = validator_func(candidate)
                security_results[validator_name] = result
            except Exception as e:
                security_results[validator_name] = {"passed": False, "error": str(e)}
        
        # Calculate overall security score
        passed_validators = sum(1 for r in security_results.values() if r.get("passed", False))
        security_score = passed_validators / len(self.security_validators)
        
        candidate.security_profile = {
            "validation_results": security_results,
            "security_score": security_score,
            "validated_at": time.time()
        }
        
        # Minimum security threshold
        if security_score < 0.7:  # 70% of security checks must pass
            return {"success": False, "error": f"Security validation failed (score: {security_score:.2f})"}
        
        return {"success": True, "security": candidate.security_profile}

    def _stage_benchmark_performance(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Benchmark substrate performance"""
        self.logger.info(f"Benchmarking performance for {candidate.candidate_id}")
        
        benchmark_suite = self.benchmark_suites.get(candidate.substrate_type)
        if not benchmark_suite:
            return {"success": True, "benchmark": {"skipped": "No benchmark suite available"}}
        
        try:
            benchmark_results = benchmark_suite(candidate)
            
            # Update performance estimates with benchmark data
            candidate.estimated_performance.update(benchmark_results.get("performance_metrics", {}))
            
            # Check minimum performance threshold
            overall_score = benchmark_results.get("overall_score", 0.0)
            min_threshold = self.discovery_config.get("performance_threshold_minimum", 0.3)
            
            if overall_score < min_threshold:
                return {"success": False, "error": f"Performance below threshold: {overall_score:.2f} < {min_threshold}"}
            
            return {"success": True, "benchmark": benchmark_results}
            
        except Exception as e:
            return {"success": False, "error": f"Performance benchmarking failed: {str(e)}"}

    def _stage_prepare_integration(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Prepare substrate for integration"""
        self.logger.info(f"Preparing integration for {candidate.candidate_id}")
        
        integration_steps = []
        
        # Determine integration requirements
        requirements = candidate.integration_requirements.copy()
        
        # Add substrate-specific requirements
        if candidate.substrate_type == SubstrateType.QUANTUM_COHERENT:
            requirements.extend(["quantum_coherence_setup", "entanglement_protocols"])
        elif candidate.substrate_type == SubstrateType.GPU_PARALLEL:
            requirements.extend(["cuda_drivers", "parallel_computing_framework"])
        elif candidate.substrate_type == SubstrateType.NEUROMORPHIC_ADAPTIVE:
            requirements.extend(["spike_processing_setup", "learning_algorithms"])
        
        # Execute integration preparation steps
        for requirement in requirements:
            try:
                result = self._execute_integration_requirement(candidate, requirement)
                integration_steps.append({
                    "requirement": requirement,
                    "success": result.get("success", False),
                    "details": result
                })
            except Exception as e:
                integration_steps.append({
                    "requirement": requirement,
                    "success": False,
                    "error": str(e)
                })
        
        # Check if all critical requirements passed
        failed_steps = [s for s in integration_steps if not s["success"]]
        if failed_steps:
            return {"success": False, "error": f"Integration preparation failed: {len(failed_steps)} steps failed"}
        
        return {"success": True, "integration": {"steps": integration_steps}}

    def _stage_test_consciousness_compatibility(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        """Test consciousness compatibility"""
        self.logger.info(f"Testing consciousness compatibility for {candidate.candidate_id}")
        
        try:
            # Run consciousness compatibility tests
            compatibility_tests = [
                "identity_persistence_test",
                "state_transfer_test", 
                "cognitive_load_test",
                "consciousness_isolation_test"
            ]
            
            test_results = {}
            
            for test in compatibility_tests:
                result = self._execute_consciousness_test(candidate, test)
                test_results[test] = result
            
            # Calculate overall compatibility score
            passed_tests = sum(1 for r in test_results.values() if r.get("passed", False))
            compatibility_score = passed_tests / len(compatibility_tests)
            
            candidate.consciousness_compatibility_score = compatibility_score
            candidate.test_results.update(test_results)
            
            # Minimum consciousness compatibility threshold
            min_compatibility = self.discovery_config.get("consciousness_compatibility_minimum", 0.5)
            
            if compatibility_score < min_compatibility:
                return {"success": False, "error": f"Consciousness compatibility too low: {compatibility_score:.2f}"}
            
            return {"success": True, "consciousness": {
                "compatibility_score": compatibility_score,
                "test_results": test_results
            }}
            
        except Exception as e:
            return {"success": False, "error": f"Consciousness compatibility test failed: {str(e)}"}

    def _complete_substrate_onboarding(self, candidate: SubstrateCandidate):
        """Complete substrate onboarding and register with orchestrator"""
        self.logger.info(f"Completing onboarding for {candidate.candidate_id}")
        
        # Prepare substrate registration data
        substrate_registration = {
            "substrate_id": candidate.candidate_id,
            "substrate_type": candidate.substrate_type.value,
            "network_address": candidate.network_address,
            "port": candidate.port,
            "capabilities": candidate.raw_capabilities,
            "performance_profile": candidate.estimated_performance,
            "security_profile": candidate.security_profile,
            "consciousness_compatibility": candidate.consciousness_compatibility_score,
            "onboarded_at": time.time(),
            "discovery_method": candidate.discovery_method.value,
            "metadata": candidate.metadata
        }
        
        # Register with orchestrator
        try:
            self.orchestrator.resource_orchestrator.register_substrate(
                candidate.candidate_id,
                substrate_registration
            )
            
            # Add to onboarded substrates
            self.onboarded_substrates[candidate.candidate_id] = substrate_registration
            
            self.logger.info(f"Successfully registered substrate: {candidate.candidate_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to register substrate {candidate.candidate_id}: {e}")
            raise

    def _coordination_loop(self):
        """Main coordination loop"""
        while self.discovery_active:
            try:
                # Update discovery metrics
                self._update_discovery_metrics()
                
                # Check for stalled onboarding pipelines
                self._check_stalled_pipelines()
                
                # Cleanup old candidates
                self._cleanup_old_candidates()
                
                # Adjust discovery parameters
                self._adjust_discovery_parameters()
                
                time.sleep(30)  # Run every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Coordination loop error: {e}")
                time.sleep(60)

    def _update_discovery_metrics(self):
        """Update discovery performance metrics"""
        current_time = time.time()
        
        # Calculate rates
        if hasattr(self, '_last_metrics_update'):
            time_diff = current_time - self._last_metrics_update
            if time_diff > 0:
                discovery_diff = self.discovery_metrics["total_discovered"] - self.discovery_metrics.get("_last_discovered", 0)
                self.discovery_metrics["discovery_rate_per_hour"] = (discovery_diff / time_diff) * 3600
        
        self.discovery_metrics["_last_discovered"] = self.discovery_metrics["total_discovered"]
        self._last_metrics_update = current_time
        
        # Calculate success rate
        total_processed = self.discovery_metrics["total_onboarded"] + self.discovery_metrics["total_rejected"]
        if total_processed > 0:
            self.discovery_metrics["onboarding_success_rate"] = self.discovery_metrics["total_onboarded"] / total_processed
        
        # Active discoveries
        self.discovery_metrics["active_discoveries"] = len(self.onboarding_pipelines)

    def get_discovery_status(self) -> Dict[str, Any]:
        """Get comprehensive discovery system status"""
        return {
            "discovery_active": self.discovery_active,
            "metrics": self.discovery_metrics,
            "discovered_candidates": len(self.discovered_candidates),
            "active_pipelines": len(self.onboarding_pipelines),
            "onboarded_substrates": len(self.onboarded_substrates),
            "rejected_candidates": len(self.rejected_candidates),
            "active_discovery_methods": list(self.active_discoveries.keys()),
            "pipeline_details": [
                {
                    "pipeline_id": p.pipeline_id,
                    "candidate_id": p.candidate.candidate_id,
                    "current_stage": p.current_stage.value,
                    "progress": p.candidate.onboarding_progress,
                    "estimated_completion": p.estimated_completion_time - (time.time() - p.started_at)
                }
                for p in self.onboarding_pipelines.values()
            ]
        }

    async def shutdown_discovery_system(self):
        """Shutdown the discovery system"""
        self.logger.info("Shutting down substrate discovery system...")
        
        self.discovery_active = False
        
        # Stop discovery threads
        for method, thread in self.discovery_threads.items():
            self.active_discoveries[method] = False
            thread.join(timeout=10)
        
        # Shutdown thread pool
        self.onboarding_thread_pool.shutdown(wait=True)
        
        # Stop coordination thread
        if self.coordination_thread:
            self.coordination_thread.join(timeout=10)
        
        self.logger.info("Substrate discovery system shut down complete")

    # Placeholder implementations for validation, benchmarking, etc.
    def _validate_encryption_support(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"passed": True, "details": "Encryption validation placeholder"}
    
    def _validate_authentication(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"passed": True, "details": "Authentication validation placeholder"}
    
    def _validate_network_isolation(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"passed": True, "details": "Network isolation validation placeholder"}
    
    def _validate_resource_access(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"passed": True, "details": "Resource access validation placeholder"}
    
    def _validate_consciousness_isolation(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"passed": True, "details": "Consciousness isolation validation placeholder"}
    
    def _benchmark_quantum_capabilities(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"overall_score": 0.8, "performance_metrics": {"quantum_coherence": 0.9}}
    
    def _benchmark_gpu_performance(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"overall_score": 0.7, "performance_metrics": {"parallel_efficiency": 0.8}}
    
    def _benchmark_neuromorphic_learning(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"overall_score": 0.6, "performance_metrics": {"learning_rate": 0.7}}
    
    def _benchmark_edge_performance(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"overall_score": 0.5, "performance_metrics": {"latency": 0.6}}
    
    def _benchmark_cpu_performance(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"overall_score": 0.6, "performance_metrics": {"sequential_speed": 0.7}}
    
    def _benchmark_memory_performance(self, candidate: SubstrateCandidate) -> Dict[str, Any]:
        return {"overall_score": 0.7, "performance_metrics": {"memory_bandwidth": 0.8}}


# Example usage
if __name__ == "__main__":
    import asyncio
    
    logging.basicConfig(level=logging.INFO)
    
    # Mock orchestrator for testing
    class MockOrchestrator:
        class MockResourceOrchestrator:
            def register_substrate(self, substrate_id, registration_data):
                print(f"Mock registered substrate: {substrate_id}")
        
        def __init__(self):
            self.resource_orchestrator = self.MockResourceOrchestrator()
    
    async def main():
        mock_orchestrator = MockOrchestrator()
        discovery_system = SubstrateDiscoveryOnboarding(mock_orchestrator)
        
        print("Starting SINCOR Substrate Discovery & Onboarding System...")
        await discovery_system.start_discovery_system()
        
        # Let it run for a bit
        await asyncio.sleep(30)
        
        # Get status
        status = discovery_system.get_discovery_status()
        print(f"\nDiscovery System Status:")
        print(json.dumps(status, indent=2, default=str))
        
        await discovery_system.shutdown_discovery_system()
    
    asyncio.run(main())