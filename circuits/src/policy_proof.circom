pragma circom 2.1.9;

// SINCOR2 Pillar 2.2 — TOOLCHAIN SPIKE prototype (NOT the full LOCKED v1.0 circuit).
//
// Implements exactly two constraints from the LOCKED v1.0 formalization doc
// (~/workspace/your_files/zk-circuit-formalization-2026-11-01.md):
//   (a) HashConsistency  — Poseidon(payload) == payload_commitment (public)
//   (b) EvaluatePolicy   — payload.amount <= limit (limit public, amount private),
//                          the one numeric range rule in scope for the spike.
//
// Payload representation (spike simplification, documented in SPIKE-REPORT.md):
// the raw action payload is modeled as a fixed vector of 4 field elements:
//   payload[0] = amount        (the policy-checked numeric field)
//   payload[1] = action_tag    (opaque field element standing in for action type)
//   payload[2] = counterparty  (opaque field element standing in for recipient hash)
//   payload[3] = nonce         (opaque field element standing in for payload nonce)
// Full v1.0 must define a canonical, versioned encoding of real envelope payloads
// into field elements; that encoding is a full-build task, not a spike task.
//
// Signature verification is OFF-circuit by founder ruling (Pillar 2.1).

include "circomlib/circuits/poseidon.circom";
include "circomlib/circuits/comparators.circom";
include "circomlib/circuits/bitify.circom";

template PolicyProof() {
    // ---- Private inputs (witness) ----
    signal input payload[4];          // payload[0] is the amount

    // ---- Public inputs ----
    signal input payload_commitment;  // Poseidon(payload[0..3])
    signal input limit;               // policy threshold (public per LOCKED doc spike scope)

    // (a) HashConsistency: Poseidon over the 4-element payload must equal the
    // public commitment, so the prover cannot swap payloads post-execution.
    component hasher = Poseidon(4);
    for (var i = 0; i < 4; i++) {
        hasher.inputs[i] <== payload[i];
    }
    hasher.out === payload_commitment;

    // (b) EvaluatePolicy: amount <= limit.
    // Range-check both values into 64 bits first so the comparison cannot be
    // gamed with field wraparound (a value near the BN254 prime would otherwise
    // "compare" as small/large incorrectly in LessEqThan's bit decomposition).
    component amountBits = Num2Bits(64);
    amountBits.in <== payload[0];
    component limitBits = Num2Bits(64);
    limitBits.in <== limit;

    component leq = LessEqThan(64);
    leq.in[0] <== payload[0];
    leq.in[1] <== limit;
    leq.out === 1;
}

component main {public [payload_commitment, limit]} = PolicyProof();
