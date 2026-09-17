# Adapters

SINCOR adapters now emit Base-native commerce metadata by default:

- `wallet` + `chain_id` for Base mainnet discovery
- `paymentMethods` advertising x402 settlement on Base
- `agentKit` metadata for Coinbase AgentKit wallet creation and action providers
- `baseCommerce` session-policy hints for sponsored/gasless flows

For hosted agents, set `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, and `AGENTKIT_WALLET_SECRET` in the runtime that serves the adapter. If no agent-specific wallet env var is set, adapters fall back to the configured treasury wallet so cards still advertise a valid Base pay-to address.
