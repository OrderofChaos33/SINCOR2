#!/usr/bin/env bash
# Local or live. Default live host.
set -euo pipefail
HOST="${HOST:-https://getsincor.com}"

echo "== health"
curl -sS "$HOST/v1/underwriting/health" || true
echo

echo "== register"
curl -sS -X POST "$HOST/v1/agents/register" \
  -H 'content-type: application/json' \
  -d '{"wallet":"0x1111111111111111111111111111111111111111","principal":"0x1111111111111111111111111111111111111111","agent_id":"ext-demo","erc8004_id":"pending"}'
echo

echo "== underwrite"
curl -sS -X POST "$HOST/v1/mandates/underwrite" \
  -H 'content-type: application/json' \
  -d '{"agent_id":"ext-demo","skill":"competitor-intel","notional_axm":1,"cap_axm":5}'
echo

echo "== JSON-RPC message/send (not REST /message/send)"
curl -sS -X POST "$HOST/api/a2a" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"skillId":"competitor-intel","message":{"role":"user","parts":[{"type":"text","text":"quick SWOT"}]}}}'
echo
