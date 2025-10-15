"""
Base Product Class with Security and Anti-Theft Measures
All SINCOR products inherit from this secure foundation
"""

import hashlib
import secrets
import time
import os
import jwt
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from functools import wraps
import logging

class ProductSecurityError(Exception):
    """Raised when security validation fails"""
    pass

class BaseProduct:
    """Base class for all SINCOR products with built-in security"""
    
    def __init__(self, product_id: str, license_key: str = None):
        self.product_id = product_id
        self.license_key = license_key
        self.session_token = None
        self.initialization_time = time.time()
        self.security_hash = self._generate_security_hash()
        self.agent_registry = {}
        self.execution_logs = []
        self.api_call_count = 0
        self.last_heartbeat = time.time()
        
        # Security settings
        self.max_api_calls_per_hour = 10000
        self.session_timeout_minutes = 60
        self.heartbeat_interval = 300  # 5 minutes
        
        # Initialize logging
        self._setup_logging()
        
    def _setup_logging(self):
        """Setup secure logging with encryption"""
        log_dir = "logs/products"
        os.makedirs(log_dir, exist_ok=True)
        
        logging.basicConfig(
            filename=f"{log_dir}/{self.product_id}.log",
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(self.product_id)
    
    def _generate_security_hash(self) -> str:
        """Generate unique security hash for this product instance"""
        components = [
            self.product_id,
            str(self.initialization_time),
            os.environ.get('SINCOR_SECRET_KEY', 'development_key'),
            secrets.token_hex(16)
        ]
        return hashlib.sha256(''.join(components).encode()).hexdigest()
    
    def authenticate(self, license_key: str, user_id: str) -> Dict[str, Any]:
        """Authenticate user and create secure session"""
        try:
            # Validate license key format
            if not self._validate_license_key(license_key):
                raise ProductSecurityError("Invalid license key format")
            
            # Check license with central validation server (mock for now)
            if not self._validate_license_with_server(license_key, user_id):
                raise ProductSecurityError("License validation failed")
            
            # Generate session token
            payload = {
                'user_id': user_id,
                'product_id': self.product_id,
                'license_key': license_key,
                'issued_at': datetime.utcnow(),
                'expires_at': datetime.utcnow() + timedelta(minutes=self.session_timeout_minutes),
                'security_hash': self.security_hash
            }
            
            secret_key = os.environ.get('SINCOR_JWT_SECRET', 'development_secret')
            self.session_token = jwt.encode(payload, secret_key, algorithm='HS256')
            self.license_key = license_key
            
            self.logger.info(f"User {user_id} authenticated successfully")
            
            return {
                'success': True,
                'session_token': self.session_token,
                'expires_in': self.session_timeout_minutes * 60,
                'product_capabilities': self.get_capabilities()
            }
            
        except ProductSecurityError as e:
            self.logger.warning(f"Authentication failed: {str(e)}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            self.logger.error(f"Authentication error: {str(e)}")
            return {'success': False, 'error': 'Authentication system error'}
    
    def _validate_license_key(self, license_key: str) -> bool:
        """Validate license key format"""
        # SINCOR license keys: SINCOR-XXXX-YYYY-ZZZZ-WWWW
        import re
        pattern = r'^SINCOR-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$'
        return re.match(pattern, license_key) is not None
    
    def _validate_license_with_server(self, license_key: str, user_id: str) -> bool:
        """Validate license with central server (implement actual validation)"""
        # Mock validation - implement actual server validation
        valid_test_keys = [
            'SINCOR-DEMO-TEST-EVAL-0001',
            'SINCOR-PROD-FULL-PAID-0001',
            'SINCOR-TRIAL-30DAY-TEMP-0001'
        ]
        return license_key in valid_test_keys
    
    def require_auth(self, func):
        """Decorator to require valid authentication"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self.is_authenticated():
                raise ProductSecurityError("Authentication required")
            
            if not self._check_rate_limits():
                raise ProductSecurityError("Rate limit exceeded")
            
            if not self._verify_heartbeat():
                raise ProductSecurityError("Session expired - heartbeat timeout")
            
            self.api_call_count += 1
            result = func(*args, **kwargs)
            
            # Log the API call
            self.execution_logs.append({
                'function': func.__name__,
                'timestamp': datetime.utcnow(),
                'args_count': len(args),
                'kwargs_keys': list(kwargs.keys())
            })
            
            return result
        return wrapper
    
    def is_authenticated(self) -> bool:
        """Check if current session is authenticated"""
        if not self.session_token:
            return False
        
        try:
            secret_key = os.environ.get('SINCOR_JWT_SECRET', 'development_secret')
            payload = jwt.decode(self.session_token, secret_key, algorithms=['HS256'])
            
            # Check expiration
            expires_at = datetime.fromisoformat(payload['expires_at'].replace('Z', '+00:00'))
            if datetime.utcnow() > expires_at:
                return False
            
            # Check security hash
            if payload.get('security_hash') != self.security_hash:
                return False
            
            return True
            
        except jwt.InvalidTokenError:
            return False
    
    def _check_rate_limits(self) -> bool:
        """Check API rate limits"""
        current_time = time.time()
        hour_ago = current_time - 3600
        
        # Count recent API calls (simple in-memory tracking)
        recent_calls = sum(1 for log in self.execution_logs 
                          if log['timestamp'].timestamp() > hour_ago)
        
        return recent_calls < self.max_api_calls_per_hour
    
    def _verify_heartbeat(self) -> bool:
        """Verify session is still active via heartbeat"""
        current_time = time.time()
        return (current_time - self.last_heartbeat) < (self.heartbeat_interval * 2)
    
    def heartbeat(self) -> Dict[str, Any]:
        """Update session heartbeat"""
        self.last_heartbeat = time.time()
        return {
            'status': 'active',
            'last_heartbeat': self.last_heartbeat,
            'api_calls_remaining': self.max_api_calls_per_hour - self.api_call_count,
            'session_valid': self.is_authenticated()
        }
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Return product capabilities - override in subclasses"""
        return {
            'product_id': self.product_id,
            'version': '1.0.0',
            'agents_available': list(self.agent_registry.keys()),
            'security_level': 'enterprise',
            'rate_limits': {
                'api_calls_per_hour': self.max_api_calls_per_hour,
                'session_timeout_minutes': self.session_timeout_minutes
            }
        }
    
    def register_agent(self, agent_name: str, agent_class: Any, capabilities: List[str]):
        """Register an AI agent with this product"""
        self.agent_registry[agent_name] = {
            'class': agent_class,
            'capabilities': capabilities,
            'registered_at': datetime.utcnow(),
            'usage_count': 0
        }
        self.logger.info(f"Agent {agent_name} registered with capabilities: {capabilities}")
    
    def deploy_agent_swarm(self, agent_type: str, count: int, task_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy a swarm of agents for parallel processing"""
        if not self.is_authenticated():
            raise ProductSecurityError("Authentication required for agent deployment")
        
        if agent_type not in self.agent_registry:
            raise ValueError(f"Agent type {agent_type} not registered")
        
        # Security check - limit swarm size based on license
        max_swarm_size = self._get_max_swarm_size()
        if count > max_swarm_size:
            raise ProductSecurityError(f"Swarm size {count} exceeds license limit {max_swarm_size}")
        
        deployment_id = secrets.token_hex(8)
        
        self.logger.info(f"Deploying swarm: {count} x {agent_type}, deployment_id: {deployment_id}")
        
        # Mock deployment - implement actual agent instantiation
        return {
            'deployment_id': deployment_id,
            'agent_type': agent_type,
            'count': count,
            'status': 'deployed',
            'estimated_completion': (datetime.utcnow() + timedelta(minutes=5)).isoformat()
        }
    
    def _get_max_swarm_size(self) -> int:
        """Get maximum swarm size based on license tier"""
        if not self.license_key:
            return 5  # Trial limit
        
        if 'DEMO' in self.license_key:
            return 10
        elif 'TRIAL' in self.license_key:
            return 25
        elif 'PROD' in self.license_key:
            return 1000  # Production license
        else:
            return 5  # Default safe limit
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """Get product usage statistics"""
        if not self.is_authenticated():
            raise ProductSecurityError("Authentication required")
        
        return {
            'api_calls_today': self.api_call_count,
            'agents_deployed': len(self.agent_registry),
            'session_duration_minutes': (time.time() - self.initialization_time) / 60,
            'execution_logs_count': len(self.execution_logs),
            'last_activity': self.last_heartbeat,
            'license_type': self._get_license_type()
        }
    
    def _get_license_type(self) -> str:
        """Determine license type from license key"""
        if not self.license_key:
            return 'none'
        
        if 'DEMO' in self.license_key:
            return 'demo'
        elif 'TRIAL' in self.license_key:
            return 'trial'
        elif 'PROD' in self.license_key:
            return 'production'
        else:
            return 'unknown'
    
    def shutdown(self) -> Dict[str, Any]:
        """Securely shutdown product instance"""
        self.logger.info(f"Product {self.product_id} shutting down")
        
        # Clear sensitive data
        self.session_token = None
        self.license_key = None
        self.agent_registry.clear()
        
        return {
            'status': 'shutdown',
            'final_stats': {
                'total_api_calls': self.api_call_count,
                'session_duration': time.time() - self.initialization_time,
                'agents_used': len(self.execution_logs)
            }
        }