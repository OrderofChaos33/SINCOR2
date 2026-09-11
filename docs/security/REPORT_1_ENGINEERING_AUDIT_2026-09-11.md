# Report 1 — Security engineering audit

**Scope:** SINCOR2 auth/session secrets and the CHROMA shop surface.  
**Date:** 2026-09-11  
**Auditor:** implementation pass (same change-set as the code).  
**Repo:** OrderofChaos33/SINCOR2  
**Production entry:** `gunicorn sincor2.mvp_app:app` (Dockerfile).

## Method

1. Traced the live boot path (`Dockerfile` → `mvp_app.py`).
2. Grepped committed secret fallbacks (`change-in-production`, `change-me`, `demo-secret`).
3. Reviewed CHROMA login/logout/send-gate against `_is_authed()`.
4. Compared `settings.py` (already fail-closed in prod) with older Flask constructors that bypassed it.

## Findings (pre-fix)

| ID | Severity | Location | Issue |
|----|----------|----------|--------|
| S1 | High | `src/sincor2/chroma_app.py` | Shared fallback `chroma-demo-secret-change-me`. Forgeable sessions if this factory ran without `SECRET_KEY`. |
| S2 | High | `src/sincor2/app.py` | Shared fallback `development-key-change-in-production`. |
| S3 | High | `src/sincor2/auth_system.py` | Shared JWT fallback `dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars`. |
| S4 | High | `scripts/ops/sincor_stripe_app.py` | Shared fallback `sincor-secret-key-change-in-production`. Ops script, not Railway CMD, still a committed secret. |
| S5 | High | `verticals/auto_detailing/blueprint.py` | `/chroma/logout` popped only `chroma_shop`. Login also set `admin_username`, which `_is_authed()` treats as logged in. |
| S6 | Medium | `verticals/auto_detailing/send_gate.py` | `approve()` on an already-`sent` item could re-deliver once a provider is wired. |
| S7 | Medium | `verticals/auto_detailing/pipeline.py` | `live_send` always `False`, lying to callers vs `/chroma/health`. |
| S8 | Medium | `verticals/auto_detailing/config.py` | `CHROMA_DEMO=true` skipped auth. Dangerous if that flag is ever set on Railway. |
| S9 | Low | `mvp_app.py` | Missing JWT secret logged critical then booted on `os.urandom` per process. Not a shared git secret, but hides misconfiguration and breaks multi-worker cookies. Did not read `SECRET_KEY` for Flask sessions (`FLASK_SECRET_KEY` only). |

`settings.py` was already correct: production requires strong `SECRET_KEY` / `JWT_SECRET_KEY`; non-prod generates in-memory keys. The hole was constructors that never called it.

## Remediations in this change-set

- New `sincor2.runtime_secrets`: env only, or in-memory random, or hard-fail in production/Railway. **No string literals used as secrets.**
- `mvp_app.py`, `app.py`, `chroma_app.py`, `auth_system.py`, `sincor_stripe_app.py` use that helper.
- CHROMA logout clears `chroma_shop`, `admin_username`, `is_admin`.
- Send-gate rejects re-approve of `sent`.
- Pipeline reports real `live_send`.
- `demo_mode()` is forced off when `RAILWAY_ENVIRONMENT` is set or env is production.

## What this audit does not claim

- Full pentest of A2A, payments, or on-chain hooks.
- Proof that Railway env vars are currently set (operator must confirm `SECRET_KEY` and `JWT_SECRET_KEY` on the service).
- Removal of historical strings from git history. Rotate live keys if those dummy values were ever used as real secrets (they should not have been).

## Operator check after merge

```
# Railway service running getsincor.com
SECRET_KEY          # set, ≥16 chars, not any demo string
JWT_SECRET_KEY      # set, ≥16 chars
ADMIN_PASSWORD      # set, not changeme123
CHROMA_DEMO         # unset or false
CHROMA_LIVE_SEND    # false until a shop is live
```
