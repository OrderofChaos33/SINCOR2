# Secrets hygiene — 2026-09-10

Public repo. Prior CLI key leak. Two of the last three commits were auth/secret fixes (Railway quote-stripping, admin identity matching). This is the standing order.

## Sweep (this pass)

Grep of current tree for `sk_live_`, `sk_test_`, `AKIA`, `ghp_`, `xai-`, `sk-ant-`, PEM private keys: **no live secrets in tracked files**. The only `sk_test_` hit historically lives in tests (fake). `.env` is gitignored. `.env.example` is names only.

`.gitleaks.toml` allowlists four historical files. Allowlisting is not rotation.

## GitHub secret scanning — ON

Four historical alerts, all **resolved as revoked** 2026-06-15:

| # | Type | Path | Publicly leaked |
|---|---|---|
| 1 | Google API key | `docs/CLINTON_AUTO_DETAILING_SETUP.md` | yes |
| 2 | Google API key | `docs/CLINTON_AUTO_DETAILING_SETUP.md` | yes |
| 3 | Google API key | `code_pkg/start_syndicator.py` | yes |
| 4 | Twilio Account SID | `buy_watcher.js` | yes |

Push protection recorded no bypasses. Rotate anything that ever sat in those files, even if later deleted. Re-run `gitleaks detect --source . --full-history` on a machine with the full clone.

## Actions still open

1. Confirm keys that touched the allowlisted files were rotated, not just hidden.
2. Stripe / PayPal live: `getsincor.com/health` reports both `not_configured`. Set `STRIPE_SECRET_KEY` and PayPal secrets **only** on Railway. Never paste `sk_live` into the repo, a PR, or a chat.
3. Anthropic is marked configured on live. Confirm the key lives in Railway, never disk.
4. `CANONICAL_ADDRESSES.md` still lists deployer `0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a` as temporary. Rotate to a multisig before any vault deposit.
5. `KYA_ADMIN_KEY` gates `POST /v1/quest/seed`, `POST /v1/sadas/publish`, `POST /v1/polyclaw/day`. Header: `X-KYA-Admin`. Fail closed if unset. Rotate if it ever touched history or a paste.
6. `KYA_PRODUCTION_ROOT` is the committed disperse merkle root. Set on Railway after `kya_build_merkle.py`. Never the 48-leaf replica.
7. `sinc_lbp_salt.json` is a CREATE2 salt, not a key. Fine in-repo. Do not add private keys next to it.

## Halt

Do not create a `.env` in this repo or in the App Builder workspace snapshot.
