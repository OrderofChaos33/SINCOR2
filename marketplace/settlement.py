from __future__ import annotations

"""Settlement coordination for A2A task payments and treasury routing.

SINC is the primary settlement token.  AXIOM is retained for backward
compatibility and can be selected by passing ``token_symbol='AXIOM'``.

Platform fee: 5 % of every confirmed payment is routed to the treasury.
The remaining 95 % is recorded for the payee.
"""

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

TREASURY_ADDRESS = os.getenv('TREASURY_ADDRESS', '0xAf9B539D8043C634b7E611818518BA7E850F289e')
AXIOM_TOKEN = os.getenv('AXIOM_CONTRACT_ADDRESS', '0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822')
SINC_TOKEN = os.getenv('SINC_CONTRACT_ADDRESS', '0x9C8cd8d3961F445D653713dE65C6578bE11668e7')
BASE_CHAIN_ID = int(os.getenv('BASE_CHAIN_ID', '8453'))

# Primary token selection — override with A2A_PRIMARY_TOKEN=AXIOM for legacy mode
_PRIMARY_TOKEN = os.getenv('A2A_PRIMARY_TOKEN', 'SINC').upper()

# Platform fee in basis points (500 = 5 %)
PLATFORM_FEE_BPS = 500
_BPS_DENOM = 10_000

_QUANT = Decimal('0.0001')


def _resolve_token_address(symbol: str) -> str:
    """Return the contract address for a given token symbol."""
    return AXIOM_TOKEN if symbol.upper() == 'AXIOM' else SINC_TOKEN


def _compute_fee(amount: Decimal) -> Decimal:
    """Return the 5 % platform fee for a given settlement amount."""
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
    sinc_amount: str        # always present; equals amount when token_symbol=SINC
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

    SINC is the default settlement token.  Legacy AXIOM quotes are still
    supported when ``token_symbol='AXIOM'`` is passed explicitly, or when
    the ``A2A_PRIMARY_TOKEN`` environment variable is set to ``AXIOM``.
    """

    def __init__(self) -> None:
        self.quotes: Dict[str, SettlementQuote] = {}
        self.settlements: Dict[str, SettlementRecord] = {}
        self.treasury_journal: List[Dict[str, str]] = []

    def create_quote(
        self,
        task_reference: str,
        payer: str,
        payee: str,
        amount: Decimal,
        token_symbol: str = _PRIMARY_TOKEN,
        expires_in_minutes: int = 15,
    ) -> SettlementQuote:
        """Create a SINC (or legacy AXIOM) quote for an A2A task execution.

        Parameters
        ----------
        task_reference:    Unique platform task identifier.
        payer:             Wallet address of the paying party.
        payee:             Wallet address of the fulfilling agent.
        amount:            Settlement amount in the chosen token.
        token_symbol:      'SINC' (default) or 'AXIOM' for legacy mode.
        expires_in_minutes: Quote validity window.
        """
        sym = token_symbol.upper()
        token_address = _resolve_token_address(sym)
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
            "settlement_quote created quote_id=%s token=%s amount=%s payer=%s",
            quote.quote_id, sym, amount, payer,
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
        quote = self.quotes[quote_id]
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
        """Record an off-chain credit deduction (alternative to on-chain tx for small tasks).

        The caller is responsible for verifying and debiting the on-chain
        credit balance via SINCPlatformAccess before calling this method.
        """
        fee = _compute_fee(amount)
        event = {
            'type': 'credit_deduction',
            'wallet': wallet.lower(),
            'task_id': task_id,
            'token_symbol': 'SINC',
            'amount': str(amount.quantize(_QUANT)),
            'platform_fee': str(fee),
            'treasury_address': TREASURY_ADDRESS,
            'recorded_at': datetime.now(timezone.utc).isoformat(),
        }
        self.treasury_journal.append(event)
        logger.info(
            "sinc_credit_deduction wallet=%s amount=%s fee=%s task=%s",
            wallet, amount, fee, task_id,
        )
        return event

    def route_to_treasury(self, amount: Decimal, token_symbol: str) -> Dict[str, str]:
        """Record a treasury routing event for fee or settlement proceeds."""
        event = {
            'treasury_address': TREASURY_ADDRESS,
            'token_symbol': token_symbol.upper(),
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
