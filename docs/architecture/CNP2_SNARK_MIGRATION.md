# CNP-2 → Real SNARKs: Migration Path

Status: **design document only**. CNP-2 ships with a commitment-based
(issuer-signed) attestation scheme in `src/sincor2/kya/zk_attestations.py`.
No SNARK dependencies (circom, gnark, snarkjs, arkworks) are added or
required. This document records how the current scheme maps onto a true
zero-knowledge proof system when the founder approves one, so the migration
is a substitution of the proof primitive — not a redesign of the protocol.

## What CNP-2 proves today (honest accounting)

`ZKAttestationIssuer.issue_tier_attestation` / `issue_capability_attestation`
produce a signed envelope containing:

* `claim` — public statement: `{"kind": "kya_tier", "tier": "T3"}` or
  `{"kind": "kya_capability", "capability": "defi-arbitrage"}`.
* `nullifier = keccak256(issuer_salt || agent_id || claim_kind || claim_value)`
  — binds the attestation to the prover without naming them.
* `commitment = keccak256(nullifier || digest || issued_at)` — anchored in
  the issuer's append-only ledger; the opening (agent_id, salt, claim) is
  revealed only for dispute/audit.
* `signature` — EIP-191 (secp256k1) over the canonical digest.

Trust assumptions today: (1) the issuer's key is uncompromised;
(2) the issuer's revocation source is reachable at verify time
(fail-closed otherwise); (3) the issuer does not lie about registry state
at issuance. A malicious or compromised issuer can forge attestations.

## Target SNARK construction

Replace the issuer signature with a proof that the *registry state itself*
entails the claim, so any verifier can check the proof without trusting an
issuer key.

### Circuit inputs

* **Private inputs (witness):** the agent's registry record (agent_id,
  status, verified flag, stake_axm_wei, capabilities, record_hash), and the
  Merkle path proving the record's inclusion in a published registry-state
  root.
* **Public inputs:** `registry_root` (a Merkle root over canonicalized
  registry records, published per epoch), `claim` (tier or capability),
  `nullifier` (as today), `issued_at`, `expires_at`.

### Circuit constraints

1. Recompute `leaf = keccak256(canonical_record)` and verify the Merkle path
   against `registry_root`.
2. Enforce the tier predicate over the private record:
   `tier(record) >= claimed_tier` using the same `tier_of` rules as today
   (T3 requires `verified && status == "verified"`; T2 requires
   `stake_axm_wei >= MIN_STAKE_WEI`; revoked records fail all tiers).
   Or enforce `capability ∈ record.capabilities` for capability claims.
3. Enforce `nullifier == keccak256(domain || agent_id || claim_kind || claim_value)`
   with the domain constant fixed in-circuit (nullifier unlinkability without
   an issuer salt).
4. Range-check `issued_at <= now < expires_at` against a public timestamp
   bound.

### What the SNARK removes and what it keeps

* Removed: issuer key, issuer salt, revocation callback availability.
  Non-revocation becomes a proof over a *fresh* `registry_root`: the verifier
  accepts only roots published within the attestation's validity window, and
  a revoked record simply cannot satisfy the tier predicate against a current
  root. "Fresh root" replaces "live revocation check".
* Kept: the attestation envelope schema (`attestation_id`, `claim`,
  `nullifier`, `issuer` → becomes `registry_root` + proof system id,
  `issued_at`, `expires_at`, `commitment`), the fail-closed verifier
  discipline, the commitment+reveal ledger for dispute audit, and the
  deterministic-nullifier revocation semantics (a per-epoch nullifier
  blocklist can still be published for explicit revocation events).

## Dependency decision (founder-gated)

Candidate stacks: circom + snarkjs (Groth16), gnark (Go), arkworks (Rust).
None is vendored here. Selection criteria when the time comes:

* audited proving system with a trusted-setup ceremony the project can
  participate in (or a transparent STARK-based alternative to avoid setup);
* keccak256-friendly constraint cost (the registry uses keccak throughout;
  a Poseidon-based leaf hash would require a parallel registry root —
  flagged as a migration cost, not a blocker);
* a Python-callable prover/verifier (snarkjs via node, or a Rust/Python
  binding) so the existing Python verifier path stays intact.

## Migration steps (when approved)

1. Add a `registry_root` publisher: per-epoch Merkle root over
   canonicalized records (`record_hash` leaves), published with the epoch.
2. Freeze the circuit (constraints above) and run the trusted setup.
3. Add `prove_snark(...)` / `verify_snark(...)` behind the same
   `verify_attestation` entry point, keyed on `proof_type` in the envelope
   (`"commitment"` today, `"groth16"` after).
4. Dual-run: accept both proof types during a transition window; the
   commitment scheme remains the fallback for agents without prover access.
5. Retire the issuer-signature path only after the founder signs off on
   circuit audit results.

Until then, the commitment scheme in `zk_attestations.py` is the production
primitive, and this file is the plan — not the implementation.
