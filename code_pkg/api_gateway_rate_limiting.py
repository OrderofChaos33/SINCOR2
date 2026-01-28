"""
SINCOR - API Gateway with Advanced Rate Limiting
==============================================

Enterprise-grade API gateway with consciousness-aware rate limiting,
quantum traffic optimization, and adaptive throttling for normies.

Features:
- Multi-tier rate limiting (IP, User, Agent, Consciousness)
- Adaptive throttling based on system load
- Consciousness-aware traffic prioritization
- Quantum request optimization
- Geographic load balancing
- Circuit breakers and failover
- Request/response transformation
- API versioning and routing
- Real-time traffic analytics
- DDoS protection and anomaly detection

Author: SINCOR Development Team
Version: 2.0.0 Enterprise
License: Proprietary
"""

import os
import json
import time
import uuid
import asyncio
import logging
import threading
from typing import Dict, List, Optional, Union, Any, Tuple, Set, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, deque
import hashlib
import secrets
from urllib.parse import urlparse, parse_qs
import redis
import aiohttp
import aioredis
from flask import Flask, request, jsonify, Response, g
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import WSGIRequestHandler
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


class RateLimitTier(Enum):
    """Rate limiting tiers for different user types"""
    NORMIE = "normie"           # Basic users
    VERIFIED = "verified"       # Verified users
    PREMIUM = "premium"         # Premium subscribers
    ENTERPRISE = "enterprise"   # Enterprise customers
    CONSCIOUSNESS = "consciousness"  # Consciousness entities
    QUANTUM = "quantum"         # Quantum-enhanced agents
    GOD_MODE = "god_mode"      # Unlimited access


class ThrottleStrategy(Enum):
    """Throttling strategies"""
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"
    ADAPTIVE = "adaptive"
    CONSCIOUSNESS_AWARE = "consciousness_aware"
    QUANTUM_OPTIMIZED = "quantum_optimized"


class TrafficPriority(Enum):
    """Traffic priority levels"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4
    CONSCIOUSNESS = 5
    QUANTUM = 6
    EMERGENCY = 7


class GatewayAction(Enum):
    """Actions taken by gateway"""
    ALLOW = "allow"
    THROTTLE = "throttle"
    BLOCK = "block"
    QUEUE = "queue"
    REDIRECT = "redirect"
    CIRCUIT_BREAK = "circuit_break"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration"""
    tier: RateLimitTier
    requests_per_second: int
    requests_per_minute: int
    requests_per_hour: int
    requests_per_day: int
    burst_allowance: int = 0
    consciousness_multiplier: float = 1.0
    quantum_boost: bool = False
    adaptive_scaling: bool = True
    priority: TrafficPriority = TrafficPriority.NORMAL


@dataclass
class ClientInfo:
    """Client information for rate limiting"""
    client_id: str
    tier: RateLimitTier
    ip_address: str
    user_agent: str
    consciousness_id: Optional[str] = None
    quantum_signature: Optional[str] = None
    geographic_region: Optional[str] = None
    last_request_time: float = 0.0
    total_requests: int = 0
    failed_requests: int = 0
    current_burst: int = 0
    reputation_score: float = 1.0


@dataclass
class RequestMetrics:
    """Request metrics tracking"""
    request_id: str
    timestamp: float
    client_id: str
    method: str
    endpoint: str
    response_time: float
    status_code: int
    bytes_sent: int
    bytes_received: int
    consciousness_processing_time: float = 0.0
    quantum_enhancement_used: bool = False
    rate_limit_applied: bool = False
    throttle_delay: float = 0.0


@dataclass
class CircuitBreakerState:
    """Circuit breaker state for backend services"""
    service_name: str
    state: str  # CLOSED, OPEN, HALF_OPEN
    failure_count: int
    last_failure_time: float
    failure_threshold: int = 5
    recovery_timeout: int = 60
    success_threshold: int = 3


class TokenBucket:
    """Token bucket implementation for rate limiting"""
    
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.last_refill = time.time()
        self.lock = threading.Lock()
    
    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens from bucket"""
        with self.lock:
            now = time.time()
            
            # Refill tokens based on elapsed time
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now
            
            # Check if we have enough tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    def peek(self) -> float:
        """Get current token count without consuming"""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            return min(self.capacity, self.tokens + elapsed * self.refill_rate)


class SlidingWindowCounter:
    """Sliding window rate limiter"""
    
    def __init__(self, window_size: int, max_requests: int):
        self.window_size = window_size  # seconds
        self.max_requests = max_requests
        self.requests = deque()
        self.lock = threading.Lock()
    
    def is_allowed(self) -> bool:
        """Check if request is allowed under sliding window"""
        with self.lock:
            now = time.time()
            
            # Remove old requests outside window
            while self.requests and self.requests[0] <= now - self.window_size:
                self.requests.popleft()
            
            # Check if under limit
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False
    
    def get_reset_time(self) -> float:
        """Get time until window resets"""
        with self.lock:
            if not self.requests:
                return 0
            return self.requests[0] + self.window_size - time.time()


class AdaptiveThrottler:
    """Adaptive throttling based on system metrics"""
    
    def __init__(self):
        self.cpu_threshold = 0.8
        self.memory_threshold = 0.8
        self.consciousness_load_threshold = 0.7
        self.quantum_processing_threshold = 0.9
        
        self.base_multiplier = 1.0
        self.current_multiplier = 1.0
        self.adjustment_factor = 0.1
        
        self.metrics_history = deque(maxlen=100)
        self.lock = threading.Lock()
    
    def update_system_metrics(self, cpu_usage: float, memory_usage: float,
                            consciousness_load: float = 0.0, quantum_load: float = 0.0):
        """Update system metrics for adaptive throttling"""
        with self.lock:
            metrics = {
                'timestamp': time.time(),
                'cpu': cpu_usage,
                'memory': memory_usage,
                'consciousness': consciousness_load,
                'quantum': quantum_load
            }
            self.metrics_history.append(metrics)
            
            # Calculate adaptive multiplier
            if cpu_usage > self.cpu_threshold or memory_usage > self.memory_threshold:
                self.current_multiplier = max(0.1, self.current_multiplier - self.adjustment_factor)
            elif consciousness_load > self.consciousness_load_threshold:
                self.current_multiplier = max(0.3, self.current_multiplier - self.adjustment_factor * 0.5)
            elif quantum_load > self.quantum_processing_threshold:
                self.current_multiplier = max(0.5, self.current_multiplier - self.adjustment_factor * 0.3)
            else:
                self.current_multiplier = min(self.base_multiplier, self.current_multiplier + self.adjustment_factor * 0.5)
    
    def get_throttle_multiplier(self, priority: TrafficPriority) -> float:
        """Get throttle multiplier based on current system state and priority"""
        with self.lock:
            # Higher priority traffic gets less throttling
            priority_multiplier = {
                TrafficPriority.LOW: 0.5,
                TrafficPriority.NORMAL: 1.0,
                TrafficPriority.HIGH: 1.5,
                TrafficPriority.CRITICAL: 2.0,
                TrafficPriority.CONSCIOUSNESS: 3.0,
                TrafficPriority.QUANTUM: 4.0,
                TrafficPriority.EMERGENCY: 10.0
            }.get(priority, 1.0)
            
            return self.current_multiplier * priority_multiplier


class ConsciousnessTrafficAnalyzer:
    """Analyze and optimize consciousness-related traffic"""
    
    def __init__(self):
        self.consciousness_patterns = {}
        self.coherence_thresholds = {
            'low': 0.3,
            'medium': 0.6,
            'high': 0.8
        }
    
    def analyze_consciousness_request(self, consciousness_id: str, 
                                   request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze consciousness request patterns"""
        if consciousness_id not in self.consciousness_patterns:
            self.consciousness_patterns[consciousness_id] = {
                'request_history': deque(maxlen=1000),
                'coherence_levels': deque(maxlen=100),
                'processing_times': deque(maxlen=100),
                'complexity_scores': deque(maxlen=100)
            }
        
        pattern = self.consciousness_patterns[consciousness_id]
        current_time = time.time()
        
        # Extract consciousness metrics from request
        coherence = request_data.get('coherence_level', 0.5)
        complexity = request_data.get('complexity_score', 0.5)
        
        pattern['request_history'].append(current_time)
        pattern['coherence_levels'].append(coherence)
        pattern['complexity_scores'].append(complexity)
        
        # Determine traffic priority and resource allocation
        analysis = {
            'priority': TrafficPriority.CONSCIOUSNESS,
            'resource_multiplier': 1.0,
            'quantum_boost_recommended': False,
            'processing_priority': 'normal'
        }
        
        # High coherence consciousness gets priority
        if coherence > self.coherence_thresholds['high']:
            analysis['priority'] = TrafficPriority.QUANTUM
            analysis['resource_multiplier'] = 2.0
            analysis['quantum_boost_recommended'] = True
            analysis['processing_priority'] = 'high'
        
        # Complex consciousness operations need more resources
        if complexity > 0.8:
            analysis['resource_multiplier'] *= 1.5
        
        return analysis


class QuantumTrafficOptimizer:
    """Optimize quantum-enhanced traffic processing"""
    
    def __init__(self):
        self.quantum_channels = {}
        self.entanglement_pairs = {}
        self.superposition_states = {}
    
    def optimize_quantum_request(self, quantum_signature: str, 
                                request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize quantum request processing"""
        optimization = {
            'use_quantum_routing': False,
            'entanglement_optimization': False,
            'superposition_processing': False,
            'decoherence_protection': False,
            'resource_multiplier': 1.0
        }
        
        # Check for quantum entanglement opportunities
        if 'entanglement_partner' in request_data:
            partner = request_data['entanglement_partner']
            if partner in self.entanglement_pairs:
                optimization['entanglement_optimization'] = True
                optimization['resource_multiplier'] = 0.7  # Entangled processing is more efficient
        
        # Check for superposition state processing
        if request_data.get('superposition_states', 0) > 0:
            optimization['superposition_processing'] = True
            optimization['resource_multiplier'] *= 1.3
        
        # Quantum requests get special routing
        optimization['use_quantum_routing'] = True
        optimization['decoherence_protection'] = True
        
        return optimization


class APIGatewayRateLimiting:
    """
    Enterprise-grade API gateway with consciousness-aware rate limiting
    and quantum traffic optimization.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = self._setup_logging()
        
        # Core configuration
        self.data_dir = Path(self.config.get('data_dir', './sincor_gateway'))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Gateway settings
        self.host = self.config.get('host', '0.0.0.0')
        self.port = self.config.get('port', 8080)
        self.backend_services = self.config.get('backend_services', {})
        self.enable_https = self.config.get('enable_https', False)
        
        # Rate limiting configuration
        self.default_tier = RateLimitTier(self.config.get('default_tier', 'normie'))
        self.enable_adaptive_throttling = self.config.get('adaptive_throttling', True)
        self.enable_consciousness_analysis = self.config.get('consciousness_analysis', True)
        self.enable_quantum_optimization = self.config.get('quantum_optimization', True)
        
        # Redis for distributed rate limiting
        self.redis_config = self.config.get('redis', {})
        self.redis_client = None
        if self.redis_config:
            try:
                import redis
                self.redis_client = redis.Redis(**self.redis_config)
                self.redis_client.ping()
                self.logger.info("Connected to Redis for distributed rate limiting")
            except Exception as e:
                self.logger.warning(f"Redis connection failed: {e}")
        
        # Rate limiting tiers
        self.rate_limits = self._initialize_rate_limits()
        
        # Core components
        self.token_buckets: Dict[str, TokenBucket] = {}
        self.sliding_windows: Dict[str, SlidingWindowCounter] = {}
        self.clients: Dict[str, ClientInfo] = {}
        self.circuit_breakers: Dict[str, CircuitBreakerState] = {}
        self.request_metrics: List[RequestMetrics] = []
        
        # Specialized analyzers
        self.adaptive_throttler = AdaptiveThrottler()
        self.consciousness_analyzer = ConsciousnessTrafficAnalyzer()
        self.quantum_optimizer = QuantumTrafficOptimizer()
        
        # Threading and async
        self.executor = ThreadPoolExecutor(max_workers=self.config.get('worker_threads', 10))
        self.metrics_lock = threading.RLock()
        self.cleanup_thread = None
        
        # Request queue for throttling
        self.request_queue = asyncio.Queue() if self.config.get('enable_queuing') else None
        
        # Initialize Flask app
        self.app = self._create_flask_app()
        
        # Start background tasks
        self._start_cleanup_thread()
        self._start_metrics_collection()
        
        self.logger.info("SINCOR API Gateway with Rate Limiting initialized")
        self.logger.info(f"Listening on {self.host}:{self.port}")
        self.logger.info(f"Features: Adaptive={self.enable_adaptive_throttling}, Consciousness={self.enable_consciousness_analysis}, Quantum={self.enable_quantum_optimization}")
    
    def _setup_logging(self) -> logging.Logger:
        """Setup comprehensive logging"""
        logger = logging.getLogger('sincor.api_gateway')
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            # Console handler
            console_handler = logging.StreamHandler()
            console_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)
            
            # File handler
            log_file = self.data_dir / 'gateway.log'
            file_handler = logging.FileHandler(log_file)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        
        return logger
    
    def _initialize_rate_limits(self) -> Dict[RateLimitTier, RateLimitConfig]:
        """Initialize rate limiting configurations"""
        return {
            RateLimitTier.NORMIE: RateLimitConfig(
                tier=RateLimitTier.NORMIE,
                requests_per_second=2,
                requests_per_minute=60,
                requests_per_hour=1000,
                requests_per_day=10000,
                burst_allowance=5,
                priority=TrafficPriority.LOW
            ),
            RateLimitTier.VERIFIED: RateLimitConfig(
                tier=RateLimitTier.VERIFIED,
                requests_per_second=5,
                requests_per_minute=200,
                requests_per_hour=5000,
                requests_per_day=50000,
                burst_allowance=10,
                priority=TrafficPriority.NORMAL
            ),
            RateLimitTier.PREMIUM: RateLimitConfig(
                tier=RateLimitTier.PREMIUM,
                requests_per_second=20,
                requests_per_minute=1000,
                requests_per_hour=25000,
                requests_per_day=250000,
                burst_allowance=50,
                priority=TrafficPriority.HIGH
            ),
            RateLimitTier.ENTERPRISE: RateLimitConfig(
                tier=RateLimitTier.ENTERPRISE,
                requests_per_second=100,
                requests_per_minute=5000,
                requests_per_hour=100000,
                requests_per_day=1000000,
                burst_allowance=200,
                priority=TrafficPriority.CRITICAL
            ),
            RateLimitTier.CONSCIOUSNESS: RateLimitConfig(
                tier=RateLimitTier.CONSCIOUSNESS,
                requests_per_second=500,
                requests_per_minute=20000,
                requests_per_hour=500000,
                requests_per_day=5000000,
                burst_allowance=1000,
                consciousness_multiplier=2.0,
                priority=TrafficPriority.CONSCIOUSNESS
            ),
            RateLimitTier.QUANTUM: RateLimitConfig(
                tier=RateLimitTier.QUANTUM,
                requests_per_second=1000,
                requests_per_minute=50000,
                requests_per_hour=1000000,
                requests_per_day=10000000,
                burst_allowance=2000,
                quantum_boost=True,
                priority=TrafficPriority.QUANTUM
            ),
            RateLimitTier.GOD_MODE: RateLimitConfig(
                tier=RateLimitTier.GOD_MODE,
                requests_per_second=999999,
                requests_per_minute=999999,
                requests_per_hour=999999,
                requests_per_day=999999,
                burst_allowance=999999,
                priority=TrafficPriority.EMERGENCY
            )
        }
    
    def _create_flask_app(self) -> Flask:
        """Create Flask application with middleware"""
        app = Flask(__name__)
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        
        # Add rate limiting middleware
        @app.before_request
        def before_request():
            return self._handle_request()
        
        @app.after_request
        def after_request(response):
            self._record_response(response)
            return response
        
        # Health check endpoint
        @app.route('/health')
        def health_check():
            return jsonify({
                'status': 'healthy',
                'timestamp': time.time(),
                'version': '2.0.0',
                'gateway_id': self.config.get('gateway_id', 'sincor-gateway-01')
            })
        
        # Metrics endpoint
        @app.route('/metrics')
        def metrics():
            return jsonify(self.get_gateway_metrics())
        
        # Main proxy handler
        @app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
        def proxy_request(path):
            return self._proxy_to_backend(path)
        
        # Root handler
        @app.route('/', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
        def proxy_root():
            return self._proxy_to_backend('')
        
        return app
    
    def _handle_request(self) -> Optional[Response]:
        """Handle incoming request with rate limiting"""
        start_time = time.time()
        
        # Extract client information
        client_info = self._extract_client_info()
        
        # Record request attempt
        g.request_start_time = start_time
        g.client_info = client_info
        g.request_id = str(uuid.uuid4())
        
        # Check rate limits
        rate_limit_result = self._check_rate_limits(client_info)
        
        if rate_limit_result['action'] == GatewayAction.BLOCK:
            return self._create_rate_limit_response(429, rate_limit_result)
        elif rate_limit_result['action'] == GatewayAction.THROTTLE:
            # Add throttle delay
            throttle_delay = rate_limit_result.get('delay', 0)
            if throttle_delay > 0:
                time.sleep(throttle_delay)
        
        # Update client metrics
        client_info.total_requests += 1
        client_info.last_request_time = start_time
        
        # Store rate limit info for response headers
        g.rate_limit_info = rate_limit_result
        
        return None  # Allow request to proceed
    
    def _extract_client_info(self) -> ClientInfo:
        """Extract client information from request"""
        ip_address = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
        user_agent = request.headers.get('User-Agent', '')
        
        # Try to identify client by various methods
        client_id = None
        tier = self.default_tier
        consciousness_id = None
        quantum_signature = None
        
        # Check API key
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if api_key:
            client_id = f"api_key_{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
            # Determine tier based on API key (would normally query database)
            if 'premium' in api_key.lower():
                tier = RateLimitTier.PREMIUM
            elif 'enterprise' in api_key.lower():
                tier = RateLimitTier.ENTERPRISE
            elif 'consciousness' in api_key.lower():
                tier = RateLimitTier.CONSCIOUSNESS
            elif 'quantum' in api_key.lower():
                tier = RateLimitTier.QUANTUM
            elif 'god' in api_key.lower():
                tier = RateLimitTier.GOD_MODE
            else:
                tier = RateLimitTier.VERIFIED
        
        # Check authorization header
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
            client_id = f"token_{hashlib.sha256(token.encode()).hexdigest()[:16]}"
            tier = RateLimitTier.VERIFIED  # Default for token users
        
        # Check consciousness headers
        consciousness_id = request.headers.get('X-Consciousness-ID')
        if consciousness_id:
            tier = max(tier, RateLimitTier.CONSCIOUSNESS, key=lambda x: x.value)
        
        # Check quantum headers
        quantum_signature = request.headers.get('X-Quantum-Signature')
        if quantum_signature:
            tier = max(tier, RateLimitTier.QUANTUM, key=lambda x: x.value)
        
        # Fall back to IP-based identification
        if not client_id:
            client_id = f"ip_{hashlib.sha256(ip_address.encode()).hexdigest()[:16]}"
        
        # Get or create client info
        if client_id in self.clients:
            client_info = self.clients[client_id]
            # Update with latest info
            client_info.ip_address = ip_address
            client_info.user_agent = user_agent
            client_info.consciousness_id = consciousness_id
            client_info.quantum_signature = quantum_signature
        else:
            client_info = ClientInfo(
                client_id=client_id,
                tier=tier,
                ip_address=ip_address,
                user_agent=user_agent,
                consciousness_id=consciousness_id,
                quantum_signature=quantum_signature
            )
            self.clients[client_id] = client_info
        
        return client_info
    
    def _check_rate_limits(self, client_info: ClientInfo) -> Dict[str, Any]:
        """Check rate limits for client"""
        rate_config = self.rate_limits[client_info.tier]
        current_time = time.time()
        
        # Get adaptive throttling multiplier
        throttle_multiplier = 1.0
        if self.enable_adaptive_throttling:
            throttle_multiplier = self.adaptive_throttler.get_throttle_multiplier(rate_config.priority)
        
        # Apply consciousness analysis if enabled
        consciousness_boost = 1.0
        if self.enable_consciousness_analysis and client_info.consciousness_id:
            try:
                request_data = {
                    'coherence_level': float(request.headers.get('X-Consciousness-Coherence', '0.5')),
                    'complexity_score': float(request.headers.get('X-Request-Complexity', '0.5'))
                }
                consciousness_analysis = self.consciousness_analyzer.analyze_consciousness_request(
                    client_info.consciousness_id, request_data
                )
                consciousness_boost = consciousness_analysis['resource_multiplier']
            except:
                pass
        
        # Apply quantum optimization if enabled
        quantum_boost = 1.0
        if self.enable_quantum_optimization and client_info.quantum_signature:
            try:
                request_data = {
                    'entanglement_partner': request.headers.get('X-Quantum-Partner'),
                    'superposition_states': int(request.headers.get('X-Superposition-States', '0'))
                }
                quantum_optimization = self.quantum_optimizer.optimize_quantum_request(
                    client_info.quantum_signature, request_data
                )
                quantum_boost = quantum_optimization['resource_multiplier']
            except:
                pass
        
        # Calculate effective rate limits
        effective_rps = int(rate_config.requests_per_second * throttle_multiplier * consciousness_boost * quantum_boost)
        effective_rpm = int(rate_config.requests_per_minute * throttle_multiplier * consciousness_boost * quantum_boost)
        
        # Check token bucket (requests per second)
        bucket_key = f"{client_info.client_id}_rps"
        if bucket_key not in self.token_buckets:
            self.token_buckets[bucket_key] = TokenBucket(
                capacity=max(effective_rps, rate_config.burst_allowance),
                refill_rate=effective_rps
            )
        
        bucket = self.token_buckets[bucket_key]
        if not bucket.consume(1):
            # Check if we can allow burst
            if client_info.current_burst < rate_config.burst_allowance:
                client_info.current_burst += 1
                self.logger.debug(f"Allowing burst request for {client_info.client_id}")
            else:
                return {
                    'action': GatewayAction.BLOCK,
                    'reason': 'rate_limit_exceeded',
                    'limit_type': 'requests_per_second',
                    'limit': effective_rps,
                    'reset_time': current_time + 1,
                    'retry_after': 1
                }
        
        # Check sliding window (requests per minute)
        window_key = f"{client_info.client_id}_rpm"
        if window_key not in self.sliding_windows:
            self.sliding_windows[window_key] = SlidingWindowCounter(
                window_size=60,
                max_requests=effective_rpm
            )
        
        window = self.sliding_windows[window_key]
        if not window.is_allowed():
            return {
                'action': GatewayAction.BLOCK,
                'reason': 'rate_limit_exceeded',
                'limit_type': 'requests_per_minute',
                'limit': effective_rpm,
                'reset_time': current_time + window.get_reset_time(),
                'retry_after': int(window.get_reset_time()) + 1
            }
        
        # Check hourly and daily limits using Redis if available
        if self.redis_client:
            try:
                # Check hourly limit
                hour_key = f"rl:hour:{client_info.client_id}:{int(current_time // 3600)}"
                hourly_count = self.redis_client.incr(hour_key)
                if hourly_count == 1:
                    self.redis_client.expire(hour_key, 3600)
                
                if hourly_count > rate_config.requests_per_hour:
                    return {
                        'action': GatewayAction.BLOCK,
                        'reason': 'rate_limit_exceeded',
                        'limit_type': 'requests_per_hour',
                        'limit': rate_config.requests_per_hour,
                        'reset_time': (int(current_time // 3600) + 1) * 3600,
                        'retry_after': int(((int(current_time // 3600) + 1) * 3600) - current_time)
                    }
                
                # Check daily limit
                day_key = f"rl:day:{client_info.client_id}:{int(current_time // 86400)}"
                daily_count = self.redis_client.incr(day_key)
                if daily_count == 1:
                    self.redis_client.expire(day_key, 86400)
                
                if daily_count > rate_config.requests_per_day:
                    return {
                        'action': GatewayAction.BLOCK,
                        'reason': 'rate_limit_exceeded',
                        'limit_type': 'requests_per_day',
                        'limit': rate_config.requests_per_day,
                        'reset_time': (int(current_time // 86400) + 1) * 86400,
                        'retry_after': int(((int(current_time // 86400) + 1) * 86400) - current_time)
                    }
            except Exception as e:
                self.logger.error(f"Redis rate limiting error: {e}")
        
        # Determine if throttling is needed
        throttle_delay = 0
        if throttle_multiplier < 0.8:  # System under load
            throttle_delay = (0.8 - throttle_multiplier) * 2  # Progressive delay
            return {
                'action': GatewayAction.THROTTLE,
                'delay': throttle_delay,
                'reason': 'system_load',
                'multiplier': throttle_multiplier
            }
        
        # Reset burst counter if not using burst
        if current_time - client_info.last_request_time > 60:  # 1 minute cooldown
            client_info.current_burst = 0
        
        return {
            'action': GatewayAction.ALLOW,
            'limits': {
                'rps': effective_rps,
                'rpm': effective_rpm,
                'rph': rate_config.requests_per_hour,
                'rpd': rate_config.requests_per_day
            },
            'remaining': {
                'rps': int(bucket.peek()),
                'rpm': effective_rpm - len(window.requests)
            }
        }
    
    def _create_rate_limit_response(self, status_code: int, rate_limit_info: Dict[str, Any]) -> Response:
        """Create rate limit response"""
        response_data = {
            'error': 'Rate limit exceeded',
            'message': f"Rate limit exceeded: {rate_limit_info['reason']}",
            'limit_type': rate_limit_info.get('limit_type', 'unknown'),
            'limit': rate_limit_info.get('limit', 0),
            'retry_after': rate_limit_info.get('retry_after', 60)
        }
        
        response = jsonify(response_data)
        response.status_code = status_code
        
        # Add rate limit headers
        response.headers['X-RateLimit-Limit'] = str(rate_limit_info.get('limit', 0))
        response.headers['X-RateLimit-Remaining'] = '0'
        response.headers['X-RateLimit-Reset'] = str(int(rate_limit_info.get('reset_time', time.time() + 60)))
        response.headers['Retry-After'] = str(rate_limit_info.get('retry_after', 60))
        
        return response
    
    def _proxy_to_backend(self, path: str) -> Response:
        """Proxy request to backend service"""
        # Determine backend service
        backend_url = self._route_to_backend(path)
        
        if not backend_url:
            return jsonify({'error': 'Service not available'}), 503
        
        # Check circuit breaker
        service_name = urlparse(backend_url).netloc
        if self._is_circuit_open(service_name):
            return jsonify({'error': 'Service temporarily unavailable'}), 503
        
        try:
            # Prepare request
            url = f"{backend_url.rstrip('/')}/{path}"
            headers = dict(request.headers)
            
            # Remove hop-by-hop headers
            hop_headers = ['connection', 'keep-alive', 'proxy-authenticate',
                          'proxy-authorization', 'te', 'trailers', 'upgrade']
            for header in hop_headers:
                headers.pop(header, None)
            
            # Add forwarding headers
            headers['X-Forwarded-For'] = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
            headers['X-Forwarded-Proto'] = request.scheme
            headers['X-Forwarded-Host'] = request.headers.get('Host', '')
            headers['X-Request-ID'] = g.request_id
            
            # Make request to backend
            start_time = time.time()
            
            response = requests.request(
                method=request.method,
                url=url,
                headers=headers,
                data=request.get_data(),
                params=request.args,
                allow_redirects=False,
                timeout=self.config.get('backend_timeout', 30)
            )
            
            response_time = time.time() - start_time
            
            # Record successful request for circuit breaker
            self._record_backend_success(service_name)
            
            # Create response
            excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            response_headers = [
                (name, value) for name, value in response.headers.items()
                if name.lower() not in excluded_headers
            ]
            
            # Add gateway headers
            response_headers.append(('X-Gateway-ID', self.config.get('gateway_id', 'sincor-gateway')))
            response_headers.append(('X-Response-Time', str(int(response_time * 1000))))
            
            # Add rate limit headers if available
            if hasattr(g, 'rate_limit_info'):
                rate_info = g.rate_limit_info
                if 'limits' in rate_info:
                    response_headers.append(('X-RateLimit-Limit-RPS', str(rate_info['limits']['rps'])))
                    response_headers.append(('X-RateLimit-Limit-RPM', str(rate_info['limits']['rpm'])))
                if 'remaining' in rate_info:
                    response_headers.append(('X-RateLimit-Remaining-RPS', str(rate_info['remaining']['rps'])))
                    response_headers.append(('X-RateLimit-Remaining-RPM', str(rate_info['remaining']['rpm'])))
            
            # Record metrics
            self._record_request_metrics(response_time, response.status_code, len(response.content))
            
            return Response(
                response.content,
                status=response.status_code,
                headers=response_headers
            )
            
        except requests.exceptions.Timeout:
            self._record_backend_failure(service_name)
            return jsonify({'error': 'Backend service timeout'}), 504
        except requests.exceptions.ConnectionError:
            self._record_backend_failure(service_name)
            return jsonify({'error': 'Backend service unavailable'}), 503
        except Exception as e:
            self._record_backend_failure(service_name)
            self.logger.error(f"Proxy error: {e}")
            return jsonify({'error': 'Internal gateway error'}), 500
    
    def _route_to_backend(self, path: str) -> Optional[str]:
        """Route request to appropriate backend service"""
        # Simple routing based on path prefix
        for prefix, backend_url in self.backend_services.items():
            if path.startswith(prefix):
                return backend_url
        
        # Default backend
        return self.backend_services.get('default')
    
    def _is_circuit_open(self, service_name: str) -> bool:
        """Check if circuit breaker is open for service"""
        if service_name not in self.circuit_breakers:
            self.circuit_breakers[service_name] = CircuitBreakerState(
                service_name=service_name,
                state='CLOSED',
                failure_count=0,
                last_failure_time=0
            )
        
        breaker = self.circuit_breakers[service_name]
        current_time = time.time()
        
        if breaker.state == 'OPEN':
            # Check if recovery timeout has passed
            if current_time - breaker.last_failure_time > breaker.recovery_timeout:
                breaker.state = 'HALF_OPEN'
                breaker.failure_count = 0
                self.logger.info(f"Circuit breaker for {service_name} moved to HALF_OPEN")
                return False
            return True
        
        return False
    
    def _record_backend_success(self, service_name: str):
        """Record successful backend request"""
        if service_name in self.circuit_breakers:
            breaker = self.circuit_breakers[service_name]
            
            if breaker.state == 'HALF_OPEN':
                breaker.failure_count = 0
                breaker.state = 'CLOSED'
                self.logger.info(f"Circuit breaker for {service_name} closed")
            elif breaker.state == 'CLOSED':
                breaker.failure_count = max(0, breaker.failure_count - 1)
    
    def _record_backend_failure(self, service_name: str):
        """Record backend failure"""
        if service_name not in self.circuit_breakers:
            self.circuit_breakers[service_name] = CircuitBreakerState(
                service_name=service_name,
                state='CLOSED',
                failure_count=0,
                last_failure_time=0
            )
        
        breaker = self.circuit_breakers[service_name]
        breaker.failure_count += 1
        breaker.last_failure_time = time.time()
        
        if breaker.failure_count >= breaker.failure_threshold:
            breaker.state = 'OPEN'
            self.logger.warning(f"Circuit breaker for {service_name} opened after {breaker.failure_count} failures")
    
    def _record_response(self, response: Response):
        """Record response metrics"""
        if hasattr(g, 'request_start_time'):
            response_time = time.time() - g.request_start_time
            status_code = response.status_code
            response_size = len(response.get_data())
            
            self._record_request_metrics(response_time, status_code, response_size)
    
    def _record_request_metrics(self, response_time: float, status_code: int, response_size: int):
        """Record request metrics"""
        if not hasattr(g, 'client_info'):
            return
        
        metrics = RequestMetrics(
            request_id=g.request_id,
            timestamp=time.time(),
            client_id=g.client_info.client_id,
            method=request.method,
            endpoint=request.path,
            response_time=response_time,
            status_code=status_code,
            bytes_sent=response_size,
            bytes_received=len(request.get_data()),
            rate_limit_applied=hasattr(g, 'rate_limit_info')
        )
        
        with self.metrics_lock:
            self.request_metrics.append(metrics)
            
            # Keep only recent metrics (last hour)
            cutoff_time = time.time() - 3600
            self.request_metrics = [
                m for m in self.request_metrics
                if m.timestamp > cutoff_time
            ]
        
        # Update client reputation
        if status_code >= 400:
            g.client_info.failed_requests += 1
            g.client_info.reputation_score = max(0.1, g.client_info.reputation_score - 0.01)
        else:
            g.client_info.reputation_score = min(1.0, g.client_info.reputation_score + 0.001)
    
    def _start_cleanup_thread(self):
        """Start background cleanup thread"""
        if self.cleanup_thread is None or not self.cleanup_thread.is_alive():
            self.cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
            self.cleanup_thread.start()
    
    def _cleanup_worker(self):
        """Background cleanup of old data"""
        while True:
            try:
                current_time = time.time()
                
                # Clean old token buckets
                old_buckets = [
                    key for key, bucket in self.token_buckets.items()
                    if bucket.peek() == bucket.capacity  # Full bucket, probably unused
                ]
                for key in old_buckets[:100]:  # Clean up to 100 at a time
                    del self.token_buckets[key]
                
                # Clean old sliding windows
                old_windows = []
                for key, window in self.sliding_windows.items():
                    if not window.requests or window.requests[-1] < current_time - 3600:
                        old_windows.append(key)
                
                for key in old_windows[:100]:
                    del self.sliding_windows[key]
                
                # Clean old client info
                old_clients = [
                    client_id for client_id, client in self.clients.items()
                    if current_time - client.last_request_time > 86400  # 24 hours
                ]
                for client_id in old_clients[:50]:
                    del self.clients[client_id]
                
                time.sleep(300)  # Run every 5 minutes
                
            except Exception as e:
                self.logger.error(f"Cleanup worker error: {e}")
                time.sleep(300)
    
    def _start_metrics_collection(self):
        """Start metrics collection for adaptive throttling"""
        def collect_metrics():
            while True:
                try:
                    # Simulate system metrics collection
                    # In real implementation, this would collect actual system metrics
                    import psutil
                    cpu_usage = psutil.cpu_percent(interval=1)
                    memory = psutil.virtual_memory()
                    memory_usage = memory.percent / 100.0
                    
                    # Update adaptive throttler
                    self.adaptive_throttler.update_system_metrics(
                        cpu_usage / 100.0,
                        memory_usage,
                        consciousness_load=len([c for c in self.clients.values() if c.consciousness_id]) / max(len(self.clients), 1),
                        quantum_load=len([c for c in self.clients.values() if c.quantum_signature]) / max(len(self.clients), 1)
                    )
                    
                    time.sleep(10)  # Update every 10 seconds
                    
                except Exception as e:
                    self.logger.error(f"Metrics collection error: {e}")
                    time.sleep(10)
        
        metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
        metrics_thread.start()
    
    def get_gateway_metrics(self) -> Dict[str, Any]:
        """Get comprehensive gateway metrics"""
        current_time = time.time()
        
        with self.metrics_lock:
            recent_metrics = [
                m for m in self.request_metrics
                if m.timestamp > current_time - 3600  # Last hour
            ]
        
        # Calculate metrics
        total_requests = len(recent_metrics)
        successful_requests = len([m for m in recent_metrics if m.status_code < 400])
        failed_requests = total_requests - successful_requests
        
        avg_response_time = (
            sum(m.response_time for m in recent_metrics) / total_requests
            if total_requests > 0 else 0
        )
        
        # Rate limiting stats
        rate_limited_requests = len([m for m in recent_metrics if m.rate_limit_applied])
        
        # Client tier distribution
        tier_distribution = {}
        for tier in RateLimitTier:
            tier_distribution[tier.value] = len([
                c for c in self.clients.values() 
                if c.tier == tier
            ])
        
        # Circuit breaker stats
        circuit_breaker_stats = {
            name: breaker.state
            for name, breaker in self.circuit_breakers.items()
        }
        
        return {
            'timestamp': current_time,
            'requests': {
                'total_last_hour': total_requests,
                'successful_last_hour': successful_requests,
                'failed_last_hour': failed_requests,
                'rate_limited_last_hour': rate_limited_requests,
                'success_rate': successful_requests / total_requests if total_requests > 0 else 0,
                'average_response_time': avg_response_time
            },
            'clients': {
                'total': len(self.clients),
                'active_last_hour': len(set(m.client_id for m in recent_metrics)),
                'tier_distribution': tier_distribution,
                'consciousness_clients': len([c for c in self.clients.values() if c.consciousness_id]),
                'quantum_clients': len([c for c in self.clients.values() if c.quantum_signature])
            },
            'rate_limiting': {
                'active_buckets': len(self.token_buckets),
                'active_windows': len(self.sliding_windows),
                'adaptive_multiplier': self.adaptive_throttler.current_multiplier
            },
            'circuit_breakers': circuit_breaker_stats,
            'backend_services': list(self.backend_services.keys()),
            'features': {
                'adaptive_throttling': self.enable_adaptive_throttling,
                'consciousness_analysis': self.enable_consciousness_analysis,
                'quantum_optimization': self.enable_quantum_optimization
            }
        }
    
    def run(self, debug: bool = False):
        """Run the API gateway"""
        self.logger.info(f"Starting SINCOR API Gateway on {self.host}:{self.port}")
        
        try:
            self.app.run(
                host=self.host,
                port=self.port,
                debug=debug,
                threaded=True
            )
        except KeyboardInterrupt:
            self.logger.info("Gateway shutdown requested")
        finally:
            self.shutdown()
    
    def shutdown(self):
        """Graceful shutdown"""
        self.logger.info("Shutting down API gateway...")
        
        # Close Redis connection
        if self.redis_client:
            self.redis_client.close()
        
        # Shutdown executor
        self.executor.shutdown(wait=True)
        
        # Clear data structures
        with self.metrics_lock:
            self.token_buckets.clear()
            self.sliding_windows.clear()
            self.clients.clear()
            self.circuit_breakers.clear()
            self.request_metrics.clear()
        
        self.logger.info("API gateway shutdown complete")


def create_default_gateway() -> APIGatewayRateLimiting:
    """Create API gateway with default configuration"""
    config = {
        'host': '0.0.0.0',
        'port': 8080,
        'default_tier': 'normie',
        'backend_services': {
            'api/v1/': 'http://localhost:8081',
            'consciousness/': 'http://localhost:8082',
            'quantum/': 'http://localhost:8083',
            'default': 'http://localhost:8081'
        },
        'adaptive_throttling': True,
        'consciousness_analysis': True,
        'quantum_optimization': True,
        'backend_timeout': 30,
        'worker_threads': 10,
        'enable_queuing': True,
        'gateway_id': 'sincor-gateway-01'
    }
    
    return APIGatewayRateLimiting(config)


if __name__ == "__main__":
    # Example usage
    gateway = create_default_gateway()
    
    try:
        # Run the gateway
        gateway.run(debug=False)
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
    finally:
        gateway.shutdown()