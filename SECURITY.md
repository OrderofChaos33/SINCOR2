# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (main branch) | ✅ |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, report security vulnerabilities via email:

📧 **security@getsincor.com**

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

You will receive a response within **48 hours** confirming receipt. We aim to resolve critical issues within **7 days** and will notify you when the fix is deployed.

## Security Architecture

SINCOR implements the following security controls:

| Control | Implementation |
|---|---|
| **Authentication** | JWT (HS256) with configurable expiry |
| **Authorization** | Role-based access (admin / user) |
| **Rate Limiting** | Per-IP, per-endpoint limits via Flask-Limiter |
| **Input Validation** | Pydantic v2 models on all POST routes |
| **HTTP Headers** | X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy, Permissions-Policy |
| **Secrets** | All credentials via environment variables — never in source code |
| **CORS** | Allowlist-based origin control |
| **Dependency Scanning** | Pinned versions in requirements.txt |

## Environment Variable Security

- **Never commit `.env` files** — `.gitignore` excludes them by default
- Use Railway / Heroku secrets or a secrets manager (AWS Secrets Manager, HashiCorp Vault) in production
- Rotate `JWT_SECRET_KEY` and `FLASK_SECRET_KEY` regularly
- Use PayPal **live** mode only after thorough sandbox testing

## Responsible Disclosure

We follow responsible disclosure practices. Security researchers who report valid vulnerabilities will be acknowledged in release notes (with permission).
