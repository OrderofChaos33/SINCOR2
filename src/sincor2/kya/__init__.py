"""KYA stack: identity, SLA receipts, SADAS feed, Polyclaw scorecard, airdrop quest.

Mount with: from sincor2.kya.blueprint import mount_kya; mount_kya(app)
"""

from sincor2.kya.registry import KYARegistry, get_registry
from sincor2.kya.sla import SLAMonitor, get_sla
from sincor2.kya.sadas import SADASFeed, get_sadas
from sincor2.kya.receipts import ReceiptBook, get_receipts
from sincor2.kya.airdrop_quest import AirdropQuest, get_quest
from sincor2.kya.pricing import PRICE_BOOK

__all__ = [
    "KYARegistry",
    "SLAMonitor",
    "SADASFeed",
    "ReceiptBook",
    "AirdropQuest",
    "get_registry",
    "get_sla",
    "get_sadas",
    "get_receipts",
    "get_quest",
    "PRICE_BOOK",
]
