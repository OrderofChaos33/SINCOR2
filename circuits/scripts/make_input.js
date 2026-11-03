// Build a circuit input JSON with a correctly computed Poseidon commitment.
// Usage: node scripts/make_input.js <amount> <limit> <out.json>
// Payload layout (see src/policy_proof.circom): [amount, action_tag, counterparty, nonce]
const { buildPoseidon } = require("circomlibjs");
const fs = require("fs");

(async () => {
  const [amount, limit, out] = process.argv.slice(2);
  if (!amount || !limit || !out) {
    console.error("usage: node scripts/make_input.js <amount> <limit> <out.json>");
    process.exit(1);
  }
  const payload = [BigInt(amount), 7n, 123456789n, 42n];
  const poseidon = await buildPoseidon();
  const F = poseidon.F;
  const commitment = F.toString(poseidon(payload));
  const input = {
    payload: payload.map((x) => x.toString()),
    payload_commitment: commitment,
    limit: limit.toString(),
  };
  fs.writeFileSync(out, JSON.stringify(input, null, 2));
  console.log(`wrote ${out}: amount=${amount} limit=${limit} payload_commitment=${commitment}`);
})();
