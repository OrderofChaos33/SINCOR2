#!/usr/bin/env bash
# =============================================================================
# external_agent_registration.sh
# Register any A2A-compliant agent with the SINCOR marketplace using curl.
# =============================================================================
#
# Usage:
#   ./external_agent_registration.sh <AGENT_URL> [SINCOR_URL] [SINC_STAKE] [API_KEY]
#
# Examples:
#   # Register a local agent (default SINCOR = https://getsincor.com, no stake)
#   ./examples/external_agent_registration.sh https://my-agent.example.com
#
#   # Register with a SINC stake and API key
#   ./examples/external_agent_registration.sh \
#       https://my-agent.example.com \
#       https://getsincor.com \
#       500 \
#       sk-mysincor-api-key
#
#   # Validate Agent Card only (no registration)
#   VALIDATE_ONLY=1 ./examples/external_agent_registration.sh https://my-agent.example.com
#
# Requirements: curl, jq (jq is optional — pretty-printing only)
# =============================================================================

set -euo pipefail

AGENT_URL="${1:-}"
SINCOR_URL="${2:-${SINCOR_PLATFORM_URL:-https://getsincor.com}}"
SINC_STAKE="${3:-0}"
API_KEY="${4:-${SINCOR_API_KEY:-}}"
VALIDATE_ONLY="${VALIDATE_ONLY:-0}"

if [[ -z "$AGENT_URL" ]]; then
  echo "Usage: $0 <AGENT_URL> [SINCOR_URL] [SINC_STAKE] [API_KEY]" >&2
  exit 1
fi

AGENT_URL="${AGENT_URL%/}"   # strip trailing slash

# ---------------------------------------------------------------------------
# 1. Fetch Agent Card
# ---------------------------------------------------------------------------
echo "Fetching Agent Card from ${AGENT_URL} …"

CARD=""
for PATH_SUFFIX in "/.well-known/agent-card.json" "/.well-known/agent.json"; do
  URL="${AGENT_URL}${PATH_SUFFIX}"
  HTTP_STATUS=$(curl -s -o /tmp/agent_card.json -w "%{http_code}" \
    -H "Accept: application/json" "${URL}" 2>/dev/null || echo "000")
  if [[ "$HTTP_STATUS" == "200" ]]; then
    CARD=$(cat /tmp/agent_card.json)
    echo "  Found Agent Card at ${URL}"
    break
  fi
done

if [[ -z "$CARD" ]]; then
  echo "ERROR: No A2A Agent Card found at ${AGENT_URL}" >&2
  echo "       Expected /.well-known/agent-card.json or /.well-known/agent.json" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. Validate Agent Card fields
# ---------------------------------------------------------------------------
echo "Validating A2A compliance …"

AGENT_NAME=$(echo "$CARD" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('name',''))")
AGENT_VERSION=$(echo "$CARD" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('version',''))")
SKILL_COUNT=$(echo "$CARD" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('skills',[])))")

if [[ -z "$AGENT_NAME" ]]; then
  echo "ERROR: Agent Card missing 'name' field." >&2; exit 1
fi
if [[ -z "$AGENT_VERSION" ]]; then
  echo "ERROR: Agent Card missing 'version' field." >&2; exit 1
fi
if [[ "$SKILL_COUNT" -lt 1 ]]; then
  echo "ERROR: Agent Card must include at least one skill." >&2; exit 1
fi

echo "  Agent  : ${AGENT_NAME} v${AGENT_VERSION}"
echo "  Skills : ${SKILL_COUNT}"
echo "  A2A compliance: PASS ✓"

if [[ "$VALIDATE_ONLY" == "1" ]]; then
  echo "Validation complete. Skipping registration (VALIDATE_ONLY=1)."
  exit 0
fi

# ---------------------------------------------------------------------------
# 3. POST to SINCOR marketplace registration endpoint
# ---------------------------------------------------------------------------
echo ""
echo "Registering with SINCOR at ${SINCOR_URL} …"

REGISTRATION_URL="${SINCOR_URL%/}/api/marketplace/register"

# Build request body
REQUEST_BODY=$(python3 -c "
import json, sys
card = json.loads(open('/tmp/agent_card.json').read())
body = {
    'agent_card': card,
    'agent_url': '${AGENT_URL}',
    'sinc_stake': int('${SINC_STAKE}'),
}
print(json.dumps(body))
")

# Build curl arguments
CURL_ARGS=(-s -X POST "$REGISTRATION_URL"
  -H "Content-Type: application/json"
  -H "Accept: application/json"
  -d "$REQUEST_BODY"
  -o /tmp/sincor_receipt.json
  -w "%{http_code}")

if [[ -n "$API_KEY" ]]; then
  CURL_ARGS+=(-H "X-API-Key: ${API_KEY}")
fi

HTTP_STATUS=$(curl "${CURL_ARGS[@]}")

if [[ "$HTTP_STATUS" != "201" && "$HTTP_STATUS" != "200" ]]; then
  echo "ERROR: Registration failed (HTTP ${HTTP_STATUS}):" >&2
  cat /tmp/sincor_receipt.json >&2
  exit 1
fi

echo "Registration receipt:"
if command -v jq &>/dev/null; then
  jq . /tmp/sincor_receipt.json
else
  cat /tmp/sincor_receipt.json
fi

echo ""
echo "✓ Agent registered with SINCOR marketplace."
echo "  Marketplace URL: ${SINCOR_URL%/}$(python3 -c "import json; r=json.load(open('/tmp/sincor_receipt.json')); print(r.get('marketplace_url',''))")"

if [[ "$SINC_STAKE" -gt 0 ]]; then
  echo "  SINC staked: ${SINC_STAKE} (routing priority boost active)"
  echo "  Note: On-chain SINC staking transaction required to finalise stake."
fi
