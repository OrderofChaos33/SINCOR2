"""Unified API authorization: tenant-scoped runtime context + guard model.

RT-01_ID-05 — replaces the patchwork of per-blueprint guards
(``_require_admin`` in mvp_app, ``_check_admin`` in admin.py,
``_customer_or_admin`` in vitals.py, bare ``jwt_required`` in billing.py,
and no guard at all on routes like ``GET /api/orders/<email>``) with one
model:

* :class:`Principal` — a *verified* identity. ``tenant_id`` is always
  resolved server-side (from the user/agent record), NEVER taken from
  request parameters, bodies, or headers supplied by the client.
* :func:`resolve_principal` — builds a Principal from the current
  request (JWT first, Flask session second). Raises :class:`AuthzError`
  (401) when no verifiable identity exists.
* :func:`require_role` / :func:`require_tenant_owner` — Flask route
  decorators implementing the guard vocabulary used by
  ``authz_matrix.json``: ``public | authenticated | customer_owner |
  operator | admin``.
* :class:`AuthzMatrix` — loader/validator for the machine-readable
  endpoint→guard mapping in ``src/sincor2/authz_matrix.json``.
* :class:`ScopedDTO` / ``to_public_*_dto`` — minimal response DTOs that
  strip non-allowlisted fields before they leave the server.
* :class:`RuntimeContext` + :func:`attach_context` /
  :func:`context_from_payload` — immutable context propagation across
  queue/worker boundaries (see :mod:`sincor2.task_queue`).

This module is additive: existing blueprints keep working untouched.
New or reworked routes should adopt these decorators; the matrix records
both the *required* guard and the *current* guard so gaps are visible.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, fields
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from flask import g, jsonify, request

logger = logging.getLogger("sincor.authz")

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Principal roles in the unified guard model."""

    customer = "customer"  # end user / buyer, tenant-scoped
    agent = "agent"  # registered A2A agent, tenant-scoped to its owner
    operator = "operator"  # staff console access (session-gated today)
    admin = "admin"  # full administrative access (JWT identity-gated)
    system = "system"  # internal service-to-service (never from browsers)


class AuthzError(Exception):
    """Authorization failure carrying the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """A verified identity.

    ``tenant_id`` is resolved server-side from the identity's own record.
    It is NEVER populated from client-supplied values — see
    :func:`resolve_principal`.
    """

    id: str
    role: Role
    tenant_id: str
    email: Optional[str] = None
    wallet: Optional[str] = None


# Pluggable server-side tenant resolution: principal_id -> tenant_id.
# Deployments backed by a user store should call set_tenant_resolver()
# at startup so tenants come from the database, not from requests.
_TenantResolver = Callable[[str, Role], str]


def _default_tenant_resolver(principal_id: str, role: Role) -> str:
    if role in (Role.admin, Role.operator, Role.system):
        return "system"
    return f"tenant:{principal_id}"


_tenant_resolver: _TenantResolver = _default_tenant_resolver


def set_tenant_resolver(fn: _TenantResolver) -> None:
    """Install a server-side tenant resolver (principal_id, role) -> tenant_id."""
    global _tenant_resolver
    _tenant_resolver = fn


# Client-controlled locations that must NEVER influence tenant resolution.
# Read (and ignored) here so resolve_principal is explicit about the drop.
_CLIENT_TENANT_FIELDS = ("tenant_id", "tenant", "account_id", "customer_id", "org_id")


def _drop_client_tenant(req) -> None:
    supplied = {}
    try:
        for f in _CLIENT_TENANT_FIELDS:
            v = req.values.get(f)
            if v:
                supplied[f] = v
        if req.is_json:
            body = req.get_json(silent=True) or {}
            if isinstance(body, dict):
                for f in _CLIENT_TENANT_FIELDS:
                    if body.get(f):
                        supplied[f] = body[f]
    except Exception:
        pass
    if supplied:
        # Debug only: never echo client values into responses or errors.
        logger.debug("Ignoring client-supplied tenant fields: %s", sorted(supplied))


def _principal_from_jwt() -> Optional[Principal]:
    try:
        from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request
    except Exception:
        return None
    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        return None
    identity = get_jwt_identity()
    if not identity:
        return None
    claims = get_jwt() or {}
    raw_role = str(claims.get("role") or "customer").lower()
    try:
        role = Role(raw_role)
    except ValueError:
        role = Role.customer
    email = claims.get("email")
    wallet = claims.get("wallet")
    tenant_id = _tenant_resolver(str(identity), role)
    return Principal(id=str(identity), role=role, tenant_id=tenant_id,
                     email=email, wallet=wallet)


def _principal_from_session() -> Optional[Principal]:
    from flask import session

    try:
        if session.get("is_admin"):
            ident = str(session.get("admin_username") or session.get("username") or "admin")
            return Principal(id=ident, role=Role.admin,
                             tenant_id=_tenant_resolver(ident, Role.admin))
        agent_id = session.get("agent_id")
        if agent_id:
            ident = str(agent_id)
            return Principal(id=ident, role=Role.agent,
                             tenant_id=_tenant_resolver(ident, Role.agent),
                             wallet=session.get("agent_wallet"))
        email = session.get("user_email") or session.get("email")
        if email:
            ident = str(email)
            return Principal(id=ident, role=Role.customer,
                             tenant_id=_tenant_resolver(ident, Role.customer),
                             email=ident if "@" in ident else None)
    except Exception:
        return None
    return None


def resolve_principal(req=None) -> Principal:
    """Resolve the verified principal for the current request.

    Identity sources, in order: JWT Bearer token, then Flask session.
    The tenant is resolved server-side from the identity via the
    configured tenant resolver. Any ``tenant_id``/``tenant``/``account_id``
    supplied by the client (query, form, JSON body, headers) is
    explicitly ignored.

    Trust contract: the ``role`` is taken from the *server-signed* JWT
    claim. Tokens must only be minted server-side after verifying the
    role (e.g. ``SINCORAuth.authenticate_user`` mints ``role: admin``
    only for the admin user). A client cannot forge a role without the
    JWT signing key; never accept a role from any unsigned source.

    Raises:
        AuthzError: 401 when no verifiable identity is present.
    """
    req = req or request
    _drop_client_tenant(req)
    principal = _principal_from_jwt() or _principal_from_session()
    if principal is None:
        raise AuthzError("Authentication required", 401)
    return principal


def current_principal() -> Optional[Principal]:
    """Return the principal stashed on ``flask.g`` by the decorators, if any."""
    return getattr(g, "principal", None)


def _deny(message: str, status_code: int):
    # Generic message on purpose: never leak which tenant/resource exists.
    return jsonify({"success": False, "error": message,
                    "error_code": "forbidden" if status_code == 403 else "unauthorized"}), status_code


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def require_role(*roles: Role):
    """Flask decorator: require an authenticated principal with one of *roles*.

    Roles may be passed as :class:`Role` members or plain strings.
    401 when unauthenticated, 403 when the role is not permitted.
    Stashes the principal on ``flask.g.principal``.
    """
    wanted = {r if isinstance(r, Role) else Role(str(r).lower()) for r in roles}

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                principal = resolve_principal()
            except AuthzError as exc:
                return _deny(exc.message, exc.status_code)
            if principal.role not in wanted:
                logger.warning("authz role deny: principal=%s role=%s wanted=%s path=%s",
                               principal.id, principal.role.value,
                               sorted(r.value for r in wanted), request.path)
                return _deny("Insufficient role", 403)
            g.principal = principal
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_tenant_owner(resource_owner_fn: Callable[[], Optional[str]]):
    """Flask decorator: principal's tenant must own the target resource.

    ``resource_owner_fn`` is a zero-arg callable returning the resource's
    owner tenant_id (resolved server-side, e.g. from the DB row). The
    principal's tenant (server-resolved) must equal it.

    ``admin`` and ``system`` roles bypass the ownership check for support
    tooling; the bypass is logged. Denials return a generic 403 and never
    include resource data or the owner's identity.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                principal = resolve_principal()
            except AuthzError as exc:
                return _deny(exc.message, exc.status_code)
            owner_tenant = resource_owner_fn()
            if owner_tenant is None:
                return _deny("Not found", 404)
            if principal.role in (Role.admin, Role.system):
                logger.info("authz tenant bypass: principal=%s role=%s path=%s",
                            principal.id, principal.role.value, request.path)
            elif principal.tenant_id != owner_tenant:
                logger.warning("authz tenant deny: principal=%s tenant=%s path=%s",
                               principal.id, principal.tenant_id, request.path)
                return _deny("Access denied", 403)
            g.principal = principal
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Authz matrix (machine-readable endpoint -> guard mapping)
# ---------------------------------------------------------------------------

_MATRIX_FILENAME = "authz_matrix.json"
_GUARDS = ("public", "authenticated", "customer_owner", "operator", "admin")
_PATH_PARAM_RE = re.compile(r"<(?:[^:<>]+:)?([^<>]+)>")


def matrix_path() -> str:
    """Filesystem path of the bundled authz matrix JSON."""
    import os

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _MATRIX_FILENAME)


class AuthzMatrix:
    """Loads, validates, and queries ``authz_matrix.json``.

    Each entry: ``{method, path_pattern, guard, tenant_scoped, notes}``
    plus informational ``current_guard`` / ``enforced`` / ``mounted`` /
    ``source`` fields describing today's actual state.
    """

    def __init__(self, entries: List[Dict[str, Any]]):
        self.entries = entries
        self._compiled = []
        # Mounted (production) entries win over legacy/unmounted ones that
        # happen to share a path.
        ordered = sorted(entries, key=lambda e: (not e.get("mounted", False), e.get("source", "")))
        for e in ordered:
            pattern = "^" + _PATH_PARAM_RE.sub(r"[^/]+", e["path_pattern"]) + "$"
            methods = {m.strip().upper() for m in str(e["method"]).split(",")}
            self._compiled.append((methods, re.compile(pattern), e))

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AuthzMatrix":
        """Load the matrix from JSON; raises on unreadable/invalid JSON."""
        with open(path or matrix_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("endpoints", data) if isinstance(data, dict) else data
        return cls(entries)

    def validate(self) -> List[str]:
        """Return a list of schema violations (empty when valid)."""
        errors = []
        seen = set()
        for i, e in enumerate(self.entries):
            where = f"entries[{i}]"
            for key in ("method", "path_pattern", "guard", "tenant_scoped"):
                if key not in e:
                    errors.append(f"{where}: missing '{key}'")
            if e.get("guard") not in _GUARDS:
                errors.append(f"{where}: guard {e.get('guard')!r} not in {_GUARDS}")
            if not isinstance(e.get("tenant_scoped"), bool):
                errors.append(f"{where}: tenant_scoped must be bool")
            if not str(e.get("path_pattern", "")).startswith("/"):
                errors.append(f"{where}: path_pattern must start with '/'")
            key = (str(e.get("method", "")).upper(), e.get("path_pattern"), e.get("source"))
            if key in seen:
                errors.append(f"{where}: duplicate {key}")
            seen.add(key)
        # At most one mounted entry may claim a (method, path).
        mounted_seen = set()
        for i, e in enumerate(self.entries):
            if e.get("mounted"):
                for meth in {m.strip().upper() for m in str(e.get("method", "")).split(",")}:
                    key = (meth, e.get("path_pattern"))
                    if key in mounted_seen:
                        errors.append(f"entries[{i}]: duplicate mounted {key}")
                    mounted_seen.add(key)
        return errors

    def guard_for(self, method: str, path: str) -> Optional[Dict[str, Any]]:
        """Return the matrix entry matching an actual request path, if any.

        Mounted (production) entries take precedence over legacy ones.
        """
        method = method.upper()
        for methods, rx, entry in self._compiled:
            if ("*" in methods or method in methods) and rx.match(path):
                return entry
        return None

    def gaps(self) -> List[Dict[str, Any]]:
        """Entries where the required guard is not enforced today."""
        return [e for e in self.entries if not e.get("enforced", True)]


# ---------------------------------------------------------------------------
# Minimal DTOs — strip non-allowlisted fields from responses
# ---------------------------------------------------------------------------


def scoped_dto(record: Mapping[str, Any], allowlist: Iterable[str]) -> Dict[str, Any]:
    """Return a new dict with only allowlisted keys present in *record*.

    Anything not on the allowlist (internal ids, cost fields, raw payloads,
    other tenants' data) is dropped. Never mutates the input.
    """
    allowed = set(allowlist)
    return {k: record[k] for k in allowed if k in record}


ORDER_PUBLIC_FIELDS = (
    "order_id", "product_name", "amount", "currency",
    "payment_status", "delivery_status", "created_at",
)

TASK_PUBLIC_FIELDS = (
    "task_id", "kind", "status", "progress",
    "created_at", "updated_at", "poll_url",
)

EVIDENCE_PUBLIC_FIELDS = (
    "entry_id", "kind", "timestamp", "suite", "recorded_by",
)


def to_public_order_dto(order: Mapping[str, Any]) -> Dict[str, Any]:
    """Public order view: drops delivery_url, customer PII, internal notes."""
    return scoped_dto(order, ORDER_PUBLIC_FIELDS)


def to_public_task_dto(task: Mapping[str, Any]) -> Dict[str, Any]:
    """Public task/job view: drops handler payloads and internal result blobs."""
    dto = scoped_dto(task, TASK_PUBLIC_FIELDS)
    # Never leak raw result payloads; surface a presence flag instead.
    if "result" in task:
        dto["has_result"] = task["result"] is not None
    return dto


def to_public_evidence_dto(evidence: Mapping[str, Any]) -> Dict[str, Any]:
    """Public evidence-ledger view: drops internal details/cost fields."""
    return scoped_dto(evidence, EVIDENCE_PUBLIC_FIELDS)


# ---------------------------------------------------------------------------
# Runtime context propagation across queue/worker boundaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeContext:
    """Immutable context that travels with a unit of work.

    Frozen: once created it cannot be mutated — workers receive a copy,
    never a live reference. ``principal_id``/``tenant_id``/``role`` are a
    *record* of who enqueued the work, not a credential: workers must
    re-resolve authorization server-side (e.g. re-check tenant ownership
    of the resource before acting) rather than trusting these fields
    blindly, because the queue store is a trusted-but-dumb transport.
    """

    principal_id: str
    tenant_id: str
    role: str  # Role value, e.g. "customer"
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    purpose: Optional[str] = None
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuntimeContext":
        known = {f.name for f in fields(cls)}
        return cls(**{k: data[k] for k in known if k in data})  # type: ignore[arg-type]

    @classmethod
    def from_principal(cls, principal: Principal, *,
                       agent_id: Optional[str] = None,
                       task_id: Optional[str] = None,
                       purpose: Optional[str] = None,
                       idempotency_key: Optional[str] = None) -> "RuntimeContext":
        """Build a context from a verified principal at enqueue time."""
        return cls(
            principal_id=principal.id,
            tenant_id=principal.tenant_id,
            role=principal.role.value,
            agent_id=agent_id,
            task_id=task_id,
            purpose=purpose,
            idempotency_key=idempotency_key or uuid.uuid4().hex,
        )


#: Reserved payload key. task_queue payloads must never use this key for
#: anything else; attach_context() refuses to overwrite an existing value.
CONTEXT_PAYLOAD_KEY = "_sincor_runtime_context"


def attach_context(payload: Optional[Dict[str, Any]], ctx: RuntimeContext) -> Dict[str, Any]:
    """Attach a RuntimeContext to a task_queue payload.

    Intended call site (additive — task_queue itself is untouched)::

        from sincor2 import authz
        from sincor2.task_queue import enqueue

        ctx = authz.RuntimeContext.from_principal(
            authz.current_principal(), task_id=..., purpose="a2a-dispatch")
        job = enqueue("a2a_dispatch", authz.attach_context({"bid_id": ...}, ctx))

    Raises:
        ValueError: if the payload already carries a context (fail closed —
            callers must not stack or replace contexts).
    """
    payload = dict(payload or {})
    if CONTEXT_PAYLOAD_KEY in payload:
        raise ValueError(f"payload already carries {CONTEXT_PAYLOAD_KEY!r}; refusing to overwrite")
    payload[CONTEXT_PAYLOAD_KEY] = ctx.to_dict()
    return payload


def context_from_payload(payload: Mapping[str, Any]) -> Optional[RuntimeContext]:
    """Rehydrate the RuntimeContext a worker received inside its payload.

    Returns None when the payload carries no context (legacy jobs).
    Workers should treat a missing context as "least privilege" and
    re-resolve any authorization from server-side state.
    """
    raw = payload.get(CONTEXT_PAYLOAD_KEY)
    if not raw:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"{CONTEXT_PAYLOAD_KEY!r} is malformed")
    return RuntimeContext.from_dict(raw)


def propagate_context(ctx: RuntimeContext,
                      payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convenience alias for :func:`attach_context` at queue boundaries."""
    return attach_context(payload, ctx)
