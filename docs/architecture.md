# SINCOR System Architecture

## Overview

SINCOR is built as a modular, agent-based system where specialized components handle different aspects of the business workflow. The architecture follows a decentralized pattern where agents can operate independently while coordinating through shared resources.

## Core Components

### Web Application Layer (`sincor_app.py`)
- **Flask-based web server** providing REST endpoints
- **Lead capture system** for customer intake
- **File management** for outputs and logs
- **Health monitoring** and diagnostics

### Agent Framework

#### 1. GAZETTE Agents (Compliance Layer)
**Location**: `agents/gazette/`

- **KYC Agent** (`kyc_agent.py`): Customer identity verification
- **AML Agent** (`aml_agent.py`): Anti-money laundering monitoring  
- **SEC Watchdog** (`sec_watchdog.py`): Securities compliance oversight
- **Main Coordinator** (`gazette_main.py`): Orchestrates compliance workflows

#### 2. OVERSIGHT Agents (System Monitoring)
**Location**: `agents/oversight/`

- **Oversight Agent** (`oversight_agent.py`): System health monitoring
- **Sentinel Node** (`sentinel_node.py`): Security and anomaly detection
- **Build Coordination** (`build_coordination_agent.py`): Development workflow management

#### 3. MARKETING Agents (Content Generation)
**Location**: `agents/marketing/`

- **Content Generator** (`content_gen_agent.py`): Automated content creation
- **Distribution Handler** (`distribution_handler.py`): Content publishing
- **Profile Sync** (`profile_sync_agent.py`): Social media integration
- **STEM Clip Agent** (`stem_clip_agent.py`): Video content processing

#### 4. PAYDAE Agents (Financial Operations)
**Location**: `agents/paydae/`

- **Reward Trigger** (`reward_trigger.py`): Incentive system management
- **Task Logger** (`task_logger.py`): Work tracking and billing
- **Token Simulator** (`token_simulator.py`): Cryptocurrency operations

#### 5. TASKPOOL Agents (Work Distribution)
**Location**: `agents/taskpool/`

- **Dispatcher** (`taskpool_dispatcher.py`): Job queue management
- **Content Workers** (`content_worker.py`): Content processing tasks
- **Image Workers** (`image_worker.py`): Media processing tasks

## Data Flow

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Web Request   │───▶│   Flask App     │───▶│   Agent Layer   │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                               │                        │
                               ▼                        ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │   File System   │    │   Log System    │
                       │   (outputs/)    │    │   (logs/)       │
                       └─────────────────┘    └─────────────────┘
```

## Configuration Management

### Environment Configuration
- **Development**: `config/environment.sample.env`
- **Production**: `config/.env` (user-created)
- **Agent Roles**: `config/agent_roles.yaml`

### Agent Coordination
Agents coordinate through:
- **Shared file system**: Common outputs and logs directories
- **Configuration files**: YAML-based role definitions
- **Message passing**: File-based communication patterns

## Security Architecture

### Credential Management
- Environment variable-based configuration
- No hardcoded secrets in source code
- Secure SMTP integration for notifications

### Access Control
- File system permissions for agent outputs
- Log file protection and rotation
- Network-level security for web endpoints

## Scalability Patterns

### Horizontal Scaling
- Agent processes can run on separate machines
- Shared storage for coordination
- Load balancing for web traffic

### Vertical Scaling
- Multi-threaded agent processing
- Database optimization for large datasets
- Caching layers for frequent operations

## Monitoring and Observability

### Health Checks
- Web application health endpoint (`/health`)
- Agent-level diagnostic functions
- File system monitoring

### Logging
- Centralized logging in `logs/` directory
- Timestamped entries with agent identification
- Error tracking and alerting capabilities

## Development Workflow

### Agent Development Pattern
1. Create agent class extending base functionality
2. Implement `heartbeat()` and `run_diagnostics()` methods
3. Add configuration to `agent_roles.yaml`
4. Write comprehensive tests
5. Document agent responsibilities

### Testing Strategy
- Unit tests for individual agents
- Integration tests for web endpoints
- End-to-end workflow testing
- Performance and load testing

This architecture enables SINCOR to operate as a truly decentralized autonomous enterprise, with agents handling specialized tasks while maintaining system-wide coordination and oversight.