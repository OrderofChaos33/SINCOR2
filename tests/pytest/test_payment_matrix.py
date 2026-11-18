"""Tests for sincor2.payment_matrix (Directive 1 routing matrix).

Self-contained: builds its own tables, needs no app fixtures. Money is
integers only throughout — several tests exist specifically to prove
floats and bools are rejected.
"""

import ast
import hashlib
import hmac
import json

import pytest

from sincor2 import payment_matrix as pm


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def full_table():
    """A synthetic 53-row table exercising every kind (test data only)."""
    rows = []
    for i in range(1, 54):
        pid = f"P{i:02d}"
        kind = ["fixed", "subscription", "percentage", "per_call", "quote_only"][i % 5]
        row = {"path_id": pid, "kind": kind}
        if kind == "fixed":
            row["amount_cents"] = 1000 + i
        elif kind == "subscription":
            row["amount_cents"] = 2000 + i
            row["period"] = "month" if i % 2 else "year"
        elif kind == "percentage":
            row["bps"] = 500
        elif kind == "per_call":
            row["micro_cents"] = 750_000
        rows.append(row)
    return rows


def small_matrix():
    return pm.build_matrix(
        [
            {"path_id": "P01", "kind": "percentage", "bps": 500},
            {"path_id": "P07", "kind": "fixed", "amount_cents": 50_000},
            {"path_id": "P47", "kind": "subscription", "amount_cents": 14_900,
             "period": "month"},
            {"path_id": "P08", "kind": "per_call", "micro_cents": 750_000},
            {"path_id": "P17", "kind": "quote_only"},
        ],
        expected_count=5,
    )


def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# validators — every kind, accept + reject
# ---------------------------------------------------------------------------

def test_fixed_accept():
    e = pm.RouteEntry("P07", "fixed", amount_cents=50_000).validate()
    assert e.amount_cents == 50_000


@pytest.mark.parametrize("kwargs", [
    {},                                     # no amount
    {"amount_cents": 0},                    # zero
    {"amount_cents": -5},                   # negative
    {"amount_cents": 10.5},                 # float — forbidden
    {"amount_cents": True},                 # bool — forbidden
    {"amount_cents": 100, "bps": 500},      # bps on a fixed entry
    {"amount_cents": 100, "period": "month"},
    {"amount_cents": 100, "micro_cents": 5},
])
def test_fixed_reject(kwargs):
    with pytest.raises(pm.InvalidRouteError) as exc:
        pm.RouteEntry("P07", "fixed", **kwargs).validate()
    assert "P07" in str(exc.value)  # error names the path_id


def test_subscription_accept():
    pm.RouteEntry("P47", "subscription", amount_cents=14_900, period="month").validate()
    pm.RouteEntry("P40", "subscription", amount_cents=11_200_000, period="year").validate()


@pytest.mark.parametrize("kwargs", [
    {"amount_cents": 100},                              # no period
    {"amount_cents": 100, "period": "week"},            # bad period
    {"period": "month"},                                # no amount
    {"amount_cents": 0, "period": "month"},             # zero amount
    {"amount_cents": 100, "period": "month", "bps": 5},  # bps set
])
def test_subscription_reject(kwargs):
    with pytest.raises(pm.InvalidRouteError) as exc:
        pm.RouteEntry("P47", "subscription", **kwargs).validate()
    assert "P47" in str(exc.value)


def test_percentage_accept():
    pm.RouteEntry("P01", "percentage", bps=500).validate()
    pm.RouteEntry("P01", "percentage", bps=10_000).validate()
    pm.RouteEntry("P01", "percentage", bps=1).validate()


@pytest.mark.parametrize("kwargs", [
    {},                          # no bps
    {"bps": 0},                  # zero
    {"bps": 10_001},             # over 100%
    {"bps": -1},
    {"bps": 5.0},                # float bps — forbidden
    {"bps": 500, "amount_cents": 100},  # amount on a percentage entry
])
def test_percentage_reject(kwargs):
    with pytest.raises(pm.InvalidRouteError) as exc:
        pm.RouteEntry("P01", "percentage", **kwargs).validate()
    assert "P01" in str(exc.value)


def test_per_call_accept_sub_cent_tariff():
    # $0.0075/call = 0.75 cents = 750_000 micro-cents, integer only.
    e = pm.RouteEntry("P08", "per_call", micro_cents=750_000).validate()
    assert e.micro_cents == 750_000
    assert pm.micro_cents_from_cents(50) == 50_000_000  # $0.50/call


@pytest.mark.parametrize("kwargs", [
    {},                                        # no tariff
    {"micro_cents": 0},
    {"micro_cents": -1},
    {"micro_cents": 0.75},                     # float — the thing we forbid
    {"micro_cents": 750_000, "amount_cents": 1},  # two sources of truth
    {"micro_cents": 750_000, "bps": 10},
])
def test_per_call_reject(kwargs):
    with pytest.raises(pm.InvalidRouteError) as exc:
        pm.RouteEntry("P08", "per_call", **kwargs).validate()
    assert "P08" in str(exc.value)


def test_quote_only_accept():
    pm.RouteEntry("P17", "quote_only").validate()


@pytest.mark.parametrize("kwargs", [
    {"amount_cents": 100},
    {"micro_cents": 100},
    {"bps": 100},
    {"period": "month"},
])
def test_quote_only_reject(kwargs):
    with pytest.raises(pm.InvalidRouteError) as exc:
        pm.RouteEntry("P17", "quote_only", **kwargs).validate()
    assert "P17" in str(exc.value)


def test_unknown_kind_and_bad_path_id_rejected():
    with pytest.raises(pm.InvalidRouteError):
        pm.RouteEntry("P01", "hourly", amount_cents=1).validate()
    with pytest.raises(pm.InvalidRouteError):
        pm.RouteEntry("X01", "fixed", amount_cents=1).validate()
    with pytest.raises(pm.InvalidRouteError):
        pm.RouteEntry("", "fixed", amount_cents=1).validate()


def test_validator_is_derived_from_kind():
    e = pm.RouteEntry("P01", "percentage", bps=500)
    assert e.validator is pm._VALIDATORS["percentage"]


# ---------------------------------------------------------------------------
# builder — count refusal, duplicates, full 53
# ---------------------------------------------------------------------------

def test_full_53_table_builds():
    m = pm.build_matrix(full_table())
    assert len(m) == 53
    assert m.path_ids[0] == "P01" and m.path_ids[-1] == "P53"
    assert m.get("P21").path_id == "P21"


def test_count_mismatch_refused():
    rows = full_table()[:-1]  # 52 rows — one path dropped
    with pytest.raises(pm.MatrixCountError):
        pm.build_matrix(rows)  # default expected_count=53
    # ... and the same rows build fine when the expectation is declared.
    assert len(pm.build_matrix(rows, expected_count=52)) == 52


def test_empty_repo_catalog_table_fails_loudly():
    # CATALOG_TABLE ships empty on purpose; building from it must refuse.
    with pytest.raises(pm.MatrixCountError):
        pm.build_matrix(pm.CATALOG_TABLE)


def test_duplicate_path_id_refused():
    rows = [
        {"path_id": "P01", "kind": "fixed", "amount_cents": 100},
        {"path_id": "P01", "kind": "fixed", "amount_cents": 200},
    ]
    with pytest.raises(pm.InvalidRouteError):
        pm.build_matrix(rows, expected_count=2)


def test_builder_validates_every_entry():
    rows = full_table()
    rows[10] = {"path_id": "P11", "kind": "fixed", "amount_cents": -1}
    with pytest.raises(pm.InvalidRouteError) as exc:
        pm.build_matrix(rows)
    assert "P11" in str(exc.value)


def test_unknown_row_field_refused():
    with pytest.raises(pm.InvalidRouteError):
        pm.build_matrix(
            [{"path_id": "P01", "kind": "fixed", "amount_cents": 1, "price": 1}],
            expected_count=1)


def test_load_catalog_json(tmp_path):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(full_table()))
    m = pm.load_catalog_json(p)
    assert len(m) == 53


# ---------------------------------------------------------------------------
# canonical serialization + fingerprint
# ---------------------------------------------------------------------------

def test_serialization_deterministic_under_reordering():
    rows = full_table()
    a = pm.build_matrix(rows)
    b = pm.build_matrix(list(reversed(rows)))
    assert a.canonical_bytes() == b.canonical_bytes()
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_is_sha256_of_canonical_bytes():
    m = small_matrix()
    assert m.fingerprint() == hashlib.sha256(m.canonical_bytes()).hexdigest()
    assert len(m.fingerprint()) == 64


def test_fingerprint_changes_with_amount():
    base = [{"path_id": "P01", "kind": "fixed", "amount_cents": 100}]
    changed = [{"path_id": "P01", "kind": "fixed", "amount_cents": 101}]
    assert (pm.build_matrix(base, expected_count=1).fingerprint()
            != pm.build_matrix(changed, expected_count=1).fingerprint())


def test_canonical_form_is_compact_sorted_integer_json():
    m = small_matrix()
    doc = json.loads(m.canonical_bytes())
    assert doc["version"] == 1
    assert [r["path_id"] for r in doc["entries"]] == sorted(m.path_ids)
    for row in doc["entries"]:
        for key in ("amount_cents", "micro_cents", "bps"):
            assert row[key] is None or (
                isinstance(row[key], int) and not isinstance(row[key], bool))
    assert b", " not in m.canonical_bytes()  # compact separators


# ---------------------------------------------------------------------------
# webhook verification
# ---------------------------------------------------------------------------

PAYLOAD = json.dumps(
    {"event_id": "evt_1", "path_id": "P07", "state": "pending"}).encode()
SECRET = "whsec_test_value"


def test_webhook_good_signature():
    assert pm.verify_webhook(PAYLOAD, sign(PAYLOAD, SECRET), SECRET) is True
    assert pm.verify_webhook(PAYLOAD, "sha256=" + sign(PAYLOAD, SECRET), SECRET) is True


def test_webhook_bad_signature():
    assert pm.verify_webhook(PAYLOAD, sign(PAYLOAD, "other-secret"), SECRET) is False
    assert pm.verify_webhook(PAYLOAD, "0" * 64, SECRET) is False
    assert pm.verify_webhook(PAYLOAD, None, SECRET) is False
    assert pm.verify_webhook(PAYLOAD, "", SECRET) is False
    tampered = PAYLOAD.replace(b"pending", b"settled")
    assert pm.verify_webhook(tampered, sign(PAYLOAD, SECRET), SECRET) is False


@pytest.mark.parametrize("secret", [None, "", b""])
def test_webhook_unset_secret_fails_closed(secret):
    with pytest.raises(pm.WebhookSecretError):
        pm.verify_webhook(PAYLOAD, sign(PAYLOAD, SECRET), secret)


def test_parse_webhook_event_ok():
    ev = pm.parse_webhook_event(PAYLOAD, small_matrix())
    assert ev == pm.WebhookEvent(event_id="evt_1", path_id="P07", state="pending")


def test_parse_webhook_event_unknown_path_rejected():
    bad = json.dumps({"event_id": "e", "path_id": "P99", "state": "pending"}).encode()
    with pytest.raises(pm.UnknownPathError):
        pm.parse_webhook_event(bad, small_matrix())


@pytest.mark.parametrize("doc", [
    {"path_id": "P07", "state": "pending"},                    # no event_id
    {"event_id": "", "path_id": "P07", "state": "pending"},    # empty event_id
    {"event_id": "e", "path_id": "P07", "state": "refunded"},  # bad state
    {"event_id": "e", "state": "pending"},                     # no path_id
])
def test_parse_webhook_event_bad_shape(doc):
    with pytest.raises(pm.WebhookPayloadError):
        pm.parse_webhook_event(json.dumps(doc).encode(), small_matrix())


def test_parse_webhook_event_not_json():
    with pytest.raises(pm.WebhookPayloadError):
        pm.parse_webhook_event(b"not json", small_matrix())


# ---------------------------------------------------------------------------
# state store — dedupe, forward-only, persistence
# ---------------------------------------------------------------------------

def ev(event_id, state, path_id="P07"):
    return pm.WebhookEvent(event_id=event_id, path_id=path_id, state=state)


def test_store_forward_lifecycle():
    s = pm.StateStore()
    r1 = s.apply(ev("e1", "pending"))
    assert r1.state == "pending"
    r2 = s.apply(ev("e1", "verified"))
    assert r2.state == "verified"
    r3 = s.apply(ev("e1", "settled"))
    assert r3.state == "settled"
    assert len(s) == 1


def test_store_duplicate_event_is_noop():
    s = pm.StateStore()
    first = s.apply(ev("e1", "pending"))
    again = s.apply(ev("e1", "pending"))
    assert again is first  # same record object returned, nothing changed
    s.apply(ev("e1", "verified"))
    dup = s.apply(ev("e1", "verified"))
    assert dup.state == "verified"
    assert len(s) == 1


def test_store_new_event_must_enter_at_pending():
    s = pm.StateStore()
    with pytest.raises(pm.InvalidTransitionError):
        s.apply(ev("e1", "verified"))
    with pytest.raises(pm.InvalidTransitionError):
        s.apply(ev("e1", "settled"))


def test_store_skipped_transition_raises():
    s = pm.StateStore()
    s.apply(ev("e1", "pending"))
    with pytest.raises(pm.InvalidTransitionError):
        s.apply(ev("e1", "settled"))  # pending -> settled skips verified


def test_store_backward_transition_raises():
    s = pm.StateStore()
    s.apply(ev("e1", "pending"))
    s.apply(ev("e1", "verified"))
    with pytest.raises(pm.InvalidTransitionError):
        s.apply(ev("e1", "pending"))
    s.apply(ev("e1", "settled"))
    with pytest.raises(pm.InvalidTransitionError):
        s.apply(ev("e1", "verified"))  # settled is terminal


def test_store_event_id_cannot_rebind_path():
    s = pm.StateStore()
    s.apply(ev("e1", "pending", path_id="P07"))
    with pytest.raises(pm.InvalidTransitionError):
        s.apply(ev("e1", "pending", path_id="P47"))


def test_store_file_backed_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    s = pm.StateStore(path)
    s.apply(ev("e1", "pending"))
    s.apply(ev("e1", "verified"))
    s.apply(ev("e2", "pending", path_id="P47"))
    reopened = pm.StateStore(path)
    assert len(reopened) == 2
    assert reopened.get("e1").state == "verified"
    assert reopened.get("e2").path_id == "P47"
    # idempotency survives the reload: re-delivery is still a no-op
    assert reopened.apply(ev("e1", "verified")).state == "verified"


def test_store_corrupt_file_raises(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{corrupt")
    with pytest.raises(pm.MatrixError):
        pm.StateStore(path)


def test_end_to_end_webhook_flow():
    m = small_matrix()
    s = pm.StateStore()
    for state in ("pending", "verified", "settled"):
        payload = json.dumps(
            {"event_id": "evt_9", "path_id": "P07", "state": state}).encode()
        assert pm.verify_webhook(payload, sign(payload, SECRET), SECRET)
        record = s.apply(pm.parse_webhook_event(payload, m))
    assert record.state == "settled"


# ---------------------------------------------------------------------------
# the real bundled catalog (payment_catalog.json) — reconciled 2026-11-18
# from CHECKOUT-SKU-CATALOG-53-v2-PRICED.md + products.json + plans.json
# ---------------------------------------------------------------------------

def _real_rows():
    return json.loads(pm.BUNDLED_CATALOG_PATH.read_text(encoding="utf-8"))


def test_real_catalog_loads_and_validates_every_entry():
    m = pm.load_bundled_catalog()
    assert len(m) == pm.RECONCILED_CATALOG_COUNT == 52
    # build_matrix already validated every entry; re-validate explicitly.
    for entry in m.entries:
        assert entry.validate() is entry


def test_real_catalog_fingerprint_stable_across_two_loads():
    a = pm.load_bundled_catalog()
    b = pm.load_catalog_json(pm.BUNDLED_CATALOG_PATH,
                             expected_count=pm.RECONCILED_CATALOG_COUNT)
    assert a.canonical_bytes() == b.canonical_bytes()
    assert a.fingerprint() == b.fingerprint()


def test_real_catalog_ids_match_catalog_of_record_exactly_once():
    rows = _real_rows()
    ids = [r["path_id"] for r in rows]
    # P01..P52 each exactly once. VENOM (SKU 53, ids V-INDIE / V-FLAGSHIP /
    # V-ANTIVENOM) is absent by design — see payment_matrix.py's comment:
    # against the P01..P53 scheme, P53 is missing and there are no extras.
    assert sorted(ids) == [f"P{i:02d}" for i in range(1, 53)]
    assert len(ids) == len(set(ids)) == 52
    m = pm.load_bundled_catalog()
    assert m.path_ids == tuple(sorted(ids))


def test_real_catalog_default_count_still_refuses():
    # The reconciled count is passed explicitly by load_bundled_catalog;
    # the module default of 53 must still refuse the 52-row table loudly.
    with pytest.raises(pm.MatrixCountError):
        pm.load_catalog_json(pm.BUNDLED_CATALOG_PATH)


def test_real_catalog_spot_values_from_sources():
    m = pm.load_bundled_catalog()
    # P01: 5% canon fee = 500 bps.
    assert m.get("P01").bps == 500
    # P03: founder-ratified 500 bps (addendum), not the 1000 researched median.
    assert m.get("P03").bps == 500
    # P09: founder-ratified 100 bps base, not the 222.5 researched median.
    assert m.get("P09").bps == 100
    # P08: $0.0075/validation = 750_000 micro-cents.
    assert m.get("P08").micro_cents == 750_000
    # P11: $0.0035/call = 350_000 micro-cents.
    assert m.get("P11").micro_cents == 350_000
    # P37: $250,000 one-time = 25_000_000 cents.
    assert m.get("P37").amount_cents == 25_000_000
    # P40: $112,000/yr subscription (plans.json YEAR cycle agrees).
    e40 = m.get("P40")
    assert (e40.kind, e40.amount_cents, e40.period) == (
        "subscription", 11_200_000, "year")
    # P50: Starter tier $297/mo (plans.json).
    e50 = m.get("P50")
    assert (e50.kind, e50.amount_cents, e50.period) == (
        "subscription", 29_700, "month")
    # P06 / P17: quote_only — no price exists in the catalog of record.
    assert m.get("P06").kind == "quote_only"
    assert m.get("P17").kind == "quote_only"


# ---------------------------------------------------------------------------
# no external telemetry — the module must not import any network machinery
# ---------------------------------------------------------------------------

def test_module_makes_no_network_calls():
    import sincor2.payment_matrix as module
    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"requests", "urllib", "socket", "http", "httpx", "aiohttp"}
    assert not (imported & forbidden), f"network imports present: {imported & forbidden}"
