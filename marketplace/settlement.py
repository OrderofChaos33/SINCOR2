from __future__ import annotations

"""Settlement coordination for A2A task payments and treasury routing."""

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import uuid4

TREASURY_ADDRESS = os.getenv('TREASURY_ADDRESS', '0xAf9B539D8043C634b7E611818518BA7E850F289e')
AXIOM_TOKEN = os.getenv('AXIOM_CONTRACT_ADDRESS', '0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822')
SINC_TOKEN = os.getenv('SINC_CONTRACT_ADDRESS', '0x9C8cd8d3961F445D653713dE65C6578bE11668e7')
BASE_CHAIN_ID = int(os.getenv('BASE_CHAIN_ID', '8453'))


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
    treasury_address: str
    status: str
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SettlementCoordinator:
    """Coordinates quotes, payment confirmation, and treasury routing records."""

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
        token_symbol: str = 'AXIOM',
        expires_in_minutes: int = 15,
    ) -> SettlementQuote:
        """Create a quote for an A2A task execution."""
        token_address = AXIOM_TOKEN if token_symbol.upper() == 'AXIOM' else SINC_TOKEN
        quote = SettlementQuote(
            quote_id=f"quote-{uuid4().hex[:12]}",
            task_reference=task_reference,
            payer=payer,
            payee=payee,
            token_symbol=token_symbol.upper(),
            token_address=token_address,
            amount=str(amount.quantize(Decimal('0.0001'))),
            chain_id=BASE_CHAIN_ID,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)).isoformat(),
        )
        self.quotes[quote.quote_id] = quote
        return quote

    def confirm_payment(self, quote_id: str, tx_hash: str, confirmed_amount: Decimal) -> SettlementRecord:
        """Mark a quote as paid after payment confirmation."""
        quote = self.quotes[quote_id]
        quote.status = 'paid'
        settlement = SettlementRecord(
            settlement_id=f"settle-{uuid4().hex[:12]}",
            quote_id=quote_id,
            task_reference=quote.task_reference,
            tx_hash=tx_hash,
            payer=quote.payer,
            payee=quote.payee,
            token_symbol=quote.token_symbol,
            amount=str(confirmed_amount.quantize(Decimal('0.0001'))),
            treasury_address=TREASURY_ADDRESS,
            status='confirmed',
        )
        self.record_settlement(settlement)
        return settlement

    def route_to_treasury(self, amount: Decimal, token_symbol: str) -> Dict[str, str]:
        """Record a treasury routing event for fee or settlement proceeds."""
        event = {
            'treasury_address': TREASURY_ADDRESS,
            'token_symbol': token_symbol.upper(),
            'amount': str(amount.quantize(Decimal('0.0001'))),
            'routed_at': datetime.now(timezone.utc).isoformat(),
        }
        self.treasury_journal.append(event)
        return event

    def record_settlement(self, settlement: SettlementRecord) -> SettlementRecord:
        """Persist a settlement record and append treasury routing metadata."""
        self.settlements[settlement.settlement_id] = settlement
        self.route_to_treasury(Decimal(settlement.amount), settlement.token_symbol)
        return settlement
