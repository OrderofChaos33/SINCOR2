#!/usr/bin/env bash
# SINCOR2 Pillar 2.2 spike: compile -> PLONK setup -> valid proof -> verify -> negative witness.
# All artifacts land in circuits/build/ (gitignored). Timing uses date +%s%N because
# /usr/bin/time is not present in this sandbox.
#
# Toolchain (installed per SPIKE-REPORT.md):
#   CIRCOM env var or ./work/circom-linux  (circom 2.1.9 release binary)
#   npm install                            (snarkjs 0.7.5, circomlib 2.0.5, circomlibjs 0.1.7)
#
# NOTE on power: the circuit is 489 R1CS constraints, but snarkjs converts it to
# 7,425 PLONK constraints, so the powers-of-tau domain must be 2^13 (power 12 fails
# with "circuit too big for this power of tau ceremony"). The ptau step is the slow
# part in pure-JS snarkjs (~6-7 min at power 13 on the spike machine); the universal
# ptau is reusable across circuits, and PLONK needs no per-circuit ceremony.
set -u
cd "$(dirname "$0")/.."
CIRCOM=${CIRCOM:-./work/circom-linux}
SNARKJS=./node_modules/.bin/snarkjs
mkdir -p build

ms_now() { date +%s%N; }
elapsed_ms() { echo $(( ($2 - $1) / 1000000 )); }

echo "=== [1/7] compile circuit ==="
t=$(ms_now)
$CIRCOM src/policy_proof.circom --r1cs --wasm --sym -l node_modules -o build >/dev/null 2>&1
echo "compile_ms=$(elapsed_ms $t $(ms_now))"
$SNARKJS r1cs info build/policy_proof.r1cs 2>/dev/null | grep -E "Constraints|Wires|Public Inputs|Private Inputs" || true

echo "=== [2/7] powers of tau (bn128, power 13, dev ceremony — NOT production) ==="
t=$(ms_now)
$SNARKJS powersoftau new bn128 13 build/pot13_0000.ptau -v >/dev/null 2>&1
$SNARKJS powersoftau contribute build/pot13_0000.ptau build/pot13_0001.ptau --name="spike dev contribution" -v -e="sincor spike entropy 2026-11-01" >/dev/null 2>&1
$SNARKJS powersoftau prepare phase2 build/pot13_0001.ptau build/pot13_final.ptau -v >/dev/null 2>&1
echo "ptau_ms=$(elapsed_ms $t $(ms_now))"

echo "=== [3/7] PLONK setup ==="
t=$(ms_now)
$SNARKJS plonk setup build/policy_proof.r1cs build/pot13_final.ptau build/policy_proof_plonk.zkey >/dev/null 2>&1
rc=$?
echo "plonk_setup_rc=$rc plonk_setup_ms=$(elapsed_ms $t $(ms_now))"
if [ $rc -ne 0 ] || [ ! -s build/policy_proof_plonk.zkey ]; then echo "PLONK SETUP FAILED"; exit 1; fi
$SNARKJS zkey export verificationkey build/policy_proof_plonk.zkey build/verification_key.json >/dev/null 2>&1

echo "=== [4/7] valid witness input (amount=7500 <= limit=10000) ==="
node scripts/make_input.js 7500 10000 build/input_valid.json

echo "=== [5/7] PLONK fullprove (valid) ==="
t=$(ms_now)
$SNARKJS plonk fullprove build/input_valid.json build/policy_proof_js/policy_proof.wasm build/policy_proof_plonk.zkey build/proof_valid.json build/public_valid.json >/dev/null 2>&1
rc=$?
echo "prove_rc=$rc prove_ms=$(elapsed_ms $t $(ms_now))"
if [ $rc -ne 0 ]; then echo "VALID PROVE FAILED"; exit 1; fi
echo "proof_json_bytes=$(wc -c < build/proof_valid.json) public_bytes=$(wc -c < build/public_valid.json)"

echo "=== [6/7] verify (valid) ==="
t=$(ms_now)
out=$($SNARKJS plonk verify build/verification_key.json build/public_valid.json build/proof_valid.json 2>&1)
rc=$?
echo "verify_rc=$rc verify_ms=$(elapsed_ms $t $(ms_now))"
echo "$out" | grep -iE "OK|FAIL|ERROR" | head -3 || true
if [ $rc -ne 0 ]; then echo "VALID VERIFY FAILED"; exit 1; fi

echo "=== [7/7] NEGATIVE witness (amount=15000 > limit=10000) must FAIL ==="
node scripts/make_input.js 15000 10000 build/input_invalid.json
t=$(ms_now)
neg_out=$(node build/policy_proof_js/generate_witness.js build/policy_proof_js/policy_proof.wasm build/input_invalid.json build/witness_invalid.wtns 2>&1)
neg_rc=$?
echo "negative_witness_rc=$neg_rc negative_witness_ms=$(elapsed_ms $t $(ms_now))"
echo "$neg_out" | grep -iE "assert|error" | head -3 || true
if [ $neg_rc -eq 0 ]; then
  echo "UNEXPECTED: negative witness generated — attempting proof (must not verify)"
  $SNARKJS plonk prove build/policy_proof_plonk.zkey build/witness_invalid.wtns build/proof_invalid.json build/public_invalid.json >/dev/null 2>&1
  echo "negative_prove_rc=$?"
  exit 2
fi
echo "NEGATIVE RESULT OK: witness generation failed for amount > limit, as required"
echo "=== spike flow done ==="
