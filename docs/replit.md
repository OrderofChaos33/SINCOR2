# Overview

SINCOR is an AI Business Automation Platform that leverages a 43-agent swarm architecture to deliver instant business intelligence, predictive analytics, and automated agent services. The platform operates as a multi-revenue stream business with services ranging from $2,500 instant BI reports to $50,000+ enterprise partnerships. The system is designed for immediate monetization through high-value deliverables while maintaining infinite scaling capabilities at low operational costs.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Multi-Agent Swarm Architecture
The core system operates on a 43-agent persistent swarm with 7 distinct archetypes:
- **Scout Agents (8)**: Market intelligence, competitive analysis, lead prospecting
- **Synthesizer Agents (6)**: Executive briefings, intelligence fusion, technical documentation  
- **Builder Agents (7)**: Architecture design, automation development, system integration
- **Negotiator Agents (6)**: Partnership negotiations, client sales, proposal development
- **Caretaker Agents (5)**: Data maintenance, compliance oversight, system monitoring
- **Auditor Agents (5)**: Quality validation, compliance checking, risk assessment
- **Director Agents (6)**: Strategic coordination, priority setting, market clearing

## Identity & Authority System
Uses cryptographic DID (Decentralized Identifier) keys for each agent with Soulbound Token (SBT) role management. Each agent maintains unique identity through constitution hash verification and promotion tracking.

## Memory Architecture (4-Tier)
- **Episodic Memory**: Time-stamped events in append-only logs
- **Semantic Memory**: Facts, profiles, rules in graph store format
- **Procedural Memory**: Tools, routines, prompts in versioned registries
- **Autobiographical Memory**: Self-narrative curation and goal tracking

## Monetization Engine
Orchestrates multiple revenue streams through dynamic pricing, recursive value products, and strategic partnerships. Integrates PayPal payment processing with automated upselling and customer lifetime value optimization.

## Real-Time Intelligence System
Provides live market data streams including financial markets, news feeds, social media sentiment, competitor monitoring, and industry trend detection for immediate strategic adjustment.

## Scaling & Resource Management
Implements infinite scaling at $1 cost per agent with predictive workload forecasting, substrate-aware resource allocation, and intent-driven autoscaling based on cognitive resonance patterns.

## Web Application Framework
Built on Flask with production deployment configuration for Railway. Includes health monitoring, payment processing endpoints, and dashboard interfaces for system management.

# External Dependencies

## Payment Processing
- **PayPal REST API**: Primary payment processor using sandbox/live environment configuration
- Environment variables: `PAYPAL_ENV`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`

## Data Sources & APIs
- **LinkedIn**: Cookie-based data extraction for lead intelligence
- **Crunchbase API**: Company and funding data
- **Google Maps API**: Location and business data
- **News API**: Real-time news feed integration
- **G2 API**: Software review and competitive intelligence

## Development & Deployment
- **Railway**: Primary hosting platform with environment-specific configurations
- **DigitalOcean**: Alternative deployment target with migration documentation
- **SQLite**: Local data storage for memory systems and agent state
- **PostgreSQL**: Production database option via Drizzle ORM compatibility

## Machine Learning & Analytics
- **scikit-learn**: Predictive scaling and quality scoring algorithms
- **NumPy**: Mathematical operations and vector computations
- **OpenTelemetry**: Industry-standard observability and monitoring

## Web Technologies
- **Flask**: Primary web framework with SocketIO for real-time features
- **Tailwind CSS**: Frontend styling via CDN
- **Chart.js**: Dashboard visualization and metrics display
- **Gunicorn**: Production WSGI server for deployment

## Security & Infrastructure
- **Cryptography libraries**: ECDSA, RSA for identity system
- **psutil**: System resource monitoring
- **PyYAML**: Configuration management
- **python-dotenv**: Environment variable management