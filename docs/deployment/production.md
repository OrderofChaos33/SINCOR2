# Production Deployment Checklist

This checklist complements `DEPLOYMENT_GUIDE.md` and focuses on production readiness gates.

## 1) Environment and Secrets
- [ ] `SECRET_KEY`, `JWT_SECRET_KEY`, and admin credentials are set securely
- [ ] API keys (Anthropic, Stripe/PayPal, other providers) are stored in environment variables
- [ ] `.env` is excluded from source control and secret scanning is enabled in CI
- [ ] Separate values exist for staging and production

## 2) Runtime Configuration
- [ ] Application starts with `gunicorn sincor2.mvp_app:app`
- [ ] `FLASK_ENV=production` and debug mode is disabled
- [ ] Rate limit storage uses Redis for multi-worker safety
- [ ] Health endpoint (`/health`) is reachable from orchestrator/load balancer

## 3) Security Controls
- [ ] HTTPS/TLS is enforced end-to-end
- [ ] Security headers are enabled (CSP, HSTS, frame/content protections)
- [ ] Authentication and authorization flows are validated
- [ ] Dependency updates and vulnerability checks are current

## 4) A2A and Marketplace Readiness
- [ ] Agent Card endpoint (`/.well-known/agent-card.json`) is valid
- [ ] Discovery and capability matching flows are verified
- [ ] Reputation and policy enforcement paths are validated
- [ ] Delegated execution and fallback behavior are tested

## 5) Payments and On-Chain Settlement
- [ ] SINC/AXIOM settlement settings are configured for Base
- [ ] Treasury routing and payout controls are verified
- [ ] Payment provider webhooks are configured and signed
- [ ] Failure/retry logic is tested for off-chain and on-chain paths

## 6) Observability and Operations
- [ ] Structured logging is enabled and retained
- [ ] Metrics and alerting are configured for latency, errors, and queue depth
- [ ] Incident response contacts and escalation paths are documented
- [ ] Backup and restore procedures are tested

## 7) Release Validation
- [ ] Automated test suite runs clean in CI for release branch
- [ ] Smoke tests pass post-deploy (auth, A2A task flow, payments, health)
- [ ] Rollback procedure is verified
- [ ] Release notes/changelog updated
