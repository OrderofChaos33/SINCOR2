"""Deterministic 53-path payment routing matrix (Directive 1).

This module is the routing/verification core for the SINCOR payment-path
matrix. It is deliberately framework-free and dependency-free: no network
calls, no telemetry, no floats anywhere in money handling. It provides:

1. ``RouteEntry`` — one row of the matrix, with a validator derived from
   its ``kind``.
2. ``build_matrix`` — constructs a :class:`PaymentMatrix` from a
   declarative table, validates EVERY entry, and refuses a matrix whose
   entry count differs from the declared expected count (default 53), so
   a dropped path fails loudly at build time instead of silently 404ing
   at charge time.
3. Canonical serialization — deterministic JSON (sorted keys, compact
   separators, integers only) plus a SHA-256 fingerprint. The same matrix
   built from rows in any order produces identical bytes and fingerprint.
4. Webhook listener core — HMAC-SHA256 signature verification with
   ``hmac.compare_digest`` (fail-closed when the secret is unset) and
   payload shape validation that rejects unknown ``path_id`` values.
5. ``StateStore`` — an idempotent event log (in-memory, optionally backed
   by a JSON file) in which states move only forward:
   ``pending -> verified -> settled``. A duplicate ``event_id`` is a
   no-op returning the existing record; a backward or skipped transition
   raises.

Money representation (NO floats, ever):
- ``fixed`` / ``subscription`` amounts are whole cents in ``amount_cents``.
- ``per_call`` tariffs are sub-cent by nature (e.g. $0.0075/call), so they
  live exclusively in ``micro_cents`` — an integer count of micro-cents
  (1 cent = 1_000_000 micro-cents; $0.0075 = 750_000 micro-cents).
  ``amount_cents`` must be ``None`` for ``per_call`` entries so a tariff
  has exactly one source of truth.
- ``percentage`` fees are integer basis points in ``bps`` (500 = 5%).

The price table itself is NOT hardcoded here. The priced catalog of
record lives outside this module; at wiring time it is loaded with
:func:`load_catalog_json` (or passed to :func:`build_matrix` as rows) and
validated by the same code path. ``CATALOG_TABLE`` below is intentionally
empty — an empty table with ``expected_count=53`` fails the count check,
which is the correct behaviour until the real catalog is wired in.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple, Union

__all__ = [
    "BUNDLED_CATALOG_PATH",
    "CATALOG_TABLE",
    "DEFAULT_EXPECTED_COUNT",
    "RECONCILED_CATALOG_COUNT",
    "FIXED",
    "KINDS",
    "MICRO_CENTS_PER_CENT",
    "PERIODS",
    "PER_CALL",
    "PERCENTAGE",
    "QUOTE_ONLY",
    "STATES",
    "SUBSCRIPTION",
    "EventRecord",
    "InvalidRouteError",
    "InvalidTransitionError",
    "MatrixCountError",
    "MatrixError",
    "PaymentMatrix",
    "RouteEntry",
    "StateStore",
    "UnknownPathError",
    "WebhookEvent",
    "WebhookPayloadError",
    "WebhookSecretError",
    "build_matrix",
    "canonical_bytes",
    "fingerprint",
    "load_bundled_catalog",
    "load_catalog_json",
    "micro_cents_from_cents",
    "parse_webhook_event",
    "verify_webhook",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

FIXED = "fixed"
SUBSCRIPTION = "subscription"
PERCENTAGE = "percentage"
PER_CALL = "per_call"
QUOTE_ONLY = "quote_only"

KINDS = frozenset({FIXED, SUBSCRIPTION, PERCENTAGE, PER_CALL, QUOTE_ONLY})

PERIODS = frozenset({"month", "year"})

#: States an event may occupy, in the only order they may be reached.
STATES = ("pending", "verified", "settled")
_STATE_RANK = {state: rank for rank, state in enumerate(STATES)}

#: The matrix is declared to carry exactly this many paths by default.
DEFAULT_EXPECTED_COUNT = 53

#: 1 cent == 1_000_000 micro-cents. Integer scaling only; never a float.
MICRO_CENTS_PER_CENT = 1_000_000

_PATH_ID_RE = re.compile(r"^P[0-9]+$")


# --------------------------------------------------------------------------
# Errors — every validation failure names the offending path_id.
# --------------------------------------------------------------------------


class MatrixError(ValueError):
    """Base class for all payment-matrix errors."""


class InvalidRouteError(MatrixError):
    """A route entry failed validation. The message names the path_id."""


class MatrixCountError(MatrixError):
    """The built matrix's entry count != the declared expected count."""


class UnknownPathError(MatrixError):
    """A path_id is not present in the matrix."""


class InvalidTransitionError(MatrixError):
    """An event attempted a backward or skipped state transition."""


class WebhookSecretError(MatrixError):
    """Webhook verification was attempted with no secret configured."""


class WebhookPayloadError(MatrixError):
    """A webhook payload failed shape validation."""


# --------------------------------------------------------------------------
# RouteEntry + per-kind validators
# --------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    """True only for a real int — bools and floats are not money."""
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(path_id: Any, reason: str) -> None:
    raise InvalidRouteError(f"invalid route entry for path_id={path_id!r}: {reason}")


def _check_common(entry: "RouteEntry") -> None:
    pid = entry.path_id
    if not isinstance(pid, str) or not _PATH_ID_RE.match(pid):
        _fail(pid, "path_id must be a string like 'P01'")
    if entry.kind not in KINDS:
        _fail(pid, f"kind must be one of {sorted(KINDS)}, got {entry.kind!r}")
    for field_name in ("amount_cents", "micro_cents", "bps"):
        value = getattr(entry, field_name)
        if value is not None and not _is_int(value):
            _fail(pid, f"{field_name} must be an int or None, got {value!r} "
                       "(floats and bools are forbidden in money handling)")
    if entry.period is not None and entry.period not in PERIODS:
        _fail(pid, f"period must be one of {sorted(PERIODS)} or None, got {entry.period!r}")


def _validate_fixed(entry: "RouteEntry") -> None:
    if entry.amount_cents is None or entry.amount_cents <= 0:
        _fail(entry.path_id, "fixed requires amount_cents > 0")
    if entry.micro_cents is not None:
        _fail(entry.path_id, "fixed must not set micro_cents")
    if entry.bps is not None:
        _fail(entry.path_id, "fixed must not set bps")
    if entry.period is not None:
        _fail(entry.path_id, "fixed must not set period")


def _validate_subscription(entry: "RouteEntry") -> None:
    if entry.amount_cents is None or entry.amount_cents <= 0:
        _fail(entry.path_id, "subscription requires amount_cents > 0")
    if entry.period is None:
        _fail(entry.path_id, "subscription requires a period ('month' or 'year')")
    if entry.micro_cents is not None:
        _fail(entry.path_id, "subscription must not set micro_cents")
    if entry.bps is not None:
        _fail(entry.path_id, "subscription must not set bps")


def _validate_percentage(entry: "RouteEntry") -> None:
    if entry.bps is None or not (0 < entry.bps <= 10_000):
        _fail(entry.path_id, "percentage requires 0 < bps <= 10000")
    if entry.amount_cents is not None:
        _fail(entry.path_id, "percentage must not set amount_cents")
    if entry.micro_cents is not None:
        _fail(entry.path_id, "percentage must not set micro_cents")
    if entry.period is not None:
        _fail(entry.path_id, "percentage must not set period")


def _validate_per_call(entry: "RouteEntry") -> None:
    # Tariffs are sub-cent; the scaled micro_cents field is the ONLY amount.
    if entry.micro_cents is None or entry.micro_cents <= 0:
        _fail(entry.path_id, "per_call requires micro_cents > 0 "
                             "(sub-cent tariffs are integer micro-cents)")
    if entry.amount_cents is not None:
        _fail(entry.path_id, "per_call must not set amount_cents; "
                             "use micro_cents as the single source of truth")
    if entry.bps is not None:
        _fail(entry.path_id, "per_call must not set bps")
    if entry.period is not None:
        _fail(entry.path_id, "per_call must not set period")


def _validate_quote_only(entry: "RouteEntry") -> None:
    if entry.amount_cents is not None:
        _fail(entry.path_id, "quote_only must not set amount_cents")
    if entry.micro_cents is not None:
        _fail(entry.path_id, "quote_only must not set micro_cents")
    if entry.bps is not None:
        _fail(entry.path_id, "quote_only must not set bps")
    if entry.period is not None:
        _fail(entry.path_id, "quote_only must not set period")


_VALIDATORS: Dict[str, Callable[["RouteEntry"], None]] = {
    FIXED: _validate_fixed,
    SUBSCRIPTION: _validate_subscription,
    PERCENTAGE: _validate_percentage,
    PER_CALL: _validate_per_call,
    QUOTE_ONLY: _validate_quote_only,
}


@dataclass(frozen=True)
class RouteEntry:
    """One row of the payment routing matrix.

    ``validator`` is derived from ``kind``: :meth:`validate` dispatches to
    the per-kind validator in ``_VALIDATORS``. Construction does not
    validate — call :meth:`validate` (``build_matrix`` always does).
    """

    path_id: str
    kind: str
    amount_cents: Optional[int] = None
    micro_cents: Optional[int] = None
    bps: Optional[int] = None
    period: Optional[str] = None

    def validate(self) -> "RouteEntry":
        _check_common(self)
        _VALIDATORS[self.kind](self)
        return self

    @property
    def validator(self) -> Callable[["RouteEntry"], None]:
        """The validation function derived from this entry's kind."""
        return _VALIDATORS[self.kind]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path_id": self.path_id,
            "kind": self.kind,
            "amount_cents": self.amount_cents,
            "micro_cents": self.micro_cents,
            "bps": self.bps,
            "period": self.period,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "RouteEntry":
        if not isinstance(row, Mapping):
            raise InvalidRouteError(f"route row must be a mapping, got {row!r}")
        known = {"path_id", "kind", "amount_cents", "micro_cents", "bps", "period"}
        unknown = set(row) - known
        if unknown:
            raise InvalidRouteError(
                f"route row for path_id={row.get('path_id')!r} has unknown "
                f"fields: {sorted(unknown)}")
        return cls(
            path_id=row.get("path_id"),
            kind=row.get("kind"),
            amount_cents=row.get("amount_cents"),
            micro_cents=row.get("micro_cents"),
            bps=row.get("bps"),
            period=row.get("period"),
        )


def micro_cents_from_cents(cents: int) -> int:
    """Scale whole cents to micro-cents with integer arithmetic only."""
    if not _is_int(cents):
        raise InvalidRouteError(f"cents must be an int, got {cents!r}")
    return cents * MICRO_CENTS_PER_CENT


# --------------------------------------------------------------------------
# The declarative table.
#
# Intentionally EMPTY in the repository: prices are loaded from the priced
# catalog of record at wiring time (see load_catalog_json). Building the
# matrix from this table with the default expected count of 53 raises
# MatrixCountError — loudly, by design — until the real catalog is wired in.
# --------------------------------------------------------------------------

CATALOG_TABLE: Tuple[Mapping[str, Any], ...] = ()


# --------------------------------------------------------------------------
# PaymentMatrix + builder
# --------------------------------------------------------------------------


class PaymentMatrix:
    """An immutable, validated set of route entries keyed by path_id."""

    def __init__(self, entries: Iterable[RouteEntry]):
        ordered = tuple(sorted(entries, key=lambda e: e.path_id))
        self._entries: Tuple[RouteEntry, ...] = ordered
        self._by_id: Dict[str, RouteEntry] = {e.path_id: e for e in ordered}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, path_id: object) -> bool:
        return path_id in self._by_id

    def __iter__(self):
        return iter(self._entries)

    @property
    def entries(self) -> Tuple[RouteEntry, ...]:
        return self._entries

    @property
    def path_ids(self) -> Tuple[str, ...]:
        return tuple(e.path_id for e in self._entries)

    def get(self, path_id: str) -> RouteEntry:
        try:
            return self._by_id[path_id]
        except KeyError:
            raise UnknownPathError(f"unknown path_id: {path_id!r}") from None

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self._entries)

    def fingerprint(self) -> str:
        return fingerprint(self._entries)


def build_matrix(
    table: Iterable[Union[RouteEntry, Mapping[str, Any]]],
    expected_count: Optional[int] = DEFAULT_EXPECTED_COUNT,
) -> PaymentMatrix:
    """Build and fully validate a :class:`PaymentMatrix` from a table.

    Every entry is validated by its kind-derived validator; a duplicate
    path_id is rejected; and if ``expected_count`` is not None the matrix
    must contain exactly that many entries or :class:`MatrixCountError`
    is raised — a dropped path fails loudly here, not at charge time.
    """
    entries = []
    seen = set()
    for row in table:
        entry = row if isinstance(row, RouteEntry) else RouteEntry.from_dict(row)
        entry.validate()
        if entry.path_id in seen:
            _fail(entry.path_id, "duplicate path_id in matrix table")
        seen.add(entry.path_id)
        entries.append(entry)
    if expected_count is not None and len(entries) != expected_count:
        raise MatrixCountError(
            f"payment matrix has {len(entries)} entries, expected "
            f"{expected_count}; refusing to build a partial matrix")
    return PaymentMatrix(entries)


def load_catalog_json(
    source: Union[str, Path],
    expected_count: Optional[int] = DEFAULT_EXPECTED_COUNT,
) -> PaymentMatrix:
    """Load a priced catalog JSON file and build a validated matrix.

    The catalog file is a JSON list of row objects with the RouteEntry
    fields (``path_id``, ``kind``, ``amount_cents``, ``micro_cents``,
    ``bps``, ``period``). This is the wiring-time entry point for the
    priced catalog of record; no prices live in this module.
    """
    with open(source, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    if not isinstance(rows, list):
        raise MatrixError(f"catalog at {source!r} must be a JSON list of rows")
    return build_matrix(rows, expected_count=expected_count)


# --------------------------------------------------------------------------
# Bundled real catalog (payment_catalog.json, next to this module).
#
# Reconciled 2026-11-18 from the three sources of record:
# CHECKOUT-SKU-CATALOG-53-v2-PRICED.md (priced catalog of record, incl.
# its 2026-11-16 founder ratification addendum — P03 is 500 bps and the
# P09 base is 100 bps there, superseding the researched medians),
# paypal-catalog/products.json (55 objects) and paypal-catalog/plans.json
# (8 subscription plans). Every value in the JSON is copied from those
# sources; no number was invented.
#
# COUNT: the catalog of record has 53 SKUs — P01..P52 plus VENOM as
# SKU 53. VENOM's variants carry the ids V-INDIE / V-FLAGSHIP /
# V-ANTIVENOM, which do not match the ^P[0-9]+$ path_id scheme, and its
# three price forms (a $500-$1,500 range, a $9,500 fixed price, and a
# per-engagement quote) cannot be collapsed into a single route entry
# without inventing data. The bundled table therefore holds exactly 52
# entries (P01..P52): against the P01..P53 scheme, P53 is MISSING and
# there are no extra ids. DEFAULT_EXPECTED_COUNT stays 53; the bundled
# build below passes the reconciled count EXPLICITLY so the difference
# is a stated fact, not a silent default change.
#
# QUOTE_ONLY ENTRIES WHOSE PRICE IS ABSENT FROM THE CATALOG OF RECORD:
# - P06: the deployment/engagement price is QUOTE in every source (its
#   50% figure is a contract penalty parameter, not a service price).
# - P17: no buyer price exists in any source — it is SINCOR's own
#   trading book, an internal PnL path, not a purchasable offer.
# Both are entered as kind "quote_only" with no amount, per the
# fail-forward rule: represent the true kind, never invent a number.
#
# MULTI-FORM SKUs: the matrix holds ONE entry per path_id, so where a
# SKU carries two true price forms the primary form is entered and the
# secondary is recorded here rather than dropped silently:
# - P07: per_call $0.01 quick-scan entered; 500 USDC Audit Express
#   (fixed) is the secondary form.
# - P18: percentage 100 bps (1% AUM/yr management) entered; the 15%
#   performance fee is the secondary form.
# - P41: percentage 100 bps (1.0% AUM/yr) entered; the 10% performance
#   fee is the secondary form.
# - P47 / P48: subscription $149/month (plans.json) entered; the $49
#   one-off report (fixed) is the secondary form.
# - P50: subscription $297/month Starter tier entered; the $997
#   Professional and $2,997 Enterprise tiers are additional plans for
#   the same path and cannot be separate entries.
# - P51: fixed $50,000 one-time engagement entered; the $3,000/month
#   platform-fee plan (plans.json) is the secondary form.
# --------------------------------------------------------------------------

#: Number of entries in the bundled payment_catalog.json (see above).
RECONCILED_CATALOG_COUNT = 52

#: Path of the bundled reconciled catalog JSON.
BUNDLED_CATALOG_PATH = Path(__file__).with_name("payment_catalog.json")


def load_bundled_catalog() -> PaymentMatrix:
    """Build the matrix from the bundled reconciled catalog JSON.

    Uses RECONCILED_CATALOG_COUNT explicitly (52 — P01..P52; VENOM
    SKU 53 is not representable in this scheme, see the comment above),
    NOT the DEFAULT_EXPECTED_COUNT of 53.
    """
    return load_catalog_json(
        BUNDLED_CATALOG_PATH, expected_count=RECONCILED_CATALOG_COUNT)


# --------------------------------------------------------------------------
# Canonical serialization + fingerprint
# --------------------------------------------------------------------------


def canonical_bytes(entries: Iterable[RouteEntry]) -> bytes:
    """Deterministic JSON for a set of entries.

    Entries are sorted by path_id, keys are sorted, separators are
    compact, and validation guarantees every numeric value is an int —
    so the same entries in any input order produce identical bytes.
    """
    rows = [e.to_dict() for e in sorted(entries, key=lambda e: e.path_id)]
    doc = {"version": 1, "entries": rows}
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprint(entries: Iterable[RouteEntry]) -> str:
    """SHA-256 hex fingerprint of the canonical serialization."""
    return hashlib.sha256(canonical_bytes(entries)).hexdigest()


# --------------------------------------------------------------------------
# Webhook listener core (framework-free)
# --------------------------------------------------------------------------


def _secret_bytes(secret: Union[str, bytes, None]) -> bytes:
    if secret is None:
        raise WebhookSecretError("webhook secret is not configured; failing closed")
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if not raw:
        raise WebhookSecretError("webhook secret is empty; failing closed")
    return raw


def verify_webhook(
    payload_bytes: bytes,
    signature_header: Optional[str],
    secret: Union[str, bytes, None],
) -> bool:
    """Verify an HMAC-SHA256 webhook signature, fail-closed.

    Accepts the signature as a bare hex digest or in ``sha256=<hex>``
    form. Comparison uses ``hmac.compare_digest``. A missing/empty
    secret raises :class:`WebhookSecretError` — an unconfigured listener
    never accepts anything. A missing or malformed signature returns
    False.
    """
    key = _secret_bytes(secret)
    if not signature_header or not isinstance(signature_header, str):
        return False
    candidate = signature_header.strip()
    if candidate.startswith("sha256="):
        candidate = candidate[len("sha256="):]
    expected = hmac.new(key, bytes(payload_bytes), hashlib.sha256).hexdigest()
    return hmac.compare_digest(candidate, expected)


@dataclass(frozen=True)
class WebhookEvent:
    event_id: str
    path_id: str
    state: str


def parse_webhook_event(payload_bytes: bytes, matrix: PaymentMatrix) -> WebhookEvent:
    """Parse and validate a webhook payload against the matrix.

    Required shape: a JSON object with non-empty string ``event_id``,
    a ``path_id`` present in ``matrix``, and a ``state`` in STATES.
    Unknown path_ids are rejected — an event for a path we do not route
    is never recorded.
    """
    try:
        doc = json.loads(bytes(payload_bytes).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookPayloadError(f"webhook payload is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise WebhookPayloadError("webhook payload must be a JSON object")
    event_id = doc.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise WebhookPayloadError("webhook payload requires a non-empty string event_id")
    path_id = doc.get("path_id")
    if not isinstance(path_id, str) or not path_id:
        raise WebhookPayloadError("webhook payload requires a string path_id")
    if path_id not in matrix:
        raise UnknownPathError(f"webhook event for unknown path_id: {path_id!r}")
    state = doc.get("state")
    if state not in _STATE_RANK:
        raise WebhookPayloadError(
            f"webhook payload state must be one of {list(STATES)}, got {state!r}")
    return WebhookEvent(event_id=event_id, path_id=path_id, state=state)


# --------------------------------------------------------------------------
# State verification store — idempotent, forward-only, optionally file-backed
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    path_id: str
    state: str

    def to_dict(self) -> Dict[str, Any]:
        return {"event_id": self.event_id, "path_id": self.path_id, "state": self.state}


class StateStore:
    """Idempotent log of webhook event states.

    Rules:
    - A new event_id must enter at ``pending``; entering at any later
      state is a skipped transition and raises.
    - Re-applying the same event_id with the same path_id and state is a
      no-op returning the existing record (safe webhook redelivery).
    - Re-applying with the next state advances the record; any backward
      or skipped transition raises :class:`InvalidTransitionError`.
    - Re-applying an event_id under a different path_id raises.

    When ``path`` is given, every successful mutation is persisted to
    that JSON file atomically (write-temp-then-replace), and an existing
    file is loaded on construction. A corrupt file raises — the store
    never silently starts empty over lost state.
    """

    def __init__(self, path: Union[str, Path, None] = None):
        self._path = Path(path) if path is not None else None
        self._records: Dict[str, EventRecord] = {}
        self._lock = threading.Lock()
        if self._path is not None and self._path.exists():
            self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
            rows = doc["records"]
            for row in rows:
                record = EventRecord(
                    event_id=row["event_id"], path_id=row["path_id"], state=row["state"])
                if record.state not in _STATE_RANK:
                    raise ValueError(f"bad state {record.state!r}")
                self._records[record.event_id] = record
        except (ValueError, KeyError, TypeError) as exc:
            raise MatrixError(f"state store file {self._path} is corrupt: {exc}") from exc

    def _persist(self) -> None:
        if self._path is None:
            return
        rows = [r.to_dict() for r in sorted(self._records.values(), key=lambda r: r.event_id)]
        blob = json.dumps({"version": 1, "records": rows},
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self._path)

    # -- core API ---------------------------------------------------------

    def apply(self, event: WebhookEvent) -> EventRecord:
        """Apply an event under the forward-only, idempotent rules."""
        with self._lock:
            existing = self._records.get(event.event_id)
            if existing is None:
                if event.state != STATES[0]:
                    raise InvalidTransitionError(
                        f"event {event.event_id!r} enters at {event.state!r}; "
                        f"new events must enter at {STATES[0]!r}")
                record = EventRecord(event.event_id, event.path_id, event.state)
                self._records[event.event_id] = record
                self._persist()
                return record
            if existing.path_id != event.path_id:
                raise InvalidTransitionError(
                    f"event {event.event_id!r} is bound to path_id "
                    f"{existing.path_id!r}, cannot rebind to {event.path_id!r}")
            if existing.state == event.state:
                return existing  # duplicate delivery: no-op
            if _STATE_RANK[event.state] != _STATE_RANK[existing.state] + 1:
                raise InvalidTransitionError(
                    f"event {event.event_id!r}: transition {existing.state!r} -> "
                    f"{event.state!r} is not the single forward step "
                    f"(order: {' -> '.join(STATES)})")
            record = EventRecord(event.event_id, event.path_id, event.state)
            self._records[event.event_id] = record
            self._persist()
            return record

    def get(self, event_id: str) -> Optional[EventRecord]:
        return self._records.get(event_id)

    def records(self) -> Tuple[EventRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda r: r.event_id))

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, event_id: object) -> bool:
        return event_id in self._records
