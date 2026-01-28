"""
Environment-Specific Configuration Management for SINCOR
Advanced configuration management system with environment isolation,
secret management, dynamic reloading, and consciousness-aware configurations
"""

import os
import json
import yaml
import toml
import configparser
from typing import Dict, Any, List, Optional, Union, Type
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
import logging
import hashlib
import time
import threading
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import getpass
import socket

class Environment(Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"
    LOCAL = "local"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    GOD_MODE = "god_mode"

class ConfigFormat(Enum):
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    INI = "ini"
    ENV = "env"

@dataclass
class ConfigurationSource:
    source_id: str
    name: str
    format: ConfigFormat
    path: str
    required: bool = True
    encrypted: bool = False
    watch_for_changes: bool = True
    priority: int = 100  # Lower number = higher priority
    environment_specific: bool = True

@dataclass
class SecretConfiguration:
    key: str
    value: str
    encrypted: bool = True
    environments: List[Environment] = None
    expires_at: Optional[float] = None
    rotation_required: bool = False

@dataclass
class ConfigurationSchema:
    section: str
    key: str
    data_type: Type
    required: bool = True
    default_value: Any = None
    validation_rules: List[str] = None
    environment_overrides: Dict[Environment, Any] = None
    consciousness_specific: bool = False
    description: str = ""

class EnvironmentConfigurationManager:
    def __init__(self, base_config_dir: str = "./config", environment: Optional[Environment] = None):
        self.logger = logging.getLogger(__name__)
        self.base_config_dir = Path(base_config_dir)
        self.environment = environment or self._detect_environment()
        
        # Configuration state
        self.configuration: Dict[str, Any] = {}
        self.secrets: Dict[str, SecretConfiguration] = {}
        self.configuration_sources: Dict[str, ConfigurationSource] = {}
        self.schema_definitions: Dict[str, ConfigurationSchema] = {}
        
        # Encryption for secrets
        self.encryption_key = None
        self.cipher_suite = None
        
        # File watching
        self.file_watcher = None
        self.config_observer = None
        self.watch_active = False
        
        # Configuration validation
        self.validation_errors: List[str] = []
        self.last_reload_time = time.time()
        
        # Thread safety
        self.config_lock = threading.RLock()
        
        # Environment-specific paths
        self.config_paths = {
            "base": self.base_config_dir / "base.yaml",
            "environment": self.base_config_dir / f"{self.environment.value}.yaml",
            "local": self.base_config_dir / "local.yaml",
            "secrets": self.base_config_dir / ".secrets.enc",
            "schema": self.base_config_dir / "schema.yaml",
            "overrides": self.base_config_dir / "overrides.yaml"
        }
        
        # Create config directory structure
        self._initialize_config_structure()
        
        # Initialize encryption
        self._initialize_encryption()
        
        # Define configuration schema
        self._define_configuration_schema()
        
        # Load initial configuration
        self.load_configuration()

    def _detect_environment(self) -> Environment:
        """Detect current environment from various indicators"""
        # Check environment variable first
        env_var = os.getenv('SINCOR_ENVIRONMENT', '').lower()
        if env_var:
            try:
                return Environment(env_var)
            except ValueError:
                pass
        
        # Check for container indicators
        if os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER'):
            return Environment.DOCKER
        
        # Check for Kubernetes indicators
        if os.getenv('KUBERNETES_SERVICE_HOST') or os.path.exists('/var/run/secrets/kubernetes.io'):
            return Environment.KUBERNETES
        
        # Check for production indicators
        if os.getenv('PRODUCTION') or os.getenv('PROD'):
            return Environment.PRODUCTION
        
        # Check for staging indicators  
        if os.getenv('STAGING') or os.getenv('STAGE'):
            return Environment.STAGING
        
        # Check for testing indicators
        if os.getenv('TESTING') or os.getenv('TEST'):
            return Environment.TESTING
        
        # Check hostname patterns
        hostname = socket.gethostname().lower()
        if 'prod' in hostname:
            return Environment.PRODUCTION
        elif 'staging' in hostname or 'stage' in hostname:
            return Environment.STAGING
        elif 'test' in hostname:
            return Environment.TESTING
        
        # Default to development
        return Environment.DEVELOPMENT

    def _initialize_config_structure(self):
        """Initialize configuration directory structure"""
        try:
            # Create base config directory
            self.base_config_dir.mkdir(parents=True, exist_ok=True)
            
            # Create environment-specific subdirectories
            for env in Environment:
                env_dir = self.base_config_dir / env.value
                env_dir.mkdir(exist_ok=True)
            
            # Create templates if they don't exist
            self._create_default_configurations()
            
            self.logger.info(f"Configuration structure initialized at {self.base_config_dir}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize config structure: {e}")
            raise

    def _create_default_configurations(self):
        """Create default configuration templates"""
        default_configs = {
            "base.yaml": {
                "sincor": {
                    "name": "SINCOR Consciousness Infrastructure",
                    "version": "1.0.0",
                    "description": "Distributed consciousness infrastructure"
                },
                "server": {
                    "host": "0.0.0.0",
                    "port": 8080,
                    "workers": 4,
                    "timeout": 30
                },
                "logging": {
                    "level": "INFO",
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    "file": "./logs/sincor.log"
                },
                "consciousness": {
                    "god_mode_enabled": False,
                    "quantum_enabled": True,
                    "coherence_threshold": 0.8,
                    "version_control_enabled": True,
                    "multiverse_deployment": False
                },
                "quantum": {
                    "processor_types": ["superconducting", "trapped_ion", "photonic"],
                    "default_qubit_count": 64,
                    "coherence_time_ns": 1500,
                    "gate_fidelity": 0.999
                },
                "database": {
                    "type": "sqlite",
                    "path": "./data/sincor.db",
                    "backup_enabled": True,
                    "connection_pool_size": 10
                },
                "security": {
                    "encryption_enabled": True,
                    "authentication_required": True,
                    "session_timeout": 3600,
                    "max_failed_attempts": 5
                }
            },
            
            "development.yaml": {
                "logging": {
                    "level": "DEBUG"
                },
                "consciousness": {
                    "god_mode_enabled": True,
                    "debug_mode": True
                },
                "database": {
                    "path": "./data/dev_sincor.db"
                },
                "security": {
                    "authentication_required": False
                }
            },
            
            "production.yaml": {
                "server": {
                    "workers": 16,
                    "timeout": 60
                },
                "logging": {
                    "level": "WARNING",
                    "file": "/var/log/sincor/production.log"
                },
                "consciousness": {
                    "god_mode_enabled": False,
                    "debug_mode": False,
                    "multiverse_deployment": True
                },
                "database": {
                    "type": "postgresql",
                    "host": "${DB_HOST}",
                    "port": "${DB_PORT}",
                    "name": "${DB_NAME}",
                    "user": "${DB_USER}",
                    "password": "${DB_PASSWORD}"
                },
                "security": {
                    "authentication_required": True,
                    "session_timeout": 1800,
                    "encryption_enabled": True
                }
            },
            
            "god_mode.yaml": {
                "consciousness": {
                    "god_mode_enabled": True,
                    "unlimited_resources": True,
                    "quantum_enabled": True,
                    "multiverse_deployment": True,
                    "emergency_protocols_active": True
                },
                "quantum": {
                    "unlimited_qubits": True,
                    "perfect_coherence": True
                },
                "server": {
                    "unlimited_workers": True,
                    "no_timeout": True
                },
                "security": {
                    "god_mode_bypass": True
                }
            }
        }
        
        # Write configuration files if they don't exist
        for filename, config in default_configs.items():
            config_path = self.base_config_dir / filename
            if not config_path.exists():
                with open(config_path, 'w') as f:
                    yaml.dump(config, f, default_flow_style=False, indent=2)
        
        # Create schema file
        self._create_configuration_schema_file()

    def _create_configuration_schema_file(self):
        """Create configuration schema definition file"""
        schema_config = {
            "schema_version": "1.0",
            "configurations": {
                "server.port": {
                    "type": "int",
                    "required": True,
                    "min": 1024,
                    "max": 65535,
                    "description": "Server port number"
                },
                "consciousness.god_mode_enabled": {
                    "type": "bool",
                    "required": True,
                    "description": "Enable god mode with unlimited resources"
                },
                "consciousness.coherence_threshold": {
                    "type": "float",
                    "required": True,
                    "min": 0.0,
                    "max": 1.0,
                    "description": "Minimum consciousness coherence threshold"
                },
                "quantum.default_qubit_count": {
                    "type": "int",
                    "required": True,
                    "min": 1,
                    "max": 1000,
                    "description": "Default number of qubits for quantum processors"
                },
                "database.connection_pool_size": {
                    "type": "int",
                    "required": True,
                    "min": 1,
                    "max": 100,
                    "description": "Database connection pool size"
                }
            }
        }
        
        schema_path = self.config_paths["schema"]
        if not schema_path.exists():
            with open(schema_path, 'w') as f:
                yaml.dump(schema_config, f, default_flow_style=False, indent=2)

    def _initialize_encryption(self):
        """Initialize encryption system for secrets"""
        try:
            # Try to load existing key
            key_file = self.base_config_dir / ".encryption_key"
            
            if key_file.exists():
                with open(key_file, 'rb') as f:
                    self.encryption_key = f.read()
            else:
                # Generate new encryption key
                password = os.getenv('SINCOR_ENCRYPTION_PASSWORD')
                if not password:
                    password = getpass.getpass("Enter encryption password for secrets: ").encode()
                else:
                    password = password.encode()
                
                salt = os.urandom(16)
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                self.encryption_key = base64.urlsafe_b64encode(kdf.derive(password))
                
                # Save key (in production, use proper key management)
                with open(key_file, 'wb') as f:
                    f.write(self.encryption_key)
                
                # Save salt
                with open(self.base_config_dir / ".salt", 'wb') as f:
                    f.write(salt)
            
            # Initialize cipher
            self.cipher_suite = Fernet(self.encryption_key)
            
            self.logger.info("Encryption system initialized for secrets management")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize encryption: {e}")
            # Fall back to no encryption
            self.encryption_key = None
            self.cipher_suite = None

    def _define_configuration_schema(self):
        """Define configuration validation schema"""
        schemas = [
            # Server configuration
            ConfigurationSchema("server", "host", str, True, "0.0.0.0", ["valid_ip_or_hostname"]),
            ConfigurationSchema("server", "port", int, True, 8080, ["range(1024,65535)"]),
            ConfigurationSchema("server", "workers", int, True, 4, ["range(1,64)"]),
            ConfigurationSchema("server", "timeout", int, True, 30, ["range(5,300)"]),
            
            # Consciousness configuration
            ConfigurationSchema("consciousness", "god_mode_enabled", bool, True, False),
            ConfigurationSchema("consciousness", "quantum_enabled", bool, True, True),
            ConfigurationSchema("consciousness", "coherence_threshold", float, True, 0.8, ["range(0.0,1.0)"]),
            ConfigurationSchema("consciousness", "version_control_enabled", bool, True, True),
            ConfigurationSchema("consciousness", "multiverse_deployment", bool, False, False,
                              environment_overrides={Environment.PRODUCTION: True, Environment.GOD_MODE: True}),
            
            # Quantum configuration
            ConfigurationSchema("quantum", "default_qubit_count", int, True, 64, ["range(1,1000)"], consciousness_specific=True),
            ConfigurationSchema("quantum", "coherence_time_ns", float, True, 1500.0, ["range(100.0,10000.0)"]),
            ConfigurationSchema("quantum", "gate_fidelity", float, True, 0.999, ["range(0.9,1.0)"]),
            
            # Database configuration
            ConfigurationSchema("database", "type", str, True, "sqlite", ["enum(sqlite,postgresql,mysql)"]),
            ConfigurationSchema("database", "connection_pool_size", int, True, 10, ["range(1,100)"]),
            ConfigurationSchema("database", "backup_enabled", bool, True, True),
            
            # Security configuration
            ConfigurationSchema("security", "encryption_enabled", bool, True, True),
            ConfigurationSchema("security", "authentication_required", bool, True, True,
                              environment_overrides={Environment.DEVELOPMENT: False}),
            ConfigurationSchema("security", "session_timeout", int, True, 3600, ["range(300,86400)"]),
            ConfigurationSchema("security", "max_failed_attempts", int, True, 5, ["range(1,20)"]),
        ]
        
        # Register schemas
        for schema in schemas:
            schema_key = f"{schema.section}.{schema.key}"
            self.schema_definitions[schema_key] = schema

    def load_configuration(self) -> bool:
        """Load configuration from all sources"""
        with self.config_lock:
            try:
                self.logger.info(f"Loading configuration for environment: {self.environment.value}")
                
                # Clear current configuration
                self.configuration = {}
                self.validation_errors = []
                
                # Load configurations in priority order
                config_files = [
                    ("base", self.config_paths["base"]),
                    ("environment", self.config_paths["environment"]),
                    ("local", self.config_paths["local"]),
                    ("overrides", self.config_paths["overrides"])
                ]
                
                for config_name, config_path in config_files:
                    if config_path.exists():
                        try:
                            loaded_config = self._load_config_file(config_path)
                            self._merge_configuration(loaded_config)
                            self.logger.debug(f"Loaded configuration from {config_path}")
                        except Exception as e:
                            self.logger.error(f"Failed to load {config_name} configuration: {e}")
                            if config_name in ["base", "environment"]:
                                raise  # These are critical
                
                # Load environment variables
                self._load_environment_variables()
                
                # Load secrets
                self._load_secrets()
                
                # Substitute environment variables
                self._substitute_environment_variables()
                
                # Validate configuration
                self._validate_configuration()
                
                # Apply environment-specific overrides
                self._apply_environment_overrides()
                
                self.last_reload_time = time.time()
                
                if self.validation_errors:
                    self.logger.warning(f"Configuration validation errors: {self.validation_errors}")
                    return False
                
                self.logger.info("Configuration loaded successfully")
                return True
                
            except Exception as e:
                self.logger.error(f"Failed to load configuration: {e}")
                raise

    def _load_config_file(self, config_path: Path) -> Dict[str, Any]:
        """Load configuration from a file"""
        try:
            with open(config_path, 'r') as f:
                if config_path.suffix.lower() in ['.yaml', '.yml']:
                    return yaml.safe_load(f) or {}
                elif config_path.suffix.lower() == '.json':
                    return json.load(f)
                elif config_path.suffix.lower() == '.toml':
                    return toml.load(f)
                elif config_path.suffix.lower() in ['.ini', '.cfg']:
                    config = configparser.ConfigParser()
                    config.read(config_path)
                    return {s: dict(config.items(s)) for s in config.sections()}
                else:
                    # Default to YAML
                    return yaml.safe_load(f) or {}
        except Exception as e:
            self.logger.error(f"Failed to parse config file {config_path}: {e}")
            return {}

    def _merge_configuration(self, new_config: Dict[str, Any]):
        """Merge new configuration into existing configuration"""
        def deep_merge(target: Dict[str, Any], source: Dict[str, Any]):
            for key, value in source.items():
                if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                    deep_merge(target[key], value)
                else:
                    target[key] = value
        
        deep_merge(self.configuration, new_config)

    def _load_environment_variables(self):
        """Load configuration from environment variables"""
        # Load SINCOR_* environment variables
        env_config = {}
        
        for key, value in os.environ.items():
            if key.startswith('SINCOR_'):
                # Convert SINCOR_SERVER_PORT to server.port
                config_key = key[7:].lower().replace('_', '.')  # Remove SINCOR_ prefix
                
                # Convert string values to appropriate types
                converted_value = self._convert_env_value(value)
                
                # Set nested configuration
                self._set_nested_config(env_config, config_key, converted_value)
        
        if env_config:
            self._merge_configuration(env_config)
            self.logger.debug(f"Loaded {len(env_config)} environment variables")

    def _convert_env_value(self, value: str) -> Union[str, int, float, bool]:
        """Convert environment variable string to appropriate type"""
        # Boolean conversion
        if value.lower() in ['true', 'false']:
            return value.lower() == 'true'
        
        # Integer conversion
        try:
            if '.' not in value:
                return int(value)
        except ValueError:
            pass
        
        # Float conversion
        try:
            return float(value)
        except ValueError:
            pass
        
        # Return as string
        return value

    def _set_nested_config(self, config: Dict[str, Any], key: str, value: Any):
        """Set nested configuration value using dot notation"""
        keys = key.split('.')
        current = config
        
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        
        current[keys[-1]] = value

    def _load_secrets(self):
        """Load encrypted secrets"""
        secrets_path = self.config_paths["secrets"]
        
        if not secrets_path.exists() or not self.cipher_suite:
            return
        
        try:
            with open(secrets_path, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.cipher_suite.decrypt(encrypted_data)
            secrets_config = json.loads(decrypted_data.decode())
            
            # Merge secrets into configuration
            self._merge_configuration(secrets_config)
            
            self.logger.debug("Loaded encrypted secrets")
            
        except Exception as e:
            self.logger.error(f"Failed to load secrets: {e}")

    def _substitute_environment_variables(self):
        """Substitute ${VAR} placeholders with environment variables"""
        def substitute_recursive(obj):
            if isinstance(obj, dict):
                return {k: substitute_recursive(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [substitute_recursive(item) for item in obj]
            elif isinstance(obj, str) and obj.startswith('${') and obj.endswith('}'):
                env_var = obj[2:-1]
                return os.getenv(env_var, obj)  # Return original if env var not found
            else:
                return obj
        
        self.configuration = substitute_recursive(self.configuration)

    def _validate_configuration(self):
        """Validate configuration against schema"""
        for schema_key, schema in self.schema_definitions.items():
            section, key = schema_key.split('.', 1)
            
            # Get configuration value
            config_value = self.get(f"{section}.{key}")
            
            # Check if required
            if schema.required and config_value is None:
                if schema.default_value is not None:
                    self.set(f"{section}.{key}", schema.default_value)
                else:
                    self.validation_errors.append(f"Required configuration missing: {schema_key}")
                continue
            
            if config_value is None:
                continue
            
            # Type validation
            if not isinstance(config_value, schema.data_type):
                try:
                    # Try to convert
                    converted_value = schema.data_type(config_value)
                    self.set(f"{section}.{key}", converted_value)
                except (ValueError, TypeError):
                    self.validation_errors.append(f"Invalid type for {schema_key}: expected {schema.data_type.__name__}")
                    continue
            
            # Validation rules
            if schema.validation_rules:
                for rule in schema.validation_rules:
                    if not self._validate_rule(config_value, rule):
                        self.validation_errors.append(f"Validation failed for {schema_key}: {rule}")

    def _validate_rule(self, value: Any, rule: str) -> bool:
        """Validate a single validation rule"""
        try:
            if rule.startswith('range('):
                # Extract range values
                range_str = rule[6:-1]  # Remove 'range(' and ')'
                min_val, max_val = map(float, range_str.split(','))
                return min_val <= value <= max_val
            
            elif rule.startswith('enum('):
                # Extract enum values
                enum_str = rule[5:-1]  # Remove 'enum(' and ')'
                allowed_values = [v.strip() for v in enum_str.split(',')]
                return str(value) in allowed_values
            
            elif rule == 'valid_ip_or_hostname':
                # Basic IP/hostname validation
                import ipaddress
                try:
                    ipaddress.ip_address(value)
                    return True
                except ValueError:
                    # Check if it's a valid hostname
                    return len(value) > 0 and '.' in value
            
            else:
                self.logger.warning(f"Unknown validation rule: {rule}")
                return True
        
        except Exception as e:
            self.logger.error(f"Validation rule error: {e}")
            return False

    def _apply_environment_overrides(self):
        """Apply environment-specific overrides from schema"""
        for schema_key, schema in self.schema_definitions.items():
            if schema.environment_overrides and self.environment in schema.environment_overrides:
                override_value = schema.environment_overrides[self.environment]
                self.set(schema_key, override_value)
                self.logger.debug(f"Applied environment override for {schema_key}: {override_value}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation"""
        keys = key.split('.')
        current = self.configuration
        
        try:
            for k in keys:
                current = current[k]
            return current
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value: Any):
        """Set configuration value using dot notation"""
        with self.config_lock:
            self._set_nested_config(self.configuration, key, value)

    def get_environment(self) -> Environment:
        """Get current environment"""
        return self.environment

    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment == Environment.PRODUCTION

    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment == Environment.DEVELOPMENT

    def is_god_mode(self) -> bool:
        """Check if god mode is enabled"""
        return self.get('consciousness.god_mode_enabled', False) or self.environment == Environment.GOD_MODE

    def get_consciousness_config(self) -> Dict[str, Any]:
        """Get consciousness-specific configuration"""
        return self.get('consciousness', {})

    def get_quantum_config(self) -> Dict[str, Any]:
        """Get quantum-specific configuration"""
        return self.get('quantum', {})

    def get_database_config(self) -> Dict[str, Any]:
        """Get database configuration"""
        return self.get('database', {})

    def get_server_config(self) -> Dict[str, Any]:
        """Get server configuration"""
        return self.get('server', {})

    def get_security_config(self) -> Dict[str, Any]:
        """Get security configuration"""
        return self.get('security', {})

    def save_secret(self, key: str, value: str, environments: List[Environment] = None):
        """Save encrypted secret"""
        if not self.cipher_suite:
            raise ValueError("Encryption not available for secrets")
        
        secret = SecretConfiguration(
            key=key,
            value=value,
            encrypted=True,
            environments=environments or [self.environment],
            expires_at=None,
            rotation_required=False
        )
        
        self.secrets[key] = secret
        
        # Save to encrypted file
        self._save_secrets_file()
        
        self.logger.info(f"Secret saved: {key}")

    def _save_secrets_file(self):
        """Save secrets to encrypted file"""
        if not self.cipher_suite:
            return
        
        secrets_data = {
            secret.key: secret.value 
            for secret in self.secrets.values()
            if not secret.environments or self.environment in secret.environments
        }
        
        try:
            json_data = json.dumps(secrets_data).encode()
            encrypted_data = self.cipher_suite.encrypt(json_data)
            
            with open(self.config_paths["secrets"], 'wb') as f:
                f.write(encrypted_data)
                
        except Exception as e:
            self.logger.error(f"Failed to save secrets: {e}")

    def start_config_watching(self):
        """Start watching configuration files for changes"""
        if self.watch_active:
            return
        
        try:
            class ConfigFileHandler(FileSystemEventHandler):
                def __init__(self, config_manager):
                    self.config_manager = config_manager
                
                def on_modified(self, event):
                    if event.is_directory:
                        return
                    
                    file_path = Path(event.src_path)
                    if file_path.suffix in ['.yaml', '.yml', '.json', '.toml', '.ini']:
                        self.config_manager.logger.info(f"Configuration file changed: {file_path}")
                        # Reload configuration after a brief delay
                        threading.Timer(1.0, self.config_manager.reload_configuration).start()
            
            self.file_watcher = ConfigFileHandler(self)
            self.config_observer = Observer()
            self.config_observer.schedule(self.file_watcher, str(self.base_config_dir), recursive=True)
            self.config_observer.start()
            
            self.watch_active = True
            self.logger.info("Configuration file watching started")
            
        except Exception as e:
            self.logger.error(f"Failed to start config watching: {e}")

    def stop_config_watching(self):
        """Stop watching configuration files"""
        if self.config_observer:
            self.config_observer.stop()
            self.config_observer.join()
            self.watch_active = False
            self.logger.info("Configuration file watching stopped")

    def reload_configuration(self):
        """Reload configuration from files"""
        try:
            self.logger.info("Reloading configuration...")
            success = self.load_configuration()
            
            if success:
                self.logger.info("Configuration reloaded successfully")
            else:
                self.logger.error("Configuration reload failed validation")
                
            return success
            
        except Exception as e:
            self.logger.error(f"Configuration reload failed: {e}")
            return False

    def export_configuration(self, format: ConfigFormat = ConfigFormat.YAML) -> str:
        """Export current configuration to string"""
        try:
            if format == ConfigFormat.YAML:
                return yaml.dump(self.configuration, default_flow_style=False, indent=2)
            elif format == ConfigFormat.JSON:
                return json.dumps(self.configuration, indent=2)
            elif format == ConfigFormat.TOML:
                return toml.dumps(self.configuration)
            else:
                return str(self.configuration)
        except Exception as e:
            self.logger.error(f"Failed to export configuration: {e}")
            return ""

    def get_configuration_status(self) -> Dict[str, Any]:
        """Get configuration management status"""
        return {
            "environment": self.environment.value,
            "config_directory": str(self.base_config_dir),
            "last_reload_time": self.last_reload_time,
            "validation_errors": self.validation_errors,
            "watch_active": self.watch_active,
            "encryption_enabled": self.cipher_suite is not None,
            "secrets_count": len(self.secrets),
            "schema_definitions": len(self.schema_definitions),
            "configuration_keys": list(self.configuration.keys()),
            "god_mode": self.is_god_mode(),
            "production": self.is_production()
        }

    def shutdown(self):
        """Shutdown configuration manager"""
        self.stop_config_watching()
        self.logger.info("Configuration manager shutdown complete")


# Example usage and testing
if __name__ == "__main__":
    import tempfile
    import shutil
    
    logging.basicConfig(level=logging.INFO)
    
    # Create temporary config directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        print("🛠️ TESTING ENVIRONMENT CONFIGURATION MANAGER 🛠️")
        
        # Test different environments
        for env in [Environment.DEVELOPMENT, Environment.PRODUCTION, Environment.GOD_MODE]:
            print(f"\n📋 Testing {env.value} environment...")
            
            config_manager = EnvironmentConfigurationManager(temp_dir, env)
            
            # Test configuration access
            print(f"Server port: {config_manager.get('server.port')}")
            print(f"God mode enabled: {config_manager.is_god_mode()}")
            print(f"Quantum enabled: {config_manager.get('consciousness.quantum_enabled')}")
            print(f"Coherence threshold: {config_manager.get('consciousness.coherence_threshold')}")
            
            # Test environment-specific overrides
            if env == Environment.GOD_MODE:
                print(f"Unlimited resources: {config_manager.get('consciousness.unlimited_resources')}")
                print(f"Perfect coherence: {config_manager.get('quantum.perfect_coherence')}")
            
            # Test configuration export
            yaml_config = config_manager.export_configuration(ConfigFormat.YAML)
            print(f"Configuration size: {len(yaml_config)} characters")
            
            # Get status
            status = config_manager.get_configuration_status()
            print(f"Validation errors: {len(status['validation_errors'])}")
            print(f"Schema definitions: {status['schema_definitions']}")
            
            config_manager.shutdown()
        
        print("\n🌟 ENVIRONMENT CONFIGURATION MANAGER TEST COMPLETE! 🌟")