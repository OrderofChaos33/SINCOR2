# Security Policy

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, please report it privately — do not open a public issue for it. Either route reaches the founder:

1. **GitHub Security Advisories:** repository → **Security** tab → **Report a vulnerability**, or
2. **Email:** court@getsincor.com with as much detail as possible (affected component, reproduction steps, impact).

We will acknowledge receipt within 48 hours and aim to provide an initial assessment within 7 days. Fixes for confirmed vulnerabilities land before any public disclosure discussion.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | ✅ Active support  |
| main    | ✅ Active support  |

## Security Measures

- Dependency scanning and automated updates
- Secret scanning in CI (gitleaks)
- Rate limiting and authentication hardening
- Regular security workflow improvements

Do not commit secrets, private keys, or production credentials.
