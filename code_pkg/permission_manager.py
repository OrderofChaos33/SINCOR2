#!/usr/bin/env python3
"""
SINCOR Permission Management System
Tiered access control for swarm intelligence operations
"""

import sqlite3
import time
import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from enum import Enum
from datetime import datetime, timedelta
import hashlib
import secrets

class PermissionLevel(Enum):
    GOD_MODE = "god_mode"
    ENTERPRISE = "enterprise" 
    PAID_USER = "paid_user"
    FREE_USER = "free_user"
    RESTRICTED = "restricted"

class ResourceType(Enum):
    AGENTS = "agents"
    CPU_HOURS = "cpu_hours"
    MEMORY_GB = "memory_gb"
    API_CALLS = "api_calls"
    STORAGE_GB = "storage_gb"

@dataclass
class ResourceQuota:
    resource_type: ResourceType
    max_limit: float
    current_usage: float
    reset_period_hours: int
    last_reset: float

@dataclass
class UserPermissions:
    user_id: str
    permission_level: PermissionLevel
    api_key: str
    
    # Agent Management
    max_agents: int
    max_scaling_rate: int  # agents per hour
    can_override_swarm: bool
    can_emergency_spawn: bool
    can_access_god_features: bool
    
    # Resource Quotas
    resource_quotas: Dict[ResourceType, ResourceQuota]
    
    # Priority and Weights
    priority_weight: float
    vote_influence: float
    
    # Billing and Limits
    monthly_budget_usd: float
    cost_per_agent_hour: float
    
    # Access Control
    allowed_endpoints: List[str]
    rate_limit_per_minute: int
    
    # Metadata
    created_at: float
    last_active: float
    account_status: str

@dataclass
class UsageMetrics:
    user_id: str
    timestamp: float
    resource_type: ResourceType
    amount_used: float
    cost_usd: float
    action_description: str

class PermissionManager:
    """Advanced permission and quota management system"""
    
    def __init__(self, db_path: str = "permissions.db"):
        self.db_path = db_path
        self.users: Dict[str, UserPermissions] = {}
        self.usage_logs: List[UsageMetrics] = []
        
        self._setup_database()
        self._initialize_default_permissions()
        
    def _setup_database(self):
        """Initialize permission database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # User permissions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id TEXT PRIMARY KEY,
            permission_level TEXT,
            api_key TEXT UNIQUE,
            max_agents INTEGER,
            max_scaling_rate INTEGER,
            can_override_swarm BOOLEAN,
            can_emergency_spawn BOOLEAN,
            can_access_god_features BOOLEAN,
            resource_quotas TEXT,
            priority_weight REAL,
            vote_influence REAL,
            monthly_budget_usd REAL,
            cost_per_agent_hour REAL,
            allowed_endpoints TEXT,
            rate_limit_per_minute INTEGER,
            created_at REAL,
            last_active REAL,
            account_status TEXT
        )
        ''')
        
        # Usage tracking table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS usage_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            timestamp REAL,
            resource_type TEXT,
            amount_used REAL,
            cost_usd REAL,
            action_description TEXT
        )
        ''')
        
        # Rate limiting table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS rate_limits (
            user_id TEXT,
            endpoint TEXT,
            minute_bucket INTEGER,
            request_count INTEGER,
            PRIMARY KEY (user_id, endpoint, minute_bucket)
        )
        ''')
        
        conn.commit()
        conn.close()
        
    def _initialize_default_permissions(self):
        """Set up default permission tiers"""
        
        # GOD MODE - System Owner (Unlimited Everything)
        god_quotas = {
            ResourceType.AGENTS: ResourceQuota(ResourceType.AGENTS, 999999, 0, 24, time.time()),
            ResourceType.CPU_HOURS: ResourceQuota(ResourceType.CPU_HOURS, 999999, 0, 24, time.time()),
            ResourceType.MEMORY_GB: ResourceQuota(ResourceType.MEMORY_GB, 999999, 0, 24, time.time()),
            ResourceType.API_CALLS: ResourceQuota(ResourceType.API_CALLS, 999999, 0, 24, time.time()),
            ResourceType.STORAGE_GB: ResourceQuota(ResourceType.STORAGE_GB, 999999, 0, 24, time.time())
        }
        
        god_permissions = UserPermissions(
            user_id="GOD_USER",
            permission_level=PermissionLevel.GOD_MODE,
            api_key=self._generate_api_key("GOD"),
            max_agents=999999,
            max_scaling_rate=999999,
            can_override_swarm=True,
            can_emergency_spawn=True,
            can_access_god_features=True,
            resource_quotas=god_quotas,
            priority_weight=10.0,
            vote_influence=1.0,
            monthly_budget_usd=999999.0,
            cost_per_agent_hour=0.0,  # Free for god mode
            allowed_endpoints=["*"],  # All endpoints
            rate_limit_per_minute=999999,
            created_at=time.time(),
            last_active=time.time(),
            account_status="ACTIVE"
        )
        
        # ENTERPRISE - High-tier paying customers
        enterprise_quotas = {
            ResourceType.AGENTS: ResourceQuota(ResourceType.AGENTS, 100, 0, 24, time.time()),
            ResourceType.CPU_HOURS: ResourceQuota(ResourceType.CPU_HOURS, 1000, 0, 24, time.time()),
            ResourceType.MEMORY_GB: ResourceQuota(ResourceType.MEMORY_GB, 500, 0, 24, time.time()),
            ResourceType.API_CALLS: ResourceQuota(ResourceType.API_CALLS, 100000, 0, 24, time.time()),
            ResourceType.STORAGE_GB: ResourceQuota(ResourceType.STORAGE_GB, 100, 0, 24, time.time())
        }
        
        enterprise_permissions = UserPermissions(
            user_id="ENTERPRISE_TEMPLATE",
            permission_level=PermissionLevel.ENTERPRISE,
            api_key=self._generate_api_key("ENT"),
            max_agents=100,
            max_scaling_rate=20,
            can_override_swarm=True,  # Enterprise can override
            can_emergency_spawn=True,
            can_access_god_features=False,
            resource_quotas=enterprise_quotas,
            priority_weight=5.0,
            vote_influence=0.8,
            monthly_budget_usd=5000.0,
            cost_per_agent_hour=0.10,
            allowed_endpoints=["*"],  # Most endpoints
            rate_limit_per_minute=1000,
            created_at=time.time(),
            last_active=time.time(),
            account_status="ACTIVE"
        )
        
        # PAID_USER - Standard paying customers
        paid_quotas = {
            ResourceType.AGENTS: ResourceQuota(ResourceType.AGENTS, 25, 0, 24, time.time()),
            ResourceType.CPU_HOURS: ResourceQuota(ResourceType.CPU_HOURS, 200, 0, 24, time.time()),
            ResourceType.MEMORY_GB: ResourceQuota(ResourceType.MEMORY_GB, 50, 0, 24, time.time()),
            ResourceType.API_CALLS: ResourceQuota(ResourceType.API_CALLS, 10000, 0, 24, time.time()),
            ResourceType.STORAGE_GB: ResourceQuota(ResourceType.STORAGE_GB, 10, 0, 24, time.time())
        }
        
        paid_permissions = UserPermissions(
            user_id="PAID_USER_TEMPLATE",
            permission_level=PermissionLevel.PAID_USER,
            api_key=self._generate_api_key("PAID"),
            max_agents=25,
            max_scaling_rate=5,
            can_override_swarm=False,  # Must respect swarm decisions
            can_emergency_spawn=False,
            can_access_god_features=False,
            resource_quotas=paid_quotas,
            priority_weight=2.0,
            vote_influence=0.5,
            monthly_budget_usd=500.0,
            cost_per_agent_hour=0.25,
            allowed_endpoints=["/api/agents/*", "/api/goals/*", "/api/status"],
            rate_limit_per_minute=100,
            created_at=time.time(),
            last_active=time.time(),
            account_status="ACTIVE"
        )
        
        # FREE_USER - Limited free tier
        free_quotas = {
            ResourceType.AGENTS: ResourceQuota(ResourceType.AGENTS, 3, 0, 24, time.time()),
            ResourceType.CPU_HOURS: ResourceQuota(ResourceType.CPU_HOURS, 10, 0, 24, time.time()),
            ResourceType.MEMORY_GB: ResourceQuota(ResourceType.MEMORY_GB, 2, 0, 24, time.time()),
            ResourceType.API_CALLS: ResourceQuota(ResourceType.API_CALLS, 1000, 0, 24, time.time()),
            ResourceType.STORAGE_GB: ResourceQuota(ResourceType.STORAGE_GB, 1, 0, 24, time.time())
        }
        
        free_permissions = UserPermissions(
            user_id="FREE_USER_TEMPLATE",
            permission_level=PermissionLevel.FREE_USER,
            api_key=self._generate_api_key("FREE"),
            max_agents=3,
            max_scaling_rate=1,
            can_override_swarm=False,
            can_emergency_spawn=False,
            can_access_god_features=False,
            resource_quotas=free_quotas,
            priority_weight=0.5,
            vote_influence=0.1,
            monthly_budget_usd=0.0,
            cost_per_agent_hour=0.0,
            allowed_endpoints=["/api/status", "/api/agents/list"],
            rate_limit_per_minute=20,
            created_at=time.time(),
            last_active=time.time(),
            account_status="ACTIVE"
        )
        
        # Store templates
        self.users["GOD_USER"] = god_permissions
        self.users["ENTERPRISE_TEMPLATE"] = enterprise_permissions
        self.users["PAID_USER_TEMPLATE"] = paid_permissions
        self.users["FREE_USER_TEMPLATE"] = free_permissions
        
    def _generate_api_key(self, prefix: str) -> str:
        """Generate secure API key"""
        timestamp = int(time.time())
        random_part = secrets.token_hex(16)
        return f"sk_{prefix}_{timestamp}_{random_part}"
    
    def create_user(self, user_id: str, permission_level: PermissionLevel, 
                   custom_limits: Optional[Dict] = None) -> UserPermissions:
        """Create new user with specified permission level"""
        
        # Get template for permission level
        template_key = f"{permission_level.value.upper()}_TEMPLATE"
        if permission_level == PermissionLevel.GOD_MODE:
            template_key = "GOD_USER"
            
        if template_key not in self.users:
            raise ValueError(f"No template found for permission level: {permission_level}")
            
        template = self.users[template_key]
        
        # Create new user based on template
        new_permissions = UserPermissions(
            user_id=user_id,
            permission_level=permission_level,
            api_key=self._generate_api_key(permission_level.value.upper()[:4]),
            max_agents=template.max_agents,
            max_scaling_rate=template.max_scaling_rate,
            can_override_swarm=template.can_override_swarm,
            can_emergency_spawn=template.can_emergency_spawn,
            can_access_god_features=template.can_access_god_features,
            resource_quotas=template.resource_quotas.copy(),
            priority_weight=template.priority_weight,
            vote_influence=template.vote_influence,
            monthly_budget_usd=template.monthly_budget_usd,
            cost_per_agent_hour=template.cost_per_agent_hour,
            allowed_endpoints=template.allowed_endpoints.copy(),
            rate_limit_per_minute=template.rate_limit_per_minute,
            created_at=time.time(),
            last_active=time.time(),
            account_status="ACTIVE"
        )
        
        # Apply custom limits if provided
        if custom_limits:
            for key, value in custom_limits.items():
                if hasattr(new_permissions, key):
                    setattr(new_permissions, key, value)
                    
        self.users[user_id] = new_permissions
        self._store_user_permissions(new_permissions)
        
        return new_permissions
    
    def check_permission(self, user_id: str, action: str, 
                        resource_type: Optional[ResourceType] = None,
                        amount: float = 1.0) -> Dict[str, Any]:
        """Check if user has permission for an action"""
        
        if user_id not in self.users:
            return {"allowed": False, "reason": "User not found"}
            
        user = self.users[user_id]
        
        # Check account status
        if user.account_status != "ACTIVE":
            return {"allowed": False, "reason": "Account inactive"}
            
        # God mode bypasses all checks
        if user.permission_level == PermissionLevel.GOD_MODE:
            return {"allowed": True, "reason": "God mode access"}
            
        # Check endpoint access
        if action not in user.allowed_endpoints and "*" not in user.allowed_endpoints:
            endpoint_allowed = any(
                endpoint.replace("*", "") in action 
                for endpoint in user.allowed_endpoints 
                if "*" in endpoint
            )
            if not endpoint_allowed:
                return {"allowed": False, "reason": "Endpoint not allowed"}
        
        # Check resource quotas
        if resource_type and resource_type in user.resource_quotas:
            quota = user.resource_quotas[resource_type]
            
            # Reset quota if needed
            if time.time() - quota.last_reset > quota.reset_period_hours * 3600:
                quota.current_usage = 0
                quota.last_reset = time.time()
                
            if quota.current_usage + amount > quota.max_limit:
                return {
                    "allowed": False, 
                    "reason": f"Quota exceeded for {resource_type.value}",
                    "current_usage": quota.current_usage,
                    "max_limit": quota.max_limit,
                    "requested_amount": amount
                }
        
        # Check rate limiting
        if not self._check_rate_limit(user_id, action):
            return {"allowed": False, "reason": "Rate limit exceeded"}
        
        return {"allowed": True, "reason": "Permission granted"}
    
    def consume_resource(self, user_id: str, resource_type: ResourceType, 
                        amount: float, description: str = "") -> bool:
        """Consume user's resource quota"""
        
        permission_check = self.check_permission(user_id, "consume_resource", resource_type, amount)
        
        if not permission_check["allowed"]:
            return False
            
        if user_id in self.users:
            user = self.users[user_id]
            
            if resource_type in user.resource_quotas:
                quota = user.resource_quotas[resource_type]
                quota.current_usage += amount
                
                # Calculate cost
                cost = 0.0
                if resource_type == ResourceType.AGENTS:
                    cost = amount * user.cost_per_agent_hour
                    
                # Log usage
                usage = UsageMetrics(
                    user_id=user_id,
                    timestamp=time.time(),
                    resource_type=resource_type,
                    amount_used=amount,
                    cost_usd=cost,
                    action_description=description
                )
                
                self.usage_logs.append(usage)
                self._store_usage_metrics(usage)
                
                return True
                
        return False
    
    def _check_rate_limit(self, user_id: str, endpoint: str) -> bool:
        """Check if user is within rate limits"""
        
        if user_id not in self.users:
            return False
            
        user = self.users[user_id]
        current_minute = int(time.time() // 60)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get current minute's request count
        cursor.execute('''
        SELECT request_count FROM rate_limits 
        WHERE user_id = ? AND endpoint = ? AND minute_bucket = ?
        ''', (user_id, endpoint, current_minute))
        
        result = cursor.fetchone()
        current_count = result[0] if result else 0
        
        if current_count >= user.rate_limit_per_minute:
            conn.close()
            return False
            
        # Increment counter
        cursor.execute('''
        INSERT OR REPLACE INTO rate_limits (user_id, endpoint, minute_bucket, request_count)
        VALUES (?, ?, ?, ?)
        ''', (user_id, endpoint, current_minute, current_count + 1))
        
        conn.commit()
        conn.close()
        
        return True
    
    def _store_user_permissions(self, permissions: UserPermissions):
        """Store user permissions in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT OR REPLACE INTO user_permissions 
        (user_id, permission_level, api_key, max_agents, max_scaling_rate,
         can_override_swarm, can_emergency_spawn, can_access_god_features,
         resource_quotas, priority_weight, vote_influence, monthly_budget_usd,
         cost_per_agent_hour, allowed_endpoints, rate_limit_per_minute,
         created_at, last_active, account_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            permissions.user_id,
            permissions.permission_level.value,
            permissions.api_key,
            permissions.max_agents,
            permissions.max_scaling_rate,
            permissions.can_override_swarm,
            permissions.can_emergency_spawn,
            permissions.can_access_god_features,
            json.dumps({k.value: asdict(v) for k, v in permissions.resource_quotas.items()}),
            permissions.priority_weight,
            permissions.vote_influence,
            permissions.monthly_budget_usd,
            permissions.cost_per_agent_hour,
            json.dumps(permissions.allowed_endpoints),
            permissions.rate_limit_per_minute,
            permissions.created_at,
            permissions.last_active,
            permissions.account_status
        ))
        
        conn.commit()
        conn.close()
    
    def _store_usage_metrics(self, usage: UsageMetrics):
        """Store usage metrics in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO usage_metrics 
        (user_id, timestamp, resource_type, amount_used, cost_usd, action_description)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            usage.user_id,
            usage.timestamp,
            usage.resource_type.value,
            usage.amount_used,
            usage.cost_usd,
            usage.action_description
        ))
        
        conn.commit()
        conn.close()
    
    def get_user_usage_report(self, user_id: str, hours: int = 24) -> Dict[str, Any]:
        """Generate usage report for a user"""
        
        start_time = time.time() - (hours * 3600)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        SELECT resource_type, SUM(amount_used), SUM(cost_usd), COUNT(*)
        FROM usage_metrics 
        WHERE user_id = ? AND timestamp > ?
        GROUP BY resource_type
        ''', (user_id, start_time))
        
        usage_by_resource = {}
        total_cost = 0.0
        
        for row in cursor.fetchall():
            resource_type, total_amount, total_cost_resource, count = row
            usage_by_resource[resource_type] = {
                "total_amount": total_amount,
                "total_cost": total_cost_resource,
                "operation_count": count
            }
            total_cost += total_cost_resource
            
        conn.close()
        
        user_permissions = self.users.get(user_id)
        
        return {
            "user_id": user_id,
            "permission_level": user_permissions.permission_level.value if user_permissions else "unknown",
            "report_period_hours": hours,
            "usage_by_resource": usage_by_resource,
            "total_cost_usd": total_cost,
            "remaining_budget": user_permissions.monthly_budget_usd - total_cost if user_permissions else 0,
            "current_quotas": {
                resource_type.value: {
                    "current_usage": quota.current_usage,
                    "max_limit": quota.max_limit,
                    "usage_percentage": (quota.current_usage / quota.max_limit) * 100
                }
                for resource_type, quota in user_permissions.resource_quotas.items()
            } if user_permissions else {}
        }
    
    def get_system_permission_summary(self) -> Dict[str, Any]:
        """Get system-wide permission and usage summary"""
        
        user_counts = {}
        total_active_users = 0
        
        for user in self.users.values():
            level = user.permission_level.value
            user_counts[level] = user_counts.get(level, 0) + 1
            if user.account_status == "ACTIVE":
                total_active_users += 1
        
        return {
            "total_users": len(self.users),
            "active_users": total_active_users,
            "users_by_level": user_counts,
            "god_mode_active": any(
                user.permission_level == PermissionLevel.GOD_MODE 
                for user in self.users.values()
            ),
            "total_usage_logs": len(self.usage_logs)
        }

# Global permission manager
permission_manager = PermissionManager()

def require_permission(user_id: str, action: str, resource_type: ResourceType = None, amount: float = 1.0):
    """Decorator-style permission check"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            check_result = permission_manager.check_permission(user_id, action, resource_type, amount)
            if not check_result["allowed"]:
                return {"error": check_result["reason"], "permission_denied": True}
            
            # Consume resource if specified
            if resource_type:
                permission_manager.consume_resource(user_id, resource_type, amount, f"Action: {action}")
            
            return func(*args, **kwargs)
        return wrapper
    return decorator

if __name__ == "__main__":
    print(">> SINCOR Permission Management System")
    print("   God Mode: ENABLED")
    print("   Tiered Access Control: ACTIVE")
    print("   Resource Quotas: ENFORCED")
    
    # Create sample users
    permission_manager.create_user("test_enterprise", PermissionLevel.ENTERPRISE)
    permission_manager.create_user("test_paid", PermissionLevel.PAID_USER)
    permission_manager.create_user("test_free", PermissionLevel.FREE_USER)
    
    print(f"   Created sample users: {len(permission_manager.users)} total")