from __future__ import annotations

"""Settlement coordination for A2A task payments and treasury routing.

AXIOM (AXM) is the PRIMARY and sole settlement token for all new A2A quotes,
billing, and platform fees per CEO directive 2026-08-16.
SINC is retained strictly for legacy residual holders and explicit opt-in.

Platform fee: 5 % of every confirmed payment is routed to the treasury.
The remaining 95 % is recorded for the payee.
"""

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

TREASURY_ADDRESS = os.getenv('TREASURY_ADDRESS', '0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac')
AXIOM_TOKEN = os.getenv('AXIOM_CONTRACT_ADDRESS', '0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822')
SINC_TOKEN = os.getenv('SINC_CONTRACT_ADDRESS', '0x9C8cd8d3961F445D653713dE65C6578bE11668e7')
BASE_CHAIN_ID = int(os.getenv('BASE_CHAIN_ID', '8453'))

# Primary token selection — DEFAULT IS NOW AXIOM per 2026-08-16 CEO directive.
# Override with A2A_PRIMARY_TOKEN=SINC only for explicit legacy residual paths.
_PRIMARY_TOKEN = os.getenv('A2A_PRIMARY_TOKEN', 'AXIOM').upper()
if _PRIMARY_TOKEN not in ('AXIOM', 'SINC', 'AXM'):
    logger.warning("Invalid A2A_PRIMARY_TOKEN=%s; forcing AXIOM", _PRIMARY_TOKEN)
    _PRIMARY_TOKEN = 'AXIOM'
if _PRIMARY_TOKEN == 'AXM':
    _PRIMARY_TOKEN = 'AXIOM'

# Platform fee in basis points (500 = 5 %)
PLATFORM_FEE_BPS = 500
_BPS_DENOM = 10_000

_QUANT = Decimal('0.0001')

_ADDRESS_RE = re.compile(r'^0x[a-fA-F0-9]{40}$')


def _validate_address(addr: str, label: str = 'address') -> str:
    """Strict checksum-free address validation. Raises ValueError on failure."""
    if not addr or not isinstance(addr, str):
        raise ValueError(f"Invalid {label}: empty or non-string")
    cleaned = addr.strip()
    if not _ADDRESS_RE.match(cleaned):
        raise ValueError(f"Invalid {label}: {addr!r} is not a valid 0x + 40 hex address")
    return cleaned.lower()


def _resolve_token_address(symbol: str) -> str:
    """Return the contract address for a given token symbol. Raises on unknown."""
    sym = symbol.upper()
    if sym in ('AXIOM', 'AXM'):
        return AXIOM_TOKEN
    if sym == 'SINC':
        return SINC_TOKEN
    raise ValueError(f"Unsupported settlement token_symbol={symbol!r}. Only AXIOM (primary) or SINC (legacy) allowed.")


def _compute_fee(amount: Decimal) -> Decimal:
    """Return the 5 % platform fee for a given settlement amount."""
    if amount < 0:
        raise ValueError("Settlement amount cannot be negative")
    return (amount * PLATFORM_FEE_BPS / _BPS_DENOM).quantize(_QUANT, rounding=ROUND_DOWN)


@dataclass
class SettlementQuote:
    """Represents a priced task quote awaiting payment confirmation."""

    quote_id: str
    task_reference: str
    payer: str
    payee: str
    token_symbol: str
    token_address: str
    amount: str
    # retained for backward compatibility; equals amount when token is SINC, else '0'
    sinc_amount: str
    chain_id: int
    expires_at: str
    status: str = 'quoted'


@dataclass
class SettlementRecord:
    """Represents a confirmed settlement event."""

    settlement_id: str
    quote_id: str
    task_reference: str
    tx_hash: str
    payer: str
    payee: str
    token_symbol: str
    amount: str
    platform_fee: str       # 5 % of amount routed to treasury
    payee_amount: str       # 95 % of amount for the payee
    treasury_address: str
    status: str
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SettlementCoordinator:
    """Coordinates quotes, payment confirmation, and treasury routing records.

    AXIOM is the default and sole primary settlement token for new work.
    SINC is supported only when explicitly requested for legacy residual holders.
    """

    def __init__(self) -> None:
        self.quotes: Dict[str, SettlementQuote] = {}
        self.settlements: Dict[str, SettlementRecord] = {}
        self.treasury_journal: List[Dict[str, str]] = []
        # Validate canonical addresses at init
        try:
            _validate_address(TREASURY_ADDRESS, 'TREASURY_ADDRESS')
            _validate_address(AXIOM_TOKEN, 'AXIOM_TOKEN')
            _validate_address(SINC_TOKEN, 'SINC_TOKEN')
        except ValueError as e:
            logger.error("Canonical address validation failed at SettlementCoordinator init: %s", e)
            raise

    def create_quote(
        self,
        task_reference: str,
        payer: str,
        payee: str,
        amount: Decimal,
        token_symbol: str = _PRIMARY_TOKEN,
        expires_in_minutes: int = 15,
    ) -> SettlementQuote:
        """Create an AXIOM (default) or explicit SINC quote for an A2A task.

        Parameters
        ----------
        task_reference:    Unique platform task identifier.
        payer:             Wallet address of the paying party.
        payee:             Wallet address of the fulfilling agent.
        amount:            Settlement amount in the chosen token.
        token_symbol:      'AXIOM' (default / primary) or 'SINC' for legacy only.
        expires_in_minutes: Quote validity window.
        """
        if not task_reference or not isinstance(task_reference, str):
            raise ValueError("task_reference is required and must be a non-empty string")
        try:
            payer = _validate_address(payer, 'payer')
            payee = _validate_address(payee, 'payee')
        except ValueError as e:
            raise ValueError(f"Address validation failed: {e}") from e

        if not isinstance(amount, Decimal):
            try:
                amount = Decimal(str(amount))
            except (InvalidOperation, TypeError) as e:
                raise ValueError(f"amount must be a valid Decimal-compatible value: {e}") from e
        if amount <= 0:
            raise ValueError("amount must be positive")

        sym = token_symbol.upper()
        if sym == 'AXM':
            sym = 'AXIOM'
        if sym not in ('AXIOM', 'SINC'):
            raise ValueError(f"token_symbol must be AXIOM (primary) or SINC (legacy); got {token_symbol!r}")

        token_address = _resolve_token_address(sym)
        # Keep sinc_amount field for schema compatibility; only non-zero when SINC is used
        sinc_amount = str(amount.quantize(_QUANT)) if sym == 'SINC' else '0.0000'
        quote = SettlementQuote(
            quote_id=f"quote-{uuid4().hex[:12]}",
            task_reference=task_reference,
            payer=payer,
            payee=payee,
            token_symbol=sym,
            token_address=token_address,
            amount=str(amount.quantize(_QUANT)),
            sinc_amount=sinc_amount,
            chain_id=BASE_CHAIN_ID,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)).isoformat(),
        )
        self.quotes[quote.quote_id] = quote
        logger.info(
            "settlement_quote created quote_id=%s token=%s amount=%s payer=%s (primary=%s)",
            quote.quote_id, sym, amount, payer, _PRIMARY_TOKEN,
        )
        return quote

    def confirm_payment(
        self,
        quote_id: str,
        tx_hash: str,
        confirmed_amount: Decimal,
    ) -> SettlementRecord:
        """Mark a quote as paid, compute the 5 % platform fee, and record the settlement.

        The platform fee is routed to the treasury; the remaining 95 % is
        recorded as the payee's amount.
        """
        if quote_id not in self.quotes:
            raise KeyError(f"Quote '{quote_id}' not found")
        if not tx_hash or not isinstance(tx_hash, str) or len(tx_hash) < 10:
            raise ValueError("tx_hash must be a non-empty valid transaction hash string")
        quote = self.quotes[quote_id]
        if quote.status != 'quoted':
            raise ValueError(f"Quote {quote_id} is already in status={quote.status}; cannot confirm again")

        if not isinstance(confirmed_amount, Decimal):
            try:
                confirmed_amount = Decimal(str(confirmed_amount))
            except (InvalidOperation, TypeError) as e:
                raise ValueError(f"confirmed_amount must be Decimal-compatible: {e}") from e
        if confirmed_amount <= 0:
            raise ValueError("confirmed_amount must be positive")

        quote.status = 'paid'

        fee = _compute_fee(confirmed_amount)
        payee_amount = confirmed_amount - fee

        settlement = SettlementRecord(
            settlement_id=f"settle-{uuid4().hex[:12]}",
            quote_id=quote_id,
            task_reference=quote.task_reference,
            tx_hash=tx_hash,
            payer=quote.payer,
            payee=quote.payee,
            token_symbol=quote.token_symbol,
            amount=str(confirmed_amount.quantize(_QUANT)),
            platform_fee=str(fee),
            payee_amount=str(payee_amount.quantize(_QUANT, rounding=ROUND_DOWN)),
            treasury_address=TREASURY_ADDRESS,
            status='confirmed',
        )
        self.record_settlement(settlement)
        logger.info(
            "settlement confirmed settlement_id=%s token=%s amount=%s fee=%s payee=%s",
            settlement.settlement_id, quote.token_symbol, confirmed_amount, fee, quote.payee,
        )
        return settlement

    def sinc_credit_deduction(
        self,
        wallet: str,
        amount: Decimal,
        task_id: str,
    ) -> Dict[str, str]:
        """Record an off-chain credit deduction (legacy SINC path only).

        Prefer on-chain AXIOM settlement for all new work. This method is
        retained solely for residual SINC credit holders.
        """
        try:
            wallet = _validate_address(wallet, 'wallet')
        except ValueError as e:
            raise ValueError(f"Invalid wallet for credit deduction: {e}") from e
        if not isinstance(amount, Decimal) or amount <= 0:
            raise ValueError("amount must be a positive Decimal")
        fee = _compute_fee(amount)
        event = {
            'type': 'credit_deduction',
            'wallet': wallet,
            'task_id': task_id,
            'token_symbol': 'SINC',
            'amount': str(amount.quantize(_QUANT)),
            'platform_fee': str(fee),
            'treasury_address': TREASURY_ADDRESS,
            'recorded_at': datetime.now(timezone.utc).isoformat(),
            'note': 'legacy_sinc_path_only',
        }
        self.treasury_journal.append(event)
        logger.info(
            "sinc_credit_deduction (legacy) wallet=%s amount=%s fee=%s task=%s",
            wallet, amount, fee, task_id,
        )
        return event

    def route_to_treasury(self, amount: Decimal, token_symbol: str) -> Dict[str, str]:
        """Record a treasury routing event for fee or settlement proceeds."""
        if amount < 0:
            raise ValueError("Cannot route negative amount to treasury")
        sym = token_symbol.upper()
        if sym not in ('AXIOM', 'SINC', 'USDC', 'AXM'):
            raise ValueError(f"Unsupported token for treasury routing: {token_symbol}")
        if sym == 'AXM':
            sym = 'AXIOM'
        event = {
            'treasury_address': TREASURY_ADDRESS,
            'token_symbol': sym,
            'amount': str(amount.quantize(_QUANT)),
            'routed_at': datetime.now(timezone.utc).isoformat(),
        }
        self.treasury_journal.append(event)
        return event

    def record_settlement(self, settlement: SettlementRecord) -> SettlementRecord:
        """Persist a settlement record and append treasury routing metadata."""
        self.settlements[settlement.settlement_id] = settlement
        self.route_to_treasury(Decimal(settlement.platform_fee), settlement.token_symbol)
        return settlement
