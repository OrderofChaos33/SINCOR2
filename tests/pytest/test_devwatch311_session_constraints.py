"""Dev-watch 311 — Bastion session-wallet failure shapes, pinned to ours.

ZeroDev's Bastion audit (dev-watch item 311) found three session-key
failure shapes:

1. Approval REPLAY refills a session budget — presenting the same signed
   approval again mints fresh spend authority.
2. A SECOND session init spends the first session's leftovers —
   re-initializing a grant row resurrects unspent allowance.
3. A WILDCARD chainId mints a session usable on chains/targets the
   signer never approved.

SINCOR2 has no session-key spend system: no ERC-7579/4337 session module
in this tree grants spend authority (the sinax
``ERC7579EpochSessionValidator`` is attestation-only, and the
``session_key_expires_at`` field in sinax vector retrieval is record
metadata — grep evidence recorded in the item-311 build report).  The
genuine surfaces are:

* ``sincor2.onchain.fee_authorization`` — a quorum-signed record that
  creates bounded spend authority for the fee-conversion executor
  (capability, chain/token/target allowlists, per-run budget cap,
  mandatory nonce journal).  This is the signed-approval surface.
* ``sincor2.sponsored_stake`` — the grant-row surface: one ACTIVE
  sponsorship per agent row, admin-gated, no signatures.

These tests pin the three Bastion constraints to those real surfaces.
All offline: synthetic approver keys, stub executors, tmp ledgers.
"""

import inspect
import os

import pytest

from sincor2.onchain.fee_authorization import (
    AuthorizationError,
    AuthorizationRecord,
    authorized_dry_run,
    demo_record,
    sign_record,
    verify_authorization,
)
from fee_auth_test_helpers import synthetic_approvers

CHAIN = 84532          # Base Sepolia — the chain the approvals are minted for
OTHER_CHAIN = 8453     # Base mainnet — never approved in these tests
TOKEN_IN = "0x4200000000000000000000000000000000000006"   # WETH (Sepolia)
TOKEN_OUT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # USDC (Sepolia)
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
OTHER_TREASURY = "0x" + "ee" * 20
NOW = 1_800_000_000
AXM = 10**18
AGENT = "dw311-agent"


def _approvers(n=3):
    return synthetic_approvers(n, seed=0x311)


def _record(approvers, **kw):
    params = dict(chain_id=CHAIN, token_in=TOKEN_IN, token_out=TOKEN_OUT,
                  treasury=TREASURY, approvers=approvers, quorum=2,
                  nonce="dw311-nonce-1", now=NOW)
    params.update(kw)
    return demo_record(**params)


def _sigs(record, approvers):
    return [sign_record(record, a) for a in approvers]


# --- stub executor (same duck-type as test_fee_authorization's) ------------

class _Ledger:
    def __init__(self):
        self.statuses = {}
        self.details = {}

    def transition(self, oid, status, detail=None):
        self.statuses[oid] = status
        self.details[oid] = detail or {}
        return {"id": oid, "status": status}

    def get(self, oid):
        return {"id": oid, "status": self.statuses.get(oid)}


class _Config:
    def __init__(self, chain_id=CHAIN, treasury=TREASURY):
        self.chain_id = chain_id
        self.treasury = treasury
        self._addrs = {"WETH": TOKEN_IN, "USDC": TOKEN_OUT}

    def token_address(self, symbol):
        return self._addrs[symbol.upper()]


class _Executor:
    """Counting stub: proves exactly how much 'movement' a run caused."""

    def __init__(self, chain_id=CHAIN, treasury=TREASURY):
        self.config = _Config(chain_id=chain_id, treasury=treasury)
        self.ledger = _Ledger()
        self.plan_calls = 0

    def plan(self, obligation_id, target=None):
        self.plan_calls += 1
        return {
            "obligation_id": obligation_id,
            "forwarder": "0x" + "11" * 20,
            "treasury": self.config.treasury,
            "quote": {
                "token_in": "WETH", "token_out": "USDC",
                "amount_in_wei": 10**18, "amount_out_wei": 2500_000000,
                "amount_out_min_wei": 2475_000000, "slippage_bps": 100,
            },
            "txs": [{"kind": "swap",
                     "unsigned": {"to": "0x" + "33" * 20,
                                  "chainId": self.config.chain_id}}],
        }

    def execute_plan(self, plan, sign_and_broadcast, wait_for_receipt,
                     dry_run=False):
        assert dry_run is True
        return {"obligation_id": plan["obligation_id"], "mode": "dry_run",
                "status": "simulated"}


# --- (a) single-use approvals: replay moves zero additional -----------------

def test_replayed_approval_moves_zero_additional(tmp_path):
    """Bastion shape 1: the SAME approval, presented a second time after a
    successful run, must authorize nothing further — no new plan, no new
    ledger authorization, no new manifest."""
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])
    ex = _Executor()
    journal = set()

    manifest = authorized_dry_run(
        ex, "oid-first", rec, sigs, manifest_dir=str(tmp_path),
        used_nonces=journal, now=NOW)
    assert ex.plan_calls == 1
    assert ex.ledger.statuses["oid-first"] == "authorized"
    assert os.path.exists(manifest["manifest_path"])

    with pytest.raises(AuthorizationError, match="already used"):
        authorized_dry_run(
            ex, "oid-replay", rec, sigs, manifest_dir=str(tmp_path),
            used_nonces=journal, now=NOW)

    # Zero additional movement of any kind.
    assert ex.plan_calls == 1
    assert "oid-replay" not in ex.ledger.statuses
    assert not os.path.exists(
        os.path.join(str(tmp_path), "authorized-oid-replay.json"))
    assert journal == {rec.nonce}


def test_approval_identity_is_its_signed_digest():
    """Single use is keyed by the approval's digest: re-minting the same
    approval under a fresh nonce produces a DIFFERENT digest, and the
    original approver signatures do not cover it — a replay cannot be
    laundered through a nonce swap."""
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])

    clone = AuthorizationRecord(
        **{**rec.canonical_dict(), "nonce": "dw311-nonce-2"})
    assert clone.auth_hash() != rec.auth_hash()
    with pytest.raises(AuthorizationError, match="quorum not met"):
        verify_authorization(clone, sigs, now=NOW)


# --- (b) one-time init per grant row; leftovers unreachable after close -----

@pytest.fixture
def stake_ledgers(tmp_path, monkeypatch):
    from sincor2.onchain.stake_ledger import reset_stake_ledger
    from sincor2.sponsored_stake import (
        ENABLE_ENV, reset_sponsored_ledger, sponsored_stake_enabled)

    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    ledger = reset_sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    monkeypatch.setenv(ENABLE_ENV, "1")
    assert sponsored_stake_enabled() is True
    return ledger


def test_second_init_rejected_and_leftovers_unreachable_after_close(
        stake_ledgers):
    """Bastion shape 2, on the grant-row surface (sponsored stake):
    a second front() while the row is active is refused and refills
    nothing; once the row is closed (settled), its leftover claim is
    unreachable — further recoup/clawback move zero."""
    from sincor2.sponsored_stake import (
        front_sponsored_stake, recoup_sponsored_stake, sponsored_ledger)

    front_sponsored_stake(AGENT, 2 * AXM)
    assert sponsored_ledger().outstanding_wei(AGENT) == 2 * AXM

    # Second init while active: rejected, and the budget is NOT refilled.
    with pytest.raises(RuntimeError, match="already has an active"):
        front_sponsored_stake(AGENT, 5 * AXM)
    assert sponsored_ledger().outstanding_wei(AGENT) == 2 * AXM

    # Close the row: full recoup settles it.
    out = recoup_sponsored_stake(AGENT, 2 * AXM, task_id="dw311-close")
    assert out["status"] == "settled"
    assert sponsored_ledger().outstanding_wei(AGENT) == 0

    # Leftovers unreachable after close: neither earnings recoup nor a
    # slash clawback can move anything against the settled row.
    again = recoup_sponsored_stake(AGENT, 3 * AXM, task_id="dw311-late")
    assert again["recouped_wei"] == "0"
    applied = sponsored_ledger().apply_clawback(
        AGENT, 3 * AXM, task_id="dw311-slash")
    assert applied == 0
    assert sponsored_ledger().outstanding_wei(AGENT) == 0
    assert sponsored_ledger().status_of(AGENT)["status"] == "settled"


# --- (c) approvals bind chainId + target + budget at signing time -----------

def test_cross_chain_approval_rejected_full_loop(tmp_path):
    """Bastion shape 3: an approval minted for Base Sepolia presented to
    a Base-mainnet executor authorizes nothing there.  A failed
    presentation must not burn the approval either (the nonce is
    journaled only on a completed run)."""
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])
    ex = _Executor(chain_id=OTHER_CHAIN)
    journal = set()

    with pytest.raises(AuthorizationError, match="chain"):
        authorized_dry_run(
            ex, "oid-xchain", rec, sigs, manifest_dir=str(tmp_path),
            used_nonces=journal, now=NOW)
    assert "oid-xchain" not in ex.ledger.statuses
    assert journal == set()


def test_cross_target_approval_rejected_full_loop(tmp_path):
    """An approval naming the treasury as its target is invalid for any
    other target: an executor configured for a different treasury gets
    nothing authorized."""
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])
    ex = _Executor(treasury=OTHER_TREASURY)
    journal = set()

    with pytest.raises(AuthorizationError, match="treasury"):
        authorized_dry_run(
            ex, "oid-xtarget", rec, sigs, manifest_dir=str(tmp_path),
            used_nonces=journal, now=NOW)
    assert "oid-xtarget" not in ex.ledger.statuses
    assert journal == set()


def test_chain_and_budget_binding_live_inside_the_signed_record():
    """The chain list and the budget cap are fields of the canonical
    record the approvers sign — re-targeting a signed approval to
    another chain, or inflating its budget after signing, breaks the
    quorum: the original signatures no longer verify."""
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])

    retargeted = AuthorizationRecord(
        **{**rec.canonical_dict(), "chain_ids": [OTHER_CHAIN]})
    with pytest.raises(AuthorizationError, match="quorum not met"):
        verify_authorization(retargeted, sigs, now=NOW)

    inflated = AuthorizationRecord(
        **{**rec.canonical_dict(), "max_wei_per_run": 10**30})
    with pytest.raises(AuthorizationError, match="quorum not met"):
        verify_authorization(inflated, sigs, now=NOW)


# --- guard: no session-key spend surface exists to regress ------------------

def test_no_session_key_spend_surface_exists():
    """Guard (item-306 pattern): the tree's only 'session'-named surfaces
    stay non-spending.  If a future change gives either of them fund
    movement, this test fails and the Bastion constraints above must be
    re-audited against the new surface.

    * ERC7579EpochSessionValidator: attestation only (validate /
      attest_payload) — no approve/grant/spend entry points.
    * sinax VectorRecord.session_key_expires_at: an expiry timestamp on
      a retrieval record — the record carries no balance, allowance,
      or spend authority of any kind.
    """
    from sincor2.sinax.epoch_attestation import ERC7579EpochSessionValidator
    from sincor2.sinax.vector_retrieval_engine import VectorRecord

    methods = {name for name, _ in inspect.getmembers(
        ERC7579EpochSessionValidator, predicate=inspect.isfunction)
        if not name.startswith("_")}
    assert methods == {"validate", "attest_payload"}

    fields = set(VectorRecord.__dataclass_fields__)
    assert "session_key_expires_at" in fields
    assert not fields & {"balance", "balance_wei", "allowance",
                         "budget", "budget_wei", "spend_limit"}
