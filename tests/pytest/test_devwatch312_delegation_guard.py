"""Dev-watch item 312: EIP-7702 delegation lifecycle guard.

Background (sven.eth drain, 2026-10-14): a signed EIP-7702 authorization
is a STANDING capability. Anyone holding the signed authorization tuple
can attach the delegation later, and the EOA keeps executing the
delegate's code until the delegation is explicitly cleared. Treating a
7702 signature as a one-shot approval is the failure mode.

SINCOR2 state at guard creation (verified 2026-10-30 by grep over src/,
contracts/, onchain/, tests/): there is NO EIP-7702 surface anywhere in
the codebase -- no ``7702`` reference in any .py/.sol/.md file, no
setCode, no authorization tuples/lists, no ``sign_authorization``, no
DELEGATECALL. The only delegation-adjacent design surface is offchain
CDP wallet delegation (the Mandates API), tracked as a design note in
``sincor2.x402_payments`` (``MANDATES_API_WATCH``).

This guard pins two things:

1. The lifecycle constraint exists where a future implementer will see
   it before building on delegated EOAs: the
   ``EIP7702_DELEGATION_LIFECYCLE`` design note in
   ``sincor2.x402_payments`` and ``docs/ops/EIP7702_DELEGATION_LIFECYCLE.md``
   must both state the full rule (short-lived + scoped, revocable by one
   clearing transaction, inventoried, unknown = compromise, re-verify
   the current delegate onchain at use time).

2. The money path stays free of 7702 execution surfaces. The modules
   named in ``MONEY_PATH_MODULES`` are scanned at the AST level
   (identifiers + non-docstring string constants -- executable
   references, not prose: documenting the constraint requires naming
   EIP-7702, and that prose lives in docstrings by design). Any
   executable reference to a 7702 surface fails CI until the lifecycle
   rule above is implemented and this guard is deliberately updated in
   the same change. The ONLY sanctioned 7702 identifier in the money
   path is the design-note class itself in ``x402_payments``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "sincor2"
LIFECYCLE_DOC = REPO_ROOT / "docs" / "ops" / "EIP7702_DELEGATION_LIFECYCLE.md"

#: Money-path modules that must never accept or execute through a
#: 7702-delegated EOA without the lifecycle rule. Named explicitly so
#: the list is reviewable; the existence test below keeps it honest.
MONEY_PATH_MODULES = (
    # x402 / rail payments
    "x402_payments.py",
    "rail_routes.py",
    "rail_subscriptions.py",
    "rail_delivery.py",
    "payment_verifier.py",
    "payment_finality.py",
    "payment_ledger.py",
    "platform_payments.py",
    "agent_billing.py",
    # bounty pool / marketplace money flow
    "a2a_bounty_pool.py",
    "a2a_inbound_market.py",
    "a2a_integration.py",
    # fee + treasury
    "onchain/fee_event_listener.py",
    "onchain/fee_conversion_executor.py",
    "onchain/fee_authorization.py",
    "treasury_inflow.py",
    "treasury_settlement.py",
    "treasury_hold.py",
    "treasury_policy.py",
    # stake / settlement / transaction submission clients
    "onchain/stake_bridge.py",
    "onchain/stake_ledger.py",
    "onchain/buyback_executor.py",
    "onchain/auction_relayer.py",
    "onchain/auction_client.py",
    "onchain/bidder_client.py",
    "sponsored_stake.py",
    "settlement_proofs.py",
)

#: The one sanctioned 7702 identifier in the money path: the design
#: note that carries the lifecycle constraint (x402_payments only).
SANCTIONED_NOTE_IDENTIFIERS = ("eip7702_delegation_lifecycle",)

#: Executable-reference patterns for a 7702 delegation surface, matched
#: against lowercased identifiers and non-docstring string constants.
SURFACE_PATTERNS = (
    r"(?<![0-9a-f])7702(?![0-9a-f])",  # EIP-7702, not a hex substring
    r"set_?code",                      # setCode / set_code tx building
    r"sign_?authorization",            # eth_account/web3 7702 signing API
    r"authorization_?list",            # tx authorizationList payload field
    r"ef0100",                         # 7702 delegation designator prefix
    r"delegatecall",                   # raw DELEGATECALL execution
)


def _executable_blob(source: str) -> str:
    """Identifiers + non-docstring string constants of a module, lowered.

    Docstrings are excluded on purpose: the lifecycle constraint is
    documented in prose that must be allowed to name EIP-7702. Anything
    executable -- a call, an attribute, a payload key, an RPC method
    string -- is included.
    """
    tree = ast.parse(source)
    docstring_value_ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            if ast.get_docstring(node, clean=False) is not None:
                docstring_value_ids.add(id(node.body[0].value))
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_value_ids
        ):
            parts.append(node.value)
    return "\n".join(parts).lower()


def _surface_hits(source: str, *, allow_design_note: bool = False) -> list[str]:
    blob = _executable_blob(source)
    if allow_design_note:
        for ident in SANCTIONED_NOTE_IDENTIFIERS:
            blob = blob.replace(ident, "")
    return [p for p in SURFACE_PATTERNS if re.search(p, blob)]


# ---------------------------------------------------------------------------
# 1. The lifecycle constraint is written down, complete, in both homes.
# ---------------------------------------------------------------------------

#: Fragments (lowercased) the lifecycle rule must state, in both homes.
REQUIRED_RULE_FRAGMENTS = (
    "7702",
    "expir",          # short-lived: explicit expiry
    "implementation", # scoped to a specific implementation address
    "revoc",          # revocable
    "clearing",       # ...by a single clearing transaction
    "inventor",       # active delegations are inventoried
    "compromise",     # unknown active delegation = compromise until cleared
    "re-verif",       # re-verify the current delegate at use time
    "onchain",        # ...onchain, not from the signed tuple
)


def test_lifecycle_design_note_present_and_complete():
    from sincor2.x402_payments import EIP7702_DELEGATION_LIFECYCLE

    doc = (EIP7702_DELEGATION_LIFECYCLE.__doc__ or "").lower()
    assert doc, "EIP7702_DELEGATION_LIFECYCLE design note has no docstring"
    missing = [f for f in REQUIRED_RULE_FRAGMENTS if f not in doc]
    assert not missing, f"lifecycle design note is missing rule fragments: {missing}"


def test_lifecycle_ops_doc_present_and_complete():
    assert LIFECYCLE_DOC.is_file(), f"missing ops doc: {LIFECYCLE_DOC}"
    text = LIFECYCLE_DOC.read_text(encoding="utf-8").lower()
    missing = [f for f in REQUIRED_RULE_FRAGMENTS if f not in text]
    assert not missing, f"lifecycle ops doc is missing rule fragments: {missing}"


# ---------------------------------------------------------------------------
# 2. The money path carries no 7702 execution surface.
# ---------------------------------------------------------------------------


def test_money_path_module_list_is_real():
    missing = [m for m in MONEY_PATH_MODULES if not (SRC / m).is_file()]
    assert not missing, f"money-path module list names files that do not exist: {missing}"


@pytest.mark.parametrize("relpath", MONEY_PATH_MODULES)
def test_no_7702_surface_in_money_path_module(relpath):
    source = (SRC / relpath).read_text(encoding="utf-8")
    hits = _surface_hits(source, allow_design_note=(relpath == "x402_payments.py"))
    assert not hits, (
        f"{relpath} references an EIP-7702 delegation surface {hits}. "
        "A 7702 surface may only enter the money path together with the "
        "lifecycle rule in sincor2.x402_payments.EIP7702_DELEGATION_LIFECYCLE "
        "(short-lived + scoped, single-clearing-tx revocable, inventoried, "
        "delegate re-verified onchain at use time) and a deliberate update "
        "to this guard in the same change."
    )


# ---------------------------------------------------------------------------
# 3. Positive controls: the scanner is not vacuous, in either direction.
# ---------------------------------------------------------------------------


def test_scanner_detects_synthetic_7702_surface():
    synthetic_signing = (
        "def attach(acct, auth):\n"
        "    signed = acct.sign_authorization(auth)\n"
        "    return {'authorizationList': [signed]}\n"
    )
    assert _surface_hits(synthetic_signing), "scanner missed sign_authorization surface"

    synthetic_designator = "DELEGATION_PREFIX = '0xef0100'\n"
    assert _surface_hits(synthetic_designator), "scanner missed 7702 designator surface"

    synthetic_setcode = "def upgrade(acct, impl):\n    return acct.set_code(impl)\n"
    assert _surface_hits(synthetic_setcode), "scanner missed set_code surface"


def test_scanner_allows_prose_and_unrelated_delegation_words():
    prose_only = (
        '"""Design note: EIP-7702 delegation is a standing capability.\n\n'
        "See the lifecycle constraint before implementing anything here.\n"
        '"""\n'
        "def revoke_delegation():\n"
        '    """Revoke an offchain payment-session delegation."""\n'
        "    return 'revoked'\n"
    )
    assert _surface_hits(prose_only) == [], (
        "scanner flagged prose/docstrings -- documenting the constraint "
        "must stay possible; only executable references are surfaces"
    )
