#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
export UNDERWRITE_DATA_DIR="${UNDERWRITE_DATA_DIR:-$ROOT/data/underwriting}"
export UNDERWRITE_TAP="${UNDERWRITE_TAP:-ledger_sim}"
rm -rf "$UNDERWRITE_DATA_DIR"
mkdir -p "$UNDERWRITE_DATA_DIR"

python3 - <<'PY'
from sincor2.underwriting.runtime import boot
from sincor2.underwriting.types import AuthorizeRequest
from sincor2.underwriting.x402_seller import handle_echo

rt = boot()
m = rt.mandates.issue(
    operator_id="court",
    controller_wallet="0x1111111111111111111111111111111111111111",
    agent_id="E-altair-04",
    max_notional_usd="25.00",
    max_single_tx_usd="5.00",
    allowed_skills=["underwrite.echo"],
    allowed_payees=["sincor:skill:underwrite.echo"],
)
print(f"mandate   {m.mandate_id}  max=25.00 single=5.00 agent={m.agent_id}")
env = rt.envelopes.request(
    m,
    requested_usd="5.00",
    skill_id="underwrite.echo",
    payee="sincor:skill:underwrite.echo",
)
print(f"envelope  {env.envelope_id}  amount={env.amount_usd} ttl={env.ttl_seconds} reason={env.reason_codes[0]} toa={env.toa_run_id}")
for i in (1, 2):
    code, body = handle_echo(rt.facilitator, envelope_id=env.envelope_id, payment_sig=env.envelope_id)
    print(f"pay#{i}     0.05    remaining={body.get('remaining_usd')}  {code}")
r = rt.facilitator.authorize(AuthorizeRequest(env.envelope_id, "0xevil", "0.05", "underwrite.echo"))
print(f"pay#bad   0.05    payee=0xevil    {403 if not r.allowed else 200} {r.reason}")
rt.mandates.revoke(m.mandate_id, "operator")
print("kill      ok")
r = rt.facilitator.authorize(AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "0.05", "underwrite.echo"))
print(f"pay#3     0.05                    {403 if not r.allowed else 200} {r.reason}")
lines = list(rt.store.iter_audit())
print(f"audit     {rt.store.root}  lines={len(lines)}")
print("viewer    python -m sincor2.underwriting.api   → http://127.0.0.1:8787/underwrite/demo")
PY
