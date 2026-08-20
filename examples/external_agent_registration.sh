#!/usr/bin/env bash
# End-to-end external agent registration + discovery smoke against production.
# Usage: bash examples/external_agent_registration.sh
set -euo pipefail

BASE="${PLATFORM_URL:-https://getsincor.com}"

echo "=== 1. Agent Card (v1.0.1 preferred) ==="
CARD_CODE=$(curl -sS -o /tmp/sincor-card.json -w "%{http_code}" "$BASE/.well-known/agent-card.json" || true)
echo "HTTP $CARD_CODE"
if [[ "$CARD_CODE" == "200" ]]; then
  python3 -c "import json; d=json.load(open('/tmp/sincor-card.json')); print('name:', d.get('name')); print('skills:', len(d.get('skills',[]))); print('interfaces:', d.get('supportedInterfaces'))"
else
  echo "agent-card.json not live — falling back to legacy agent.json"
  curl -sS "$BASE/.well-known/agent.json" | head -c 400
  echo
fi

echo ""
echo "=== 2. Skill catalogue ==="
curl -sS "$BASE/api/a2a/agents" | head -c 500 || echo "(endpoint offline until A2ARouter registered)"
echo

echo ""
echo "=== 3. Quote lead-enrichment ==="
curl -sS "$BASE/api/a2a/quote?skill_id=lead-enrichment&caller_id=external-smoke" | head -c 600 || echo "(quote offline)"
echo

echo ""
echo "=== 4. Marketplace register (optional — requires marketplace bootstrap) ==="
curl -sS -X POST "$BASE/api/marketplace/register" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_card": {
      "name": "External Smoke Agent",
      "description": "CI smoke registration",
      "version": "0.1.0",
      "skills": [{"id": "ping", "name": "Ping", "description": "Health ping"}]
    },
    "agent_url": "https://example.com/agent",
    "sinc_stake": 0
  }' | head -c 400 || echo "(register offline or gated)"
echo

echo ""
echo "=== 5. Health / Base RPC ==="
curl -sS "$BASE/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print('base_rpc:', d.get('checks',{}).get('base_rpc', d))" 2>/dev/null || curl -sS "$BASE/health" | head -c 300
echo
echo "Done."
