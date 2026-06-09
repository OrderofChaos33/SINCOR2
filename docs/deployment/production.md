# Production Deployment & Operations Guide

## Overview
This guide defines the standards and practices for running SINCOR2 reliably in production environments.

## Supported Deployment Targets
- Railway (primary recommended path)
- Docker / container platforms
- Cloud VMs with systemd or supervisor

## Environment & Configuration
All configuration must be supplied via environment variables. Never commit secrets.

Required categories:
- LLM provider credentials
- Wallet / on-chain keys (Base)
- Payment providers (Stripe, etc.)
- Database / storage configuration (if used)
- Monitoring and observability keys

## Railway Production Setup
1. Connect repository and enable automatic deploys.
2. Configure all environment variables in the Railway dashboard.
3. Use `railway.json` for build and start commands.
4. After code pushes, manually trigger redeploy if needed for immediate effect.

## Docker Production Standards
- Use multi-stage builds for smaller images.
- Run as non-root user.
- Healthcheck on `/health`.
- Proper signal handling for graceful shutdown.

## Security Requirements
- Enforce rate limiting and authentication on all public endpoints.
- Rotate secrets regularly.
- Enable secret scanning in CI.
- Use HTTPS only.
- Treat all agent-to-agent and payment data as sensitive.

## Observability & Monitoring
- Expose `/health` and structured logging.
- Integrate error tracking (e.g. Sentry).
- Add metrics collection for task volume, latency, and error rates.
- Log aggregation is required for production troubleshooting.

## Scaling & Reliability
- The orchestration layer supports horizontal scaling.
- For high-throughput workloads, introduce async task processing.
- Implement circuit breakers and retry policies on external calls.
- Database connection pooling and query optimization recommended.

## Operational Checklist
- All dependencies scanned and up to date.
- Tests passing in CI before deploy.
- Monitoring and alerting configured.
- Rollback plan defined (previous Railway deployment or Docker tag).
