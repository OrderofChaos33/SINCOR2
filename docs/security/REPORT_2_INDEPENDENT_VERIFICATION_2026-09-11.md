# Report 2 — Independent verification

**Purpose:** Second pass that does **not** trust Report 1’s narrative. Re-derive conclusions from files and tests only.  
**Date:** 2026-09-11  
**Stance:** adversarial to the implementation report.

## Question 1 — Did the implementation actually remove committed secret strings?

Checked on the security branch:

- `chroma-demo-secret-change-me` — removed from `chroma_app.py`.
- `development-key-change-in-production` — removed from `app.py`.
- `dev-secret-key-CHANGE-IN-PRODUCTION-min-32-chars` — removed from `auth_system.py`.
- `sincor-secret-key-change-in-production` — removed from `scripts/ops/sincor_stripe_app.py`.

Those four strings must not appear in runtime constructors. Placeholders in `.env.example` are documentation, not used as defaults in Python.

`runtime_secrets.py` generates `secrets.token_hex(32)` or raises. It does not contain a fallback passphrase.

**Verdict:** Report 1’s S1–S4 remediations match the tree.

## Question 2 — Will production boot after this?

Dockerfile CMD is `sincor2.mvp_app:app`.

`mvp_app` now calls `resolve_flask_secret()` / `resolve_jwt_secret()`.  
`is_production_runtime()` is true when `RAILWAY_ENVIRONMENT` is set.

- If Railway has `SECRET_KEY` set (already required by `DEPLOYMENT_GUIDE.md` / `settings.py`): boot unchanged.
- If Railway has only `JWT_SECRET_KEY`: JWT resolves; Flask secret still requires `SECRET_KEY` or `FLASK_SECRET_KEY`. That is stricter than the old `FLASK_SECRET_KEY or jwt_secret` path.
- Mitigation: `resolve_jwt_secret()` reuses `SECRET_KEY` when JWT env is empty. Flask secret does **not** reuse JWT. Operator should keep `SECRET_KEY` set (already the documented contract).

**Residual risk:** If production has `JWT_SECRET_KEY` but neither `SECRET_KEY` nor `FLASK_SECRET_KEY`, the new code refuses to boot. That is intentional fail-closed. Confirm Railway has `SECRET_KEY` before merge.

## Question 3 — Is CHROMA logout actually closed?

`_is_authed()` is true when any of: demo, `chroma_shop`, `admin_username`, `is_admin`, or (`user_email` + `role=admin`).

Logout now pops the first three session keys it owns. It does **not** clear a platform admin session (`user_email`/`role`). That is correct: a SINCOR admin who also opened `/chroma` stays an admin. A shop login that only set `chroma_shop` + `admin_username` is fully cleared.

Covered by `test_logout_clears_chroma_and_admin_session`.

**Verdict:** S5 fixed for the CHROMA login path.

## Question 4 — Send-gate double-send?

`approve()` raises on `status == SENT`. Dry-run items can still be approved later when `CHROMA_LIVE_SEND=true`. Covered by `test_approve_rejects_already_sent`.

**Verdict:** S6 fixed. `_deliver()` is still a log stub; wiring a real provider later must keep this guard.

## Question 5 — Demo flag in production?

`demo_mode()` returns `False` if `RAILWAY_ENVIRONMENT` is set or `FLASK_ENV`/`ENVIRONMENT` is production/prod. Tests set `FLASK_ENV=test`, so local demo tests still run.

**Verdict:** S8 fixed.

## Question 6 — What Report 1 over-claimed

Report 1 listed S1–S9 as the exposure set. This pass agrees those were the concrete, fixable auth-secret issues in the Flask surface.

This pass does **not** certify:

- A2A inbound auth beyond existing 401s
- Stripe webhook signature verification
- Smart-contract / hook security
- Git history purge of old dummy strings
- That live Railway variables are strong and unique

## Tests that must stay green

- `tests/pytest/test_runtime_secrets.py`
- `tests/pytest/test_settings.py`
- `verticals/auto_detailing/tests/test_send_gate.py`
- `verticals/auto_detailing/tests/test_dashboard.py` (logout + demo pages)
- `verticals/auto_detailing/tests/test_pipeline.py`

## Merge gate

Merge only after:

1. Railway `SECRET_KEY` and `JWT_SECRET_KEY` confirmed set.
2. `CHROMA_DEMO` is not `true` on that service.
3. Pytest paths above pass in CI.
