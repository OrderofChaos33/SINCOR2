"""Sybil-economics fix tests (red-team 2026-10-08, CONFIRMED).

Covers the four Worker-1 fixes:

1. Tombstone enforcement at registration — a wallet killed by
   ``kya_registry.flag_ghost`` (or ``revoke``) can no longer back a new
   registration OR a re-registration: ``register_agent_record`` fails
   closed with ``RegistrationAuthError``.
2. Ghost penalty on plaintext no-shows — ``expire_stale_assignments()``
   now applies the sealed-commit ghost penalty (reputation reset to 0.0,
   wallet tombstoned) and publishes it on the task.expired event.
3. MAX_IDENTITIES_PER_WALLET on the inbound path — the 4th live identity
   on one wallet is rejected at registration, not just at bind().
4. Funding-graph linkage before payout — ``sybil_defense.quarantine_check``
   flags shared-wallet clusters on the payout record and blocks payouts
   for clusters >= 3 or with a tombstoned member.

ADVERSARIAL SELF-REVIEW (how I tried to break it):

* Rebirth, same wallet, different agent_id → BLOCKED by fix 1 (test_a).
* Rebirth on the tombstoned wallet via re-registration with a VALID
  EIP-191 proof → BLOCKED: the tombstone gate runs before the proof
  check, so a correct signature cannot resurrect a dead wallet (test_a).
* No-show then immediate re-bid with the SAME agent_id → NOT blocked:
  the penalty zeroes reputation (probation) and tombstones the wallet,
  but the existing record is not banned from bidding on non-merit tasks.
  Documented residual: the penalty is economic/reputational, not an
  identity ban; the ban bites only NEW identities on the tombstoned
  wallet (test_b_residual_same_identity_can_still_bid).
* Fresh wallet rebirth after ghosting → NOT blocked: tombstones bind to
  wallets, not to the off-chain operator. The 3-identity cluster cap
  (fix 3) raises the cost (3 fresh wallets per fleet), and fix 4 blocks
  the payout if the operator reuses one wallet >= 3 times, but a
  well-funded sybil with fresh wallets per identity is a KNOWN RESIDUAL.
  Full mitigation needs funding-graph tracing beyond shared wallets
  (onchain funding sources), which is out of scope here.
* Cluster split across 2 wallets x 2 agents → flagged but NOT blocked:
  each cluster is size 2 < 3. Documented residual: the >= 3 threshold is
  a tripwire, not a proof of innocence (test_c_residual_split_cluster).
* Tombstoned member inside a small cluster → BLOCKED: quarantine also
  fires when ANY cluster member is tombstoned, even at size 2, so a
  ghost cannot launder one clean co-identity into a payout
  (test_c_tombstoned_member_quarantines_small_cluster).

No Flask server, no chain. KYA + fabric persistence are redirected to
temp dirs and both registries are reset per test.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya
from sincor2.a2a_inbound import (
    RegistrationAuthError,
    _apply_reputation,
    _now_ms,
    build_reregistration_message,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_inbound_ext import heartbeat_agent, register_agent_record
from sincor2.a2a_inbound_market import (
    expire_stale_assignments,
    place_bid,
    submit_proof,
)
from sincor2.sybil_defense import cluster_identities_by_wallet, quarantine_check


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    """Hermetic KYA + fabric state per test (mirrors test_kya_trust_lifecycle)."""
    kya_dir = tempfile.mkdtemp(prefix="sybil_kya_")
    store_fd, store_path = tempfile.mkstemp(prefix="sybil_kya_", suffix=".json")
    os.close(store_fd)
    if os.path.exists(store_path):
        os.unlink(store_path)
    monkeypatch.setenv("KYA_STORE_PATH", store_path)
    monkeypatch.setenv("SINCOR_DATA_DIR", kya_dir)
    kya.reset()
    reset_fabric()
    yield
    kya.reset()
    reset_fabric()
    if os.path.exists(store_path):
        os.unlink(store_path)
    shutil.rmtree(kya_dir, ignore_errors=True)


def _body(agent_id: str, wallet: str, **extra):
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "description": "sybil probe agent",
        "version": "0.1.0",
        "capability_tags": ["lead-enrichment"],
        "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
        "rpc_callback": "https://probe.example/rpc",
        "wallet": wallet,
        "chain_id": 8453,
    }
    body.update(extra)
    return body


def _register(agent_id: str, wallet: str, **extra):
    return register_agent_record(_body(agent_id, wallet, **extra))


def _signed_reregistration_body(key, agent_id: str, wallet: str, **extra):
    """A re-registration body carrying a VALID EIP-191 proof by the wallet."""
    body = _body(agent_id, wallet, **extra)
    ts = _now_ms()
    message = build_reregistration_message(body, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    body["registration_ts"] = ts
    body["registration_signature"] = "0x" + bytes(sig).hex()
    return body


# ---------------------------------------------------------------------------
# (a) Tombstone enforcement at registration
# ---------------------------------------------------------------------------

def test_a_tombstoned_wallet_rejects_new_registration():
    """Rebirth with a new agent_id on a ghost-tombstoned wallet fails closed."""
    key = Account.create()
    wallet = key.address
    _register("sybil-a1", wallet)

    tomb = kya.flag_ghost("sybil-a1")
    assert tomb is not None and tomb["reason"] == "ghosting"
    assert kya.is_wallet_tombstoned(wallet)

    # Same wallet, fresh agent_id: the classic whitewash. Must be refused.
    with pytest.raises(RegistrationAuthError, match="tombstoned"):
        _register("sybil-a2", wallet)


def test_a_tombstoned_wallet_rejects_reregistration_even_with_valid_proof():
    """A correct EIP-191 re-registration proof cannot resurrect a dead wallet.

    The tombstone gate runs BEFORE the proof check: fail closed means even
    the legitimate key holder is refused once the wallet is tombstoned.
    """
    key = Account.create()
    wallet = key.address
    _register("sybil-a1r", wallet)
    kya.flag_ghost("sybil-a1r")
    assert kya.is_wallet_tombstoned(wallet)

    body = _signed_reregistration_body(key, "sybil-a1r", wallet,
                                       description="updated blurb")
    with pytest.raises(RegistrationAuthError, match="tombstoned"):
        register_agent_record(body)


def test_a_revoke_tombstone_also_blocks_registration():
    """revoke() writes a tombstone too — it must block rebirth as well."""
    key = Account.create()
    wallet = key.address
    snap = _register("sybil-a1v", wallet)
    kya.revoke(snap["kya_id"], "test revocation")
    assert kya.is_wallet_tombstoned(wallet)

    with pytest.raises(RegistrationAuthError, match="tombstoned"):
        _register("sybil-a2v", wallet)


def test_a_untombstoned_wallet_registers_fine():
    """The gate is evidence-triggered: clean wallets are unaffected."""
    snap = _register("sybil-clean", Account.create().address)
    assert snap["agent_id"] == "sybil-clean"


# ---------------------------------------------------------------------------
# (3) MAX_IDENTITIES_PER_WALLET on the inbound registration path
# ---------------------------------------------------------------------------

def test_wallet_identity_cap_enforced_on_inbound_path():
    """The 4th live identity on one wallet is rejected at registration."""
    wallet = Account.create().address
    for i in range(kya.MAX_IDENTITIES_PER_WALLET):
        _register(f"sybil-cap-{i}", wallet)
    with pytest.raises(RegistrationAuthError, match="identity cap"):
        _register("sybil-cap-over", wallet)


def test_wallet_identity_cap_allows_reregistration_of_member():
    """Re-registering an existing identity must not self-trip the cap."""
    key = Account.create()
    wallet = key.address
    for i in range(kya.MAX_IDENTITIES_PER_WALLET):
        _register(f"sybil-capm-{i}", wallet)
    # exclude_agent_id=self keeps the member's own record out of the count.
    body = _signed_reregistration_body(key, "sybil-capm-0", wallet,
                                       description="still me")
    snap = register_agent_record(body)
    assert snap["agent_id"] == "sybil-capm-0"


# ---------------------------------------------------------------------------
# (b) Ghost penalty on plaintext no-shows
# ---------------------------------------------------------------------------

def _assigned_task(task_id: str, agent_id: str, assigned_ago_ms: int):
    fabric = get_fabric()
    fabric.tasks[task_id] = {
        "task_id": task_id,
        "state": "assigned",
        "assigned_to": agent_id,
        "assigned_at": _now_ms() - assigned_ago_ms,
        "time_est_ms": 60_000,
        "tags": ["lead-enrichment"],
        "poster_id": "poster-1",
        "winning_bid_axm": 1.0,
        "bounty_axm": 1.0,
    }
    return task_id


def test_b_plaintext_noshow_triggers_reputation_reset_and_tombstone():
    """expire_stale_assignments() now punishes no-shows like sealed ghosts."""
    wallet = Account.create().address
    _register("sybil-ns", wallet)
    fabric = get_fabric()
    _apply_reputation(fabric.agents["sybil-ns"], 0.8)
    assert fabric.agents["sybil-ns"]["reputation"] == 0.8

    _assigned_task("task-noshow-1", "sybil-ns", assigned_ago_ms=10_000_000)

    expired = expire_stale_assignments()
    assert len(expired) == 1
    snap = expired[0]
    assert snap["state"] == "expired"
    assert snap["expired_reason"] == "execution_timeout"

    # Reputation reset to 0.0, probation restored — the sealed-ghost penalty.
    agent = fabric.agents["sybil-ns"]
    assert agent["reputation"] == 0.0
    assert agent["probation"] is True

    # Wallet tombstoned — rebirth behind it is now blocked (fix 1).
    assert kya.is_wallet_tombstoned(wallet)

    # Penalty recorded on the task and published on the task.expired event.
    task = fabric.tasks["task-noshow-1"]
    penalty = task.get("ghost_penalty") or {}
    assert penalty["agent_id"] == "sybil-ns"
    assert penalty["reputation_reset"] is True
    assert penalty["wallet_tombstoned"] is True
    assert snap["ghost_penalty"] == penalty
    expired_events = [e for e in fabric.events if e.get("type") == "task.expired"]
    assert expired_events, "task.expired must be published"
    assert expired_events[-1]["payload"]["ghost_penalty"] == penalty


def test_b_residual_same_identity_can_still_bid_after_penalty():
    """KNOWN RESIDUAL: the penalty is reputational, not an identity ban.

    After a no-show the SAME agent_id can still bid on non-merit tasks
    (reputation 0.0 only gates merit-gated work). The ban bites new
    identities on the tombstoned wallet, not the penalized record itself.
    """
    wallet = Account.create().address
    _register("sybil-ns2", wallet)
    fabric = get_fabric()
    _apply_reputation(fabric.agents["sybil-ns2"], 0.8)
    _assigned_task("task-noshow-2", "sybil-ns2", assigned_ago_ms=10_000_000)
    expire_stale_assignments()
    assert fabric.agents["sybil-ns2"]["reputation"] == 0.0

    # The penalized identity heartbeats and bids on a fresh open task.
    heartbeat_agent("sybil-ns2")
    fabric.tasks["task-fresh"] = {
        "task_id": "task-fresh",
        "state": "open",
        "tags": ["lead-enrichment"],
        "auction_closes_at": None,
        "bounty_axm": 1.0,
    }
    bid = place_bid("task-fresh", "sybil-ns2", 0.9, 600)
    assert bid["agent_id"] == "sybil-ns2"


# ---------------------------------------------------------------------------
# (4) Funding-graph linkage before payout
# ---------------------------------------------------------------------------

def test_c_cluster_of_three_is_quarantined_and_payout_blocked():
    """3 identities on one wallet: quarantine_check blocks; payout refused."""
    wallet = Account.create().address
    for i in range(3):
        _register(f"sybil-c{i}", wallet)

    clusters = cluster_identities_by_wallet()
    assert clusters[wallet.lower()] == ["sybil-c0", "sybil-c1", "sybil-c2"]

    verdict = quarantine_check("sybil-c1")
    assert verdict["cluster_size"] == 3
    assert verdict["flagged"] is True
    assert verdict["quarantined"] is True

    _assigned_task("task-pay-1", "sybil-c1", assigned_ago_ms=1_000)
    with pytest.raises(PermissionError, match="quarantined"):
        submit_proof("task-pay-1", "sybil-c1", "0x" + "ab" * 16)
    # The task is untouched: the block fires before any state mutation.
    assert get_fabric().tasks["task-pay-1"]["state"] == "assigned"


def test_c_pair_cluster_is_flagged_not_blocked_and_noted_on_receipt():
    """2 identities on one wallet: flagged on the payout record, not blocked."""
    wallet = Account.create().address
    _register("sybil-p0", wallet)
    _register("sybil-p1", wallet)

    verdict = quarantine_check("sybil-p0")
    assert verdict["cluster_size"] == 2
    assert verdict["flagged"] is True
    assert verdict["quarantined"] is False

    _assigned_task("task-pay-2", "sybil-p0", assigned_ago_ms=1_000)
    proof = submit_proof("task-pay-2", "sybil-p0", "0x" + "cd" * 16)
    assert proof["status"] == "paid"
    sybil = proof["payout"].get("sybil") or {}
    assert sybil["flagged"] is True
    assert sybil["cluster_size"] == 2
    assert sorted(sybil["cluster"]) == ["sybil-p0", "sybil-p1"]


def test_c_solo_agent_payout_clean():
    """A singleton wallet pays out with no sybil annotation."""
    wallet = Account.create().address
    _register("sybil-solo", wallet)
    verdict = quarantine_check("sybil-solo")
    assert verdict["flagged"] is False
    assert verdict["quarantined"] is False

    _assigned_task("task-pay-3", "sybil-solo", assigned_ago_ms=1_000)
    proof = submit_proof("task-pay-3", "sybil-solo", "0x" + "ef" * 16)
    assert proof["status"] == "paid"
    assert "sybil" not in proof["payout"]


def test_c_tombstoned_member_quarantines_small_cluster():
    """Any tombstoned cluster member blocks payout even below size 3."""
    wallet = Account.create().address
    _register("sybil-t0", wallet)
    _register("sybil-t1", wallet)
    kya.flag_ghost("sybil-t0")  # one member ghosts elsewhere

    verdict = quarantine_check("sybil-t1")
    assert verdict["cluster_size"] == 2
    # Tombstones are wallet-keyed: every member behind the tombstoned
    # wallet counts as a tombstoned member.
    assert set(verdict["tombstoned_members"]) == {"sybil-t0", "sybil-t1"}
    assert verdict["quarantined"] is True

    _assigned_task("task-pay-4", "sybil-t1", assigned_ago_ms=1_000)
    with pytest.raises(PermissionError, match="quarantined"):
        submit_proof("task-pay-4", "sybil-t1", "0x" + "ab" * 16)


def test_c_residual_split_cluster_evades_size_tripwire():
    """KNOWN RESIDUAL: 2 wallets x 2 agents each is flagged, not blocked.

    The >= 3 tripwire is per-wallet. An operator spreading identities
    across fresh wallets (2 per wallet) stays under it. Full mitigation
    needs funding-graph tracing beyond shared wallets (onchain funding
    sources), which is out of scope for this fix.
    """
    wa, wb = Account.create().address, Account.create().address
    _register("sybil-x0", wa)
    _register("sybil-x1", wa)
    _register("sybil-y0", wb)
    _register("sybil-y1", wb)

    for aid in ("sybil-x0", "sybil-y0"):
        verdict = quarantine_check(aid)
        assert verdict["cluster_size"] == 2
        assert verdict["flagged"] is True
        assert verdict["quarantined"] is False, (
            "documented residual: split clusters evade the size tripwire")
