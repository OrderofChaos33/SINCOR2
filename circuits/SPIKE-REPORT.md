# Pillar 2.2 ZK Toolchain Spike — Report

**Scope:** feasibility spike ONLY for the LOCKED v1.0 circuit formalization
(`~/workspace/your_files/zk-circuit-formalization-2026-11-01.md`). Not the full circuit.
**Branch:** `xioix/zk-circuit-spike` (base `735e5cb9`) · **Date:** 2026-11-01
**Prototype implements exactly two LOCKED constraints:**
(a) **HashConsistency** — `Poseidon(payload) == payload_commitment` (public), and
(b) **EvaluatePolicy (numeric)** — `payload.amount <= limit`, amount private, limit public,
both 64-bit range-checked first so field wraparound cannot game the comparison.
Signature verification is off-circuit by founder ruling (Pillar 2.1) — none in the circuit.
Payload is modeled as a fixed 4-field vector `[amount, action_tag, counterparty, nonce]`
(see Risks — the real encoding is a full-build decision).

## Toolchain verdict: **circom + snarkjs (PLONK)** — it ran end-to-end in this sandbox

| Tool | Version | Install path | Result |
|---|---|---|---|
| circom | 2.1.9 | official GitHub release binary (`circom-linux-amd64`) into `circuits/work/` | ✅ runs |
| snarkjs | 0.7.5 | `npm install` (registry via sandbox proxy) | ✅ runs |
| circomlib / circomlibjs | 2.0.5 / 0.1.7 | `npm install` | ✅ Poseidon + comparators used |
| Node | 24.22.1 | preinstalled | ✅ |
| Noir `nargo` | 1.0.0 | noirup script → GitHub release tarball into `~/.nargo` | ⚠️ runs, with quirks (below) |
| Barretenberg `bb` | 1.0.0 | bbup (`bbup -v 1.0.0`; auto-version resolution failed) into `~/.bb` | ⚠️ binary runs, with quirks (below) |
| Rust/cargo | — | not preinstalled; not needed for the circom path | — |

### Noir evaluation (attempted seriously; not chosen)
- `noirup` **reported failure** — its tarball extraction hit `tar: Cannot change ownership to uid 1001: Operation not permitted` in this sandbox — yet the binaries extracted anyway and `nargo --version` reports `1.0.0`. Same tar-ownership quirk for `bb` 1.0.0 (extracted, `--version` runs).
- `bbup` without an explicit version failed: "No version specified and couldn't determine version from noir"; `bbup -v 1.0.0` worked.
- **Blocker:** in nargo 1.0.0's stdlib, the LOCKED spec's hash does not resolve: `std::hash::poseidon::bn254::hash_4` → `error: Could not resolve 'poseidon' in path` (the BN254 Poseidon v1 API is gone from std), and `std::hash::poseidon2::Poseidon2` also failed to resolve in this build (`Could not resolve 'Poseidon2' in path`). A comparable Poseidon circuit in Noir would need an external poseidon library dependency or a spec change to Poseidon2 — archaeology the spike did not need, because circomlib's Poseidon is the reference implementation the LOCKED doc assumes.
- Noir remains attractive later (native u64 comparisons, better ergonomics) **if** the hash-function question is settled; it is not the pragmatic v1 path on this evidence.

## Hard numbers (circom + snarkjs PLONK, this machine, single clean run — `scripts/run_spike.sh`)

| Metric | Value |
|---|---|
| R1CS constraints | **489** (non-linear; 492 wires, 4 private + 2 public inputs) |
| PLONK constraints after snarkjs conversion | **7,425** (domain 2^13 — power-12 ptau is rejected: "circuit too big for this power of tau ceremony") |
| Compile time | 1,883 ms |
| Powers-of-tau (power 13, dev ceremony, pure-JS snarkjs) | 385,894 ms (~6.4 min — one-time, reusable, and the strongest argument for a native prover stack; see Risks) |
| PLONK setup time | 7,171 ms |
| Prover time (valid witness) | 13,587 ms |
| Verifier time | 967 ms |
| Proof size | **768 bytes on-chain** (24 × 32-byte calldata words, via `zkey export soliditycalldata`; snarkjs `proof.json` = 2,251 bytes pretty-printed / 2,100 minified) |
| Public inputs | 2 (payload_commitment, limit) + 93-byte `public.json` |

## Proof results (evidence: `circuits/evidence/spike-run-full.txt`, committed; build dir is gitignored)

- **Valid witness** (amount 7500 ≤ limit 10000): witness generated, PLONK proof generated,
  `snarkjs plonk verify` → **OK!**
- **Negative witness** (amount 15000 > limit 10000, commitment honestly recomputed for that
  payload): witness generation **fails** — `Error: Assert Failed … in template PolicyProof
  line: 54` (the `leq.out === 1` line, i.e. EvaluatePolicy itself) — rc=1. No proof can be
  produced for an over-limit payload.

## Top 3 risks / blockers for the full LOCKED v1.0 constraint list

1. **Canonical payload encoding is undefined — and it is the real circuit.** This spike hashes a
   toy 4-field vector. The full build must define a versioned, canonical mapping from real
   envelope payloads (variable-length action data, strings, nested params) into field
   elements, and PolicyAuthenticity / SigBinding / NullifierCorrectness all hash structured
   objects the same way. Get the encoding wrong and proofs verify against the wrong thing.
   This encoding spec should be written (and founder-locked) before circuit code.
2. **snarkjs (pure JS) performance will not carry production proving.** The dev ptau at
   power 13 took minutes in JS and the 489-constraint prototype already needed domain 2^13
   after PLONK conversion blow-up (489 → 7,425). The full constraint list (two more Poseidons
   for PolicyAuthenticity + Nullifier, TemporalBound ranges, SigBinding) will grow this
   several-fold. Production proving should evaluate a native prover (e.g. rapidsnark) and
   benchmark again before committing to prover infrastructure; the universal PLONK setup
   also needs a real multi-party ceremony for production — the entropy used here is a
   throwaway dev string and the ptau/zkey from this spike must NEVER be used in production.
3. **Poseidon parameter/implementation consistency across the stack.** Commitments computed
   off-circuit (envelope layer, TypeScript/Python) must be bit-identical to circomlib's
   in-circuit Poseidon, and to any future on-chain verifier's expectations. The spike
   verified circomlibjs ↔ circomlib agree (the witness only generates if they do), but the
   Python envelope layer (Phase 1/2 code) has no Poseidon yet — a cross-implementation
   test-vector suite (fixed inputs → fixed commitments, checked in Python, TS, and circom)
   is a required early deliverable of the full build. (This is also why Noir's missing
   std Poseidon matters: a third implementation would have needed the same proof.)

## Reproduce

```bash
cd circuits
# circom 2.1.9 binary at ./work/circom-linux (or set CIRCOM=/path/to/circom)
npm install
./scripts/run_spike.sh   # full flow incl. dev ptau; proof + verify + negative witness
```

**Honesty note:** every number above comes from tools that actually ran in this sandbox
(log: `evidence/spike-run-full.txt`, committed with this report). No production keys,
ceremonies, or claims attach to this spike; the ptau/zkey are dev artifacts in the
gitignored `build/` directory.
