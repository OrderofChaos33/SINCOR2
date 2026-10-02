# CNP-9 — MEV-protected private auction routing: threat model

Status: design + offchain implementation (local simulation backends).
No live private-mempool or threshold-decryption integration exists and none
is authorized in this wave — those backends are documented interfaces only,
gated on explicit founder approval.

Scope: the offchain A2A sealed-bid commit/reveal pipeline
(`sincor2/a2a_inbound_market.py`: `commit_bid` / `reveal_bid` /
`close_auction`) plus the CNP-9 routing layer
(`sincor2/mev_protected_routing.py`).

Explicitly OUT of scope:
* `sincor2/defi/mev_capture.py` — that is MEV *capture* as a DeFi product,
  not MEV protection. Different module, different purpose.
* The Solidity `CommitRevealAuction` contracts — separate audit surface;
  the offchain shim deliberately uses a different commitment preimage
  (see `a2a_inbound_market.py` header) and shim hashes must never be
  submitted onchain.
* Post-auction settlement / payout paths (escrow, fee executor).

## 1. What commit/reveal already gives us (grounding)

Commit/reveal provides **bid-value privacy until reveal**. During the
commit phase the platform, observers, and rival bidders see only
`keccak256(price, salt, agentIdHash)` — an opaque 32-byte value. Bid values
become public at reveal, by design: the auction must publish them to
select a winner.

What commit/reveal does NOT give us is **pending-envelope privacy**:
between the moment a bidder broadcasts a reveal and the moment it is
applied, the envelope sits in a public mempool readable by anyone running
a sniffer. That pre-inclusion window is the attack surface CNP-9 closes.

## 2. Assets

| Asset | Sensitivity |
|---|---|
| Bid values before reveal | High — the core sealed-bid property |
| Bid values in *pending* reveals (pre-inclusion) | High — tradeable information |
| Bidder pseudonymity (which wallet bids which skills) | Medium — intent linkage |
| Commit/reveal timing and participation | Medium — strategy signal |
| Task pricing intelligence across sibling auctions | Medium — undercutting fuel |
| Envelope integrity (no forgery, no cross-task replay) | High — auction correctness |

## 3. Actors

* **Honest bidders** — agents bidding via commit/reveal.
* **Poster** — task creator; receives ghost-slash re-auction credit.
* **Platform operator** — runs the Python auction pipeline and the router.
* **Mempool observer / sniper** — watches pending transactions, acts on
  what they see before inclusion. The primary adversary in this model.
* **Colluding bidders** — share information out-of-band; see §6.
* **Future relay operator** — operator of a live private mempool or
  threshold-decryption committee; trusted party, see §7.

## 4. Who sees what, when

### Commit phase (commit window open)

| Observer | Public path | CNP-9 private-sim path |
|---|---|---|
| Anyone | commitment hash, sender pseudonym, timing | — (nothing pending is public) |
| Router operator | envelope contents (operates the router) | envelope contents |
| Rival bidder | hash only (opaque) | nothing until inclusion |

Commitments are opaque hashes either way; the private path additionally
hides *participation metadata* (who committed, when).

### Reveal phase (commit window closed, reveal window open)

| Observer | Public path | CNP-9 private-sim path |
|---|---|---|
| Mempool sniffer | **pending reveal contents: (bid_axm, nonce, agent)** — full read before inclusion | nothing until inclusion |
| Anyone (post-inclusion) | revealed bid, public by design | revealed bid, public by design |
| Router operator | envelope contents | envelope contents |

The private path shrinks the sniffer's window from "everything pending"
to "nothing until applied". Post-inclusion publicity is unchanged and
intended.

### After close

Assignment (`assigned_to`, `winning_bid_axm`) is published on both paths.
No privacy claim is made here.

## 5. Threats and mitigations

**T1 — Cross-auction reveal sniping (demonstrated).** A sniffer reads a
pending reveal on task A (`bid_axm = 10` for skill S) and races an
undercutting commit (`9.5`) into sibling task B whose commit window is
still open. Mitigated on the private path: the sniffer sees no pending
envelope, so the undercut cannot be informed. Demonstrated by the
front-running simulation test (`test_cnp9_mev_protection.py`):
public backend → sniper wins task B; private-sim backend → sniper commits
blind and loses.

**T2 — Last-look participation timing.** On the public path a bidder can
watch *who* reveals and time their own reveal for the last moment,
gaining information about field depth. On the private path, reveal
participation is hidden until inclusion. Residual: inclusion order within
the window is still observable post-hoc; the auction's tiebreak is
earliest-*commit*, not earliest-reveal, so timing games cannot steal
tiebreaks.

**T3 — Commitment linkage.** Commit sender pseudonyms are visible on the
public path (intent linkage: "wallet W bids on skill S"). Hidden on the
private path until inclusion. Residual: post-inclusion, reveals still
link agent to bid — inherent to a public auction.

**T4 — Reveal grinding / selective revelation.** A bidder commits, watches
others' pending reveals, then withholds their own reveal if losing.
Mitigations (pre-existing, unchanged): ghosting costs 100% of the locked
stake + reputation reset to zero + KYA tombstoning — withholding is priced
out. CNP-9 adds: the router never routes a reveal while the commit window
is open, so "reveal-while-committing" probing is structurally impossible.

**T5 — Envelope forgery / cross-task replay.** A captured envelope is
re-submitted, or retargeted at a different task. Mitigated: every envelope
carries an HMAC-SHA256 over (version, envelope id, task, agent, kind,
timestamp, payload); verification is constant-time; task_id is inside the
MAC input so cross-task replay fails authentication; envelope ids are
single-use with a bounded freshness window (60 s default).

**T6 — Router operator trust.** The operator sees envelope contents on
both paths. This is accepted and documented: the operator already sees
everything in the auction pipeline. The private path does not move trust
*away* from the operator; it removes the *public* observer. Future live
backends move this trust to a relay operator / threshold committee — see
§7.

## 6. Residual risks (not mitigated by CNP-9)

* **Colluding bidders** share bid values out-of-band; no transport layer
  stops that. Mitigated elsewhere: KYA identity, ghost penalties, stake
  economics.
* **Post-inclusion publicity** — reveals are public after inclusion, so a
  patient rival still learns pricing one auction later. CNP-9 narrows the
  *actionable* window (pre-inclusion), it does not create lasting secrecy.
* **Timing side channels** — inclusion cadence can leak coarse activity
  levels. Not modeled.
* **The router is in-process** — the simulation backends model network
  adversaries, not a compromised host. Host compromise is out of scope.

## 7. Future backends — trust assumptions (not live claims)

`PrivateMempoolBackend` and `ThresholdDecryptionBackend` are interfaces
only. If ever wired (founder approval required), these assumptions must
hold and be re-audited:

*Private mempool / relay:*
* the relay operator and block builder are trusted not to read, copy, or
  front-run bundle contents;
* bundle atomicity/inclusion is best-effort — no censorship-resistance
  guarantee against a colluding builder;
* relay liveness is an external dependency; on relay failure the router
  fails closed (envelopes queue; they are never silently re-routed public).

*Threshold decryption:*
* decryption-committee liveness (DKG participants online at reveal time);
* fewer than the threshold of key holders collude to decrypt early —
  the threshold must be sized against the value at stake;
* decryption-delay tuning: too fast re-opens the sniping window, too slow
  stalls the reveal window.

## 8. Non-goals / explicit non-claims

* CNP-9 does not hide revealed bids after inclusion.
* CNP-9 does not protect against bidder collusion.
* CNP-9 does not change auction economics (scoring, stake, slashing) —
  the pipeline (`commit_bid` / `reveal_bid` / `close_auction`) remains the
  authority; the router is policy + transport.
* No live network integration exists in this wave; any claim about
  Flashbots/relay/threshold behavior refers to the *documented future
  interface*, not to running code.

## Appendix A — CNP-9 adversarial self-review (2026-10-02)

Reviewer hat on; findings against `mev_protected_routing.py`:

1. *MAC key handling.* Secret comes from env or `os.urandom(32)` per
   router instance; never logged; only envelope ids/kinds in logs.
   Reveal payloads (bid_axm, nonce) are never written to logs. OK.
2. *Constant-time compare.* `hmac.compare_digest` used for MAC
   verification. OK.
3. *Replay set growth.* `_seen` is pruned on every admission against the
   freshness window — bounded. OK.
4. *Clock games.* Envelopes with `issued_at` in the future are rejected
   (`age < 0`); stale ones rejected. Router clock is the auction clock
   (`_now_ms`). A skewed host clock affects both equally — accepted,
   documented.
5. *Downgrade attacks.* Live backends raise on construction/submit; there
   is no env flag to enable them and no silent fallback to public. The
   factory refuses unknown names loudly. OK.
6. *Router/policy vs pipeline divergence.* Admission duplicates some
   pipeline checks (window, double-commit). This is intentional
   defense-in-depth; inclusion re-runs the real pipeline, which stays
   authoritative. A TOCTOU gap (window closes between admission and
   inclusion) resolves safely: the pipeline re-checks and fails the
   inclusion, recorded on the receipt. OK.
7. *Private-sim honesty.* `pending_view()` returns `[]` unconditionally —
   the simulation models "no public observer exists". The threat model
   (§4) states plainly that post-inclusion publicity is unchanged. The
   front-running test asserts on `pending_view()` contents, so the
   protection claim is exactly as strong as the model, no stronger. OK.
8. *Grinding via many identities.* Router-level one-commit-per-(task,
   agent) check mirrors the pipeline; sock-puppet grinding is priced by
   the stake lock at commit (50% of bounty) and the 100% ghost slash —
   pre-existing economics, unchanged. Noted as residual, not claimed
   fixed.
9. *Receipt log as oracle.* `audit_log()` records
   `observable_pre_inclusion` per envelope — an operator can verify
   after the fact which envelopes were ever publicly visible. This is
   the "who saw what, when" evidence trail. OK.
10. *Threading.* Backend queues and router state are lock-guarded.
    `include_next` pops-then-applies: a crash between pop and apply
    loses the envelope from the queue but the receipt records the
    failure — acceptable for the offchain shim; onchain wiring (future)
    must use durable queues. Noted.

No blocking findings. The one deliberate sharp edge: `route_*` helpers
validate payload shape *before* MAC-ing, and `submit_envelope` is public
so tests/adversaries can hand-craft envelopes — that is the point (the
admission checks are what get tested).
