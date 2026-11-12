# sincor-x402 — Verified x402 + MCP wrapper

**"Pay-and-pray" is the gap.** Coinbase's x402 lets an agent pay USDC to an
endpoint up front — with no escrow, no committed spec, and no guarantee the
seller won't return garbage or time out after taking the funds. `sincor-x402`
wraps any x402 endpoint or MCP tool in the missing piece:

```
request → HTTP 402 (escrow terms + committed spec hash)
        → payment verified → funds held in escrow
        → work executes (timeout-bounded)
        → result verified against the committed spec  (SIGIL-style commitment)
        → SEAL release on pass · refund on fail or timeout
```

## Honesty first — what is real and what is not

* **Live on getsincor.com today:** CNP task bidding/auctions, pool
  settlement, and KYA, plus the platform's own x402 seller/buyer code this
  package's wire shapes are modelled on.
* **Integration-pilot framing:** SEAL (EIP-712 settlement) and SIGIL
  (verification) are the platform concepts this wrapper's interfaces are
  shaped around. This package implements them as **pluggable interfaces
  with labelled local reference implementations** (`LocalReferencePlatform`,
  `LocalFacilitator`, `ReferenceVerifier`). They do NOT talk to a live
  SIGIL service or a production escrow endpoint — none is wired yet.
* **No SINCOR escrow contract is deployed on Base mainnet.** Nothing in
  this package moves real funds. Do not represent it otherwise.

## Networks & constants

* **Base mainnet:** chain ID **8453**
* **Base Sepolia:** chain ID **84532** — testnet rehearsal only
* **USDC on Base:** `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
* **Live agent card:** https://getsincor.com/.well-known/agent-card.json

Nothing in this package is deployed on either network — these are the
chain IDs and contract constants the wire shapes and the Hub engine are
written against, not live deployments.

## Install

```bash
pip install sincor-x402            # core: zero dependencies, stdlib only
pip install "sincor-x402[fastapi]" # + the FastAPI adapter
```

## Server: wrap an endpoint (2 lines)

```python
from sincor_x402 import VerifiedEndpoint, PaymentRequirement
endpoint = VerifiedEndpoint(summarize, requirement=REQ, spec={"required_keys": ["summary"]})
```

`REQ` is your price/terms (`PaymentRequirement(scheme="exact", network="base",
max_amount_required="10000", asset=<USDC on Base>, pay_to=<your address>,
max_timeout_seconds=60)`). Feed requests to `endpoint.handle(headers=..., kwargs=...)`:
no payment header → a 402 challenge carrying your `accepts[]` + escrow terms;
a paid request runs the full escrow → execute → verify → release/refund loop.

With FastAPI (`sincor-x402[fastapi]`):

```python
from sincor_x402.fastapi_adapter import sincor_verified
@app.post("/summarize")
@sincor_verified(requirement=REQ, spec={"required_keys": ["summary"]})
def summarize(payload: dict): ...
```

## MCP: wrap a tool (2 lines)

```python
from sincor_x402 import verified_tool
@verified_tool(requirement=REQ, spec={"required_keys": ["answer"]})
def lookup(symbol: str): ...
```

No MCP SDK is imported or required: the wrapped tool exposes
`tool_definition()` (an MCP `tools/list`-shaped descriptor) and
`call_paid(payment_header=..., **kwargs)` for your host's `tools/call`
handler, plus a best-effort `register(server)` for FastMCP-style hosts.

## Buyer: pay a wrapped endpoint — exactly once per instruction

```python
from sincor_x402 import VerifiedBuyer
buyer = VerifiedBuyer(wallet_address, signer_fn)  # signer is injected; keys never enter this package
out = buyer.fetch(url, instruction_id="job-123", body={"q": "..."})
```

* **Instruction-scoped idempotency:** `instruction_id` is your unit of work.
  Re-calling `fetch` with the same id and the same request returns the
  recorded outcome and **never signs a second payment** — a historical x402
  retry bug produced 40 payments for one request; the journal + same-payload
  resend design here makes that impossible (covered by a 40× retry-storm test).
  Reusing an id with a *different* request raises `IdempotencyConflict`.
* **Per-payee velocity cap:** distinct instructions to one `payTo` are
  capped by count and total amount per sliding window (`VelocityCap`),
  checked **before** signing, so a capped attempt creates no payment.
* **No blind retries:** an ambiguous paid response raises `AmbiguousPayment`
  and locks the instruction rather than risking a double-pay.
* **Per-call cap:** challenges above `max_payment_usd` are refused unsigned.

## Server safety: the accepts[] hedge

A client echoes the terms it "accepted" inside its payment payload — and a
buggy or malicious client can rewrite them (lower price, swapped `payTo`).
This server **never trusts the client's copy**: it matches the claim against
its own retained `accepts[]` entry on scheme, network, amount, asset,
payTo, and timeout, rejects mismatches before the facilitator is touched,
and passes **its own** `PaymentRequirement` object to the facilitator.
Price-rewrite and payTo-swap attacks have dedicated negative tests.

## Hub settlement engine (`sincor_x402.hub_engine`)

A client for the **SincorSettlementHub** contract interface from the
2026-11-11 spec drop — the on-chain counterpart to the wrapper above.
Requires the `hub` extra: `pip install "sincor-x402[hub]"`.

* **Buyer side:** `create_buyer_seal_header(...)` signs an EIP-712 SEAL
  envelope (`SealAuthorization` under domain `SINCOR_SEAL` / `"1"`, nonce
  read fresh from the Hub's `nonces(buyer)`) and returns it as a base64
  X-PAYMENT header, scheme `sincor-seal-v1`. The buyer's key stays with
  the injected signer callable; the package never holds keys.
* **Settler side:** `verify_and_settle(...)` runs the package's
  `Verifier` seam against the committed spec first, then submits
  `verifyAndSettleSeal` through an injected relayer signer and parses the
  `SealSettled` event (gross / net payout / protocol fee).
* **SIGIL canon on this path:** `keccak256(taskId ‖ canonical-JSON
  output)` — the EVM-native hash, matching the Hub's own typehashes.

### Status: the Hub is NOT deployed

The Hub contract exists today only as an unverified spec-drop draft;
**deploying it is a founder gate** and nothing here deploys anything. The
engine is proven end-to-end against a *local* test chain
(`tests/test_hub_engine.py`): a vendored test copy of the Hub + mock USDC
on eth-tester, Python-signed SEALs settling through the real Solidity
`ecrecover` path.

### Setup step buyers must not skip: the USDC allowance

The SEAL signature alone moves **nothing** — the Hub pulls funds with
ERC-20 `transferFrom` at settlement time, and nothing is escrowed at
authorization time. Before a buyer's first settlement, the buyer must
`approve` the Hub on the USDC contract for at least the seal amount. The
engine preflights this and raises `AllowanceInsufficient` naming the exact
approval step instead of letting the settlement revert on-chain.

### Trust model — stated plainly

The Hub **does not verify work output**: it records whatever
`sigilCommitment` the submitter hands it, and never checks the delivered
output against the seal's `expectedSchemaHash` on-chain. On-chain
`isTaskVerified` therefore proves only that a settlement was recorded
with that commitment. The enforcement layer is this engine, off-chain:
it recomputes the SIGIL commitment from the delivered result, requires
the seal's `expectedSchemaHash` to equal the committed spec hash, runs
the `Verifier`, and **refuses to submit when verification fails — the Hub
is never called**. Anyone submitting directly to the Hub bypasses that
enforcement; the Hub alone is not a verification system.

### Fee canon

The locked SINCOR platform fee is **5% (500 bps)**. The spec-drop draft
hardcodes 100 bps — a known defect. The vendored test copy under
`tests/fixtures/` sets `protocolFeeBps = 500` (change recorded in its
header), and the chain tests assert the canon split on-chain: provider
95% / treasury 5%.

Lower effective rates exist only as negotiated enterprise/volume
arrangements applied at the platform layer — never as a different code
or docs default. The default in this package's code, tests, and docs is
500 bps.

## Plugging in real SIGIL / SEAL / facilitator services

Three protocols, three seams — implement one method each and pass the
object in (`verifier=`, `platform=`, `facilitator=`):

* `Verifier.verify(*, spec, spec_hash, result)` — judge a delivered result
  against the spec committed before payment; fail closed if
  `sha256(spec) != spec_hash`. A SIGIL service client drops in here.
* `PlatformClient.open_escrow / release / refund` — the SEAL settlement
  surface. `LocalReferencePlatform` is an in-memory ledger with
  settle-exactly-once semantics; a production client implements the same
  protocol against the real platform when its endpoint ships.
* `FacilitatorClient.verify_and_hold(*, payment_payload, requirement)` —
  x402 facilitator verification (e.g. Coinbase CDP). `LocalFacilitator`
  checks structure only; it does not verify EIP-712 signatures or touch a
  chain, and is labelled accordingly.

## Tests

```bash
pip install pytest && python -m pytest   # 46 tests, no network
```

Full loop with mocked platform + mocked facilitator: good result →
release; garbage → refund; timeout → refund; exception → refund; 40× retry
storms → exactly one payment; accepts[] rewrite attacks → rejected. Plus
the Hub engine's cross-implementation suite (needs the `dev` extra:
web3 + eth-tester + py-solc-x): Python-signed SEALs settling through the
compiled Solidity Hub on a local chain — canon 95/5 fee split,
`isTaskVerified`, and the Hub's own reverts (wrong signer → 
`InvalidSealSignature`, expired → `SealExpired`, replay → `InvalidNonce`),
with the engine-refusal and allowance-preflight paths proven to never
reach the chain.

## PyPI publish checklist (founder gate — do NOT publish without sign-off)

- [ ] Founder approves publishing under the `sincor-x402` name
- [ ] Confirm PyPI name availability / claim the name (and `sincor-mcp` if used)
- [ ] Bump `version` in `pyproject.toml`; tag the release commit
- [ ] `python -m build` → inspect sdist + wheel contents (no stray files)
- [ ] Upload to TestPyPI first; install from TestPyPI in a clean venv; run tests
- [ ] Verify README renders on the PyPI project page (honesty section intact)
- [ ] Publish to PyPI via a project-scoped API token stored in the vault —
      never a password, never a token in the repo
- [ ] Post-publish: `pip install sincor-x402` in a clean venv and smoke-test
      the 2-line server + MCP examples from this README

## License

Apache-2.0 (declared in `pyproject.toml`).
