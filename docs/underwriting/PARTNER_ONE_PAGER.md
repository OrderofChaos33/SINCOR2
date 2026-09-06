# Agent Underwriting

Spend envelopes for machine treasuries.

**Problem.** Static caps treat agent spend like a card swipe. Agents fail over trajectories — loops, new counterparties, rising error rates. Issuers and operators cannot underwrite unattended float.

**Object.** An operator signs an Intent Mandate (max notional, skills, payees, TTL, kill switch). A Temporal Optimization pass scores futures and issues a Spend Envelope. A facilitator outside the model is the only component allowed to move value. Settlement is x402. The same envelope can open a simulated ledger, USDC on Base, or a licensed mint tap later.

**Controls.** CIP attaches to the human/operator. Agents are authorized users. Append-only audit. Freeze and clawback. Fail closed if the path set is empty.

**Not this.** Not a public ticker. Not permissionless issuance. Not yield to holders.

**Ask.** Design-partner slot and, when ready, sandbox keys for closed-loop mint behind the same envelope.
