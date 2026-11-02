"""Wren PR 9 — replay transport-key stripping in ``_paid_skill_dispatch``.

A buyer replaying a paid serve (the documented retry path after a
settle) naturally re-sends the transport fields that were in the
original request: ``access_token``, ``payer_wallet`` — and often
``wallet``, the field the 402 challenge flow itself asked for. On
main, the dispatch popped only ``access_token``, only for
args-derived payloads, and only exact-case; JSON bodies got no
stripping at all. For the four strict-allowlist resources
(web-search-read, local-business-site-check, market-data-read,
toa-micro-decision) a stray ``wallet`` / case-variant transport key
therefore 400s with ``unknown_payload_fields`` AFTER the token was
presented — the buyer paid, then gets a validation error for a field
the payment layer itself put there.

The fix (billing.py ``_strip_transport_keys``):
* ``access_token`` + ``payer_wallet`` are transport keys — stripped
  case-insensitively from args AND JSON payloads in all 7 skill
  branches;
* ``wallet`` is stripped case-insensitively ONLY in the four strict
  branches. For wallet-sanctions-screen (screen-target alias),
  kya-trust-score (the agent wallet being scored) and
  polymarket-whale-signal (tracked-wallet filter) ``wallet`` is a
  DOMAIN input and must reach the handler untouched;
* allowlists stay strict: a genuinely unknown field still 400s.

Also pinned: a 400 never consumes the single-use access token
(P1-1 lifecycle), and rotating ``wallet`` values from one IP does
not extend the Wave 1 free ration (per-IP cap binds).

Hermetic pattern mirrors the per-resource suites: real Flask routes,
mocked on-chain verifier, provider seams stubbed per module. The
free ration is disabled (``daily_limit = 0``) for the paid tests so
every serve goes through the token path; the rotation test sets its
own caps.
"""
from __future__ import annotations

import copy
import itertools
import socket
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.kya_trust_score as kts
import sincor2.market_data_read as mdr
import sincor2.sanctions_screen as ss
import sincor2.site_check as sc
import sincor2.web_search_read as wsr
import sincor2.whale_flow as wf
import sincor2.whale_signal as whale
import sincor2.x402_payments as x402
from sincor2.a2a_integration import ReputationLedger
from sincor2.defi.ofac_sdn import load_snapshot
from sincor2.defi.price_oracle import ChainlinkRound
from sincor2.free_ration import daily_ration_tracker
from sincor2.kya.registry import KYARegistry, MIN_STAKE_WEI
from sincor2.onchain.stake_ledger import StakeLedger
from sincor2.sig_canonical import is_low_s

STRICT_FOUR = [
    "web-search-read",
    "local-business-site-check",
    "market-data-read",
    "toa-micro-decision",
]
# Strict resources whose domain payload is flat strings (servable
# through query args). toa-micro-decision's payload is structured
# (options list) and gets its own args test below.
STRICT_FLAT_ARGS = [
    "web-search-read",
    "local-business-site-check",
    "market-data-read",
]
DOMAIN_THREE = [
    "wallet-sanctions-screen",
    "kya-trust-score",
    "polymarket-whale-signal",
]
ALL_SEVEN = STRICT_FOUR + DOMAIN_THREE

DUMMY_WALLET = "0x" + "ab" * 20

# --- sanctions fixtures (mirrors test_sanctions_screen.py) -----------------
CLEAN_ADDR = "0x000000000000000000000000000000000000dEaD"

# --- kya fixtures (mirrors test_kya_trust_score.py) ------------------------
KYA_AGENT = "agent-veteran"
KYA_WALLET = "0x1111111111111111111111111111111111111111"
ONE_AXM = 10**18
DAY = 86400

# --- whale fixtures (mirrors test_whale_signal.py) -------------------------
WHALE_A = "0xf0318c32136c2db7fec88b84869aee6a1106c80c"
WHALE_B = "0x1f3a646ce5cbe2c70270e7a46161d89b7c7b895d"
WHALE_REGISTRY = {
    WHALE_A: {
        "realized_pnl_usd": 54321.09, "win_rate": 0.62,
        "resolved_trades": 87, "resolved_markets": 41,
        "scored_at": 1761000000.0,
    },
    WHALE_B: {
        "realized_pnl_usd": 1234.5, "win_rate": 0.55,
        "resolved_trades": 34, "resolved_markets": 22,
        "scored_at": 1761000000.0,
    },
}


def _whale_signal(wallet=WHALE_A, market_id="558934"):
    return wf.WhaleSignal(
        wallet=wallet, market_id=market_id,
        question="Will Spain win the 2026 FIFA World Cup?",
        token_id="987654321", side="BUY_YES",
        whale_pnl_usd=WHALE_REGISTRY[wallet]["realized_pnl_usd"],
        whale_win_rate=WHALE_REGISTRY[wallet]["win_rate"],
        whale_trade_usdc=1234.56, detected_at=1761100000.0)


# --- site-check fixtures (mirrors test_site_check.py) ----------------------
DEFICIENT_URL = "http://oldsite.example.com/"
DEFICIENT_HTML = """<html><head></head><body>
<p>We do stuff. Est. 1998.</p>
<a href="/about">About</a>
</body></html>"""
DEFICIENT_PAGE = sc.SiteFetch(status_code=200, url=DEFICIENT_URL,
                              html=DEFICIENT_HTML)

# --- web-search fixtures (mirrors test_web_search_read.py) -----------------
SEARCH_FIXTURE = [
    {"title": "Base — L2 for Ethereum",
     "url": "https://base.org/",
     "text": "Base is a secure, low-cost, builder-friendly Ethereum L2.",
     "source": "base.org"},
    {"title": "x402 Protocol",
     "url": "https://x402.org/",
     "highlights": ["x402 is an open payment protocol over HTTP."]},
    {"title": "", "url": "", "text": "no URL — must be dropped"},
]

# --- toa fixture (mirrors test_toa_micro_decision.py) ----------------------
TOA_WINNER = {
    "options": [
        {"id": "strong", "label": "strong performer",
         "signals": [0.80, 0.82, 0.81, 0.83, 0.84]},
        {"id": "weak", "label": "weak performer",
         "signals": [0.20, 0.21, 0.19, 0.22, 0.20]},
    ],
    "context": {"horizon": 4},
}

# Valid domain payload per resource (fresh copy per call).
DOMAIN_PAYLOADS = {
    "web-search-read": {"mode": "search", "query": "what is x402"},
    "local-business-site-check": {
        "url": DEFICIENT_URL, "business_name": "Old Site Co"},
    "market-data-read": {"pair": "ETH/USD", "mode": "price"},
    "toa-micro-decision": TOA_WINNER,
    "wallet-sanctions-screen": {"wallet": CLEAN_ADDR},
    "kya-trust-score": {"wallet": KYA_WALLET},
    "polymarket-whale-signal": {"wallet": WHALE_A},
}


def _payload(resource):
    return copy.deepcopy(DOMAIN_PAYLOADS[resource])


def _make_key() -> Account:
    while True:
        acct = Account.create()
        probe = acct.sign_message(encode_defunct(text="sincor-low-s-probe"))
        if is_low_s(probe.signature):
            return acct


BUYER_KEY = _make_key()
BUYER = BUYER_KEY.address


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_ration():
    daily_ration_tracker.clear()
    yield
    daily_ration_tracker.clear()


@pytest.fixture(autouse=True)
def _limiter_reset():
    # The billing blueprint's limiter ("120 per hour" on /api/paid)
    # is live in bare test apps and its in-memory budget is shared
    # process-wide per IP: ~120 paid-route calls across ALL files in
    # a one-process batch, after which serves 429. Reset around every
    # test here so this file neither exhausts that budget for later
    # files nor inherits an earlier file's spend.
    billing.limiter.reset()
    yield
    billing.limiter.reset()


@pytest.fixture(autouse=True)
def _toa_log(tmp_path, monkeypatch):
    monkeypatch.setenv("TOA_DECISION_LOG", str(tmp_path / "toa.jsonl"))


@pytest.fixture()
def no_free_ration(monkeypatch):
    """Paid tests: ration off, so serves exercise the token path."""
    monkeypatch.setattr(daily_ration_tracker, "daily_limit", 0)


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def mock_onchain(monkeypatch):
    def _verify(tx_hash, **kwargs):
        expected = int(kwargs["expected_atomic"])
        supplied = (kwargs.get("payer_wallet") or "").lower()
        if supplied and supplied != BUYER.lower():
            return {"ok": False, "error": "payer_wallet_mismatch"}
        return {"ok": True, "payer_wallet": BUYER}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _verify)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(
        x402, "record_inflow", lambda *a, **k: {"ok": True})


@pytest.fixture()
def seams(monkeypatch, tmp_path):
    """Stub every resource's provider seam (hermetic, no network)."""
    # web-search-read: provider seams.
    monkeypatch.setattr(
        wsr, "search_provider",
        lambda q: [dict(r) for r in SEARCH_FIXTURE])
    monkeypatch.setattr(
        wsr, "read_provider",
        lambda u: {"url": u, "title": "T", "text": "body"})
    # market-data-read: Chainlink loader seam, fresh round.
    now = int(time.time())
    round_ = ChainlinkRound(
        round_id=424242, answer=324_150_000_000, decimals=8,
        started_at=now - 90, updated_at=now - 90,
        answered_in_round=424242)
    monkeypatch.setattr(mdr, "load_latest_round", lambda pair: round_)
    # local-business-site-check: fetch seam + hermetic DNS (the SSRF
    # guard resolves the target host before fetching; mirror the
    # canonical suite's stub so example.com hosts resolve public).
    monkeypatch.setattr(sc, "fetch_site_page", lambda url: DEFICIENT_PAGE)

    def _dns(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM,
                 socket.IPPROTO_TCP, "", ("93.184.216.34", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _dns)
    # wallet-sanctions-screen: fresh vendored snapshot.
    import datetime as _dt
    snap = dict(load_snapshot())
    snap["fetched_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    monkeypatch.setattr(ss, "load_screen_snapshot", lambda: snap)
    # polymarket-whale-signal: registry + fresh-entries seams.
    monkeypatch.setattr(
        whale, "load_tracked_registry", lambda: dict(WHALE_REGISTRY))
    monkeypatch.setattr(
        whale, "_fresh_entries", lambda window_min: [_whale_signal()])
    # kya-trust-score: the three real stores, seeded exactly as in
    # test_kya_trust_score.py (score 70 / "established" for KYA_AGENT).
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "kya-data"))
    reg = KYARegistry()
    rec = reg.list_agent({"agent_id": KYA_AGENT, "wallet": KYA_WALLET})
    reg.bind(KYA_AGENT, KYA_WALLET)
    reg.apply_stake(rec["kya_id"], str(MIN_STAKE_WEI), "0xstake")
    reg.verify(rec["kya_id"])
    ledger = StakeLedger(path=str(tmp_path / "stake.json"))
    ledger.deposit(KYA_AGENT, 20 * ONE_AXM)
    t0 = int(time.time()) - 90 * DAY
    posters = ["poster-1", "poster-2", "poster-3"]
    ledger.record_completion(KYA_AGENT, "task-0",
                             poster_id=posters[0], at=t0)
    for i in range(1, 25):
        ledger.record_completion(
            KYA_AGENT, f"task-{i}", poster_id=posters[i % 3],
            at=t0 + 89 * DAY)
    rep = ReputationLedger(db_path=str(tmp_path / "rep.db"))
    for i in range(5):
        rep.record("wallet:" + KYA_WALLET, "skill-x", f"settle-{i}",
                   ONE_AXM)
    now2 = t0 + 90 * DAY
    monkeypatch.setattr(
        kts, "load_kya_record",
        lambda agent_id=None, wallet=None: reg.lookup(
            agent_id=agent_id, wallet=wallet))
    monkeypatch.setattr(
        kts, "load_stake_view",
        lambda agent_id, now=None: kts.stake_view_from_ledger(
            ledger, agent_id, now=now or now2))
    monkeypatch.setattr(
        kts, "load_settlement_count",
        lambda agent_id=None, wallet=None: sum(
            rep.score(k) for k in kts.settlement_keys(agent_id, wallet)))


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


_tx_counter = itertools.count(1)


def _pay(client, resource) -> str:
    """Full W-102r3 flow for ``resource`` -> the access token."""
    seed = f"{next(_tx_counter):02x}"
    tx_hash = "0x" + seed * 32
    r = client.get(f"/x402/{resource}?wallet={BUYER}")
    assert r.status_code == 402, r.get_json()
    ch = r.get_json()
    sig = BUYER_KEY.sign_message(
        encode_defunct(text=ch["sign_message"])).signature.hex()
    r = client.post("/api/x402/challenge-bind", json={
        "challenge_id": ch["challenge_id"],
        "payer_wallet": BUYER,
        "signature": sig,
    })
    assert r.status_code == 200, r.get_json()
    r = client.post("/api/x402/verify", json={
        "challenge_id": ch["challenge_id"],
        "tx_hash": tx_hash,
        "payer_wallet": BUYER,
    })
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["status"] == "fulfilled"
    return body["access_token"]


def _serve_json(client, resource, token, payload):
    return client.post(
        f"/api/paid/{resource}",
        headers={"X-Payment-Token": token},
        json=payload,
    )


def _serve_args(client, resource, token, params):
    return client.post(
        f"/api/paid/{resource}",
        headers={"X-Payment-Token": token},
        query_string=params,
    )


def _args_payload(resource, extra=None):
    """The resource's domain payload as flat query-arg strings."""
    params = {k: v for k, v in _payload(resource).items()
              if isinstance(v, str)}
    params.update(extra or {})
    return params


# ---------------------------------------------------------------------------
# wallet stripping on the strict four
# ---------------------------------------------------------------------------

class TestWalletStrippedOnStrictFour:
    @pytest.mark.parametrize("resource", STRICT_FOUR)
    def test_wallet_in_json_body_replay_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        payload = _payload(resource)
        payload["wallet"] = DUMMY_WALLET
        r = _serve_json(client, resource, token, payload)
        assert r.status_code == 200, r.get_json()

    @pytest.mark.parametrize("resource", STRICT_FLAT_ARGS)
    def test_wallet_in_args_replay_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        params = _args_payload(resource, {"wallet": DUMMY_WALLET})
        r = _serve_args(client, resource, token, params)
        assert r.status_code == 200, r.get_json()

    def test_wallet_in_args_toa_is_not_unknown_fields(
            self, client, x402_db, mock_onchain, seams, no_free_ration):
        # TOA's payload is structured: via query args ``options`` can
        # only arrive as a string, so the serve can never be a 200 —
        # but the failure must be the options validation, NOT
        # unknown_payload_fields for the transport ``wallet`` key.
        # (Pre-fix this returns unknown_payload_fields.)
        token = _pay(client, "toa-micro-decision")
        r = _serve_args(client, "toa-micro-decision", token,
                        {"options": "strong,weak", "wallet": DUMMY_WALLET})
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] != "unknown_payload_fields"
        assert r.get_json()["error"] == "options_required"


# ---------------------------------------------------------------------------
# wallet is a DOMAIN input on the other three — never stripped
# ---------------------------------------------------------------------------

class TestWalletDomainPreserved:
    @pytest.mark.parametrize("resource", DOMAIN_THREE)
    def test_wallet_in_json_body_replay_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        r = _serve_json(client, resource, token, _payload(resource))
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        if resource == "wallet-sanctions-screen":
            # wallet named the screen target (clean fixture address).
            assert body["verdict"] == "clean"
            assert body["screened"] is True
        elif resource == "kya-trust-score":
            # wallet resolved to the seeded agent and its real score.
            assert body["agent_id"] == KYA_AGENT
            assert body["score"] == 70
        else:
            # wallet filtered the tracked-whale signals.
            assert body["status"] == "signals_available"
            assert body["signals"]
            assert {s["wallet"] for s in body["signals"]} == {WHALE_A}

    @pytest.mark.parametrize("resource", DOMAIN_THREE)
    def test_wallet_in_args_replay_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        r = _serve_args(client, resource, token, _args_payload(resource))
        assert r.status_code == 200, r.get_json()
        if resource == "kya-trust-score":
            assert r.get_json()["agent_id"] == KYA_AGENT


# ---------------------------------------------------------------------------
# access_token / payer_wallet: transport keys in ALL seven branches
# ---------------------------------------------------------------------------

class TestTransportKeysStrippedEverywhere:
    @pytest.mark.parametrize("resource", ALL_SEVEN)
    def test_case_variant_transport_keys_in_json_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        payload = _payload(resource)
        payload["Access_Token"] = "replayed-token-value"
        payload["PAYER_WALLET"] = BUYER
        r = _serve_json(client, resource, token, payload)
        assert r.status_code == 200, r.get_json()


class TestPayerWalletCaseVariantStrictFour:
    @pytest.mark.parametrize("resource", STRICT_FOUR)
    def test_payer_wallet_case_variant_json_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        payload = _payload(resource)
        payload["Payer_Wallet"] = BUYER
        r = _serve_json(client, resource, token, payload)
        assert r.status_code == 200, r.get_json()

    @pytest.mark.parametrize("resource", STRICT_FLAT_ARGS)
    def test_payer_wallet_case_variant_args_200(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        params = _args_payload(resource, {"Payer_Wallet": BUYER})
        r = _serve_args(client, resource, token, params)
        assert r.status_code == 200, r.get_json()


# ---------------------------------------------------------------------------
# Allowlists stay strict — genuinely unknown fields still 400
# ---------------------------------------------------------------------------

class TestAllowlistsStayStrict:
    @pytest.mark.parametrize("resource", STRICT_FOUR)
    def test_unknown_field_json_still_400(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        payload = _payload(resource)
        payload["bogus_field"] = "x"
        r = _serve_json(client, resource, token, payload)
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "unknown_payload_fields"

    @pytest.mark.parametrize("resource", STRICT_FOUR)
    def test_unknown_field_not_masked_by_wallet_strip(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        # wallet is stripped; the genuinely unknown field is not.
        token = _pay(client, resource)
        payload = _payload(resource)
        payload["wallet"] = DUMMY_WALLET
        payload["bogus_field"] = "x"
        r = _serve_json(client, resource, token, payload)
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "unknown_payload_fields"

    @pytest.mark.parametrize("resource", STRICT_FLAT_ARGS)
    def test_unknown_field_args_still_400(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        params = _args_payload(resource, {"bogus_field": "x"})
        r = _serve_args(client, resource, token, params)
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error"] == "unknown_payload_fields"


# ---------------------------------------------------------------------------
# A 400 never consumes the access token (P1-1 lifecycle)
# ---------------------------------------------------------------------------

class TestBadPayloadDoesNotConsumeToken:
    @pytest.mark.parametrize("resource", STRICT_FOUR)
    def test_400_then_clean_replay_succeeds(
            self, client, x402_db, mock_onchain, seams, no_free_ration,
            resource):
        token = _pay(client, resource)
        bad = _payload(resource)
        bad["bogus_field"] = "x"
        r = _serve_json(client, resource, token, bad)
        assert r.status_code == 400, r.get_json()
        # The token survived the 400: a clean replay delivers.
        r2 = _serve_json(client, resource, token, _payload(resource))
        assert r2.status_code == 200, r2.get_json()


# ---------------------------------------------------------------------------
# Free ration: rotating wallet values from one IP mint no extra serves
# ---------------------------------------------------------------------------

class TestFreeRationWalletRotation:
    def test_rotating_wallets_hit_the_per_ip_cap(
            self, client, x402_db, mock_onchain, seams, monkeypatch):
        # Per-IP cap 4, per-identity limit untouched (3): each fresh
        # wallet identity serves once, well under its own limit —
        # only the shared IP cap can stop the fifth wallet.
        monkeypatch.setattr(daily_ration_tracker, "per_ip_daily_cap", 4)
        resource = "toa-micro-decision"
        wallets = ["0x" + f"{i:02x}" * 20 for i in range(1, 6)]
        for w in wallets[:4]:
            payload = _payload(resource)
            payload["wallet"] = w
            r = client.post(f"/api/paid/{resource}", json=payload)
            assert r.status_code == 200, r.get_json()
            assert r.get_json()["x402_free_ration"] is True
        # Fifth distinct wallet, same IP: no free serve left — the
        # request drops to the paid path and is answered 402.
        payload = _payload(resource)
        payload["wallet"] = wallets[4]
        r = client.post(f"/api/paid/{resource}", json=payload)
        assert r.status_code == 402, r.get_json()
