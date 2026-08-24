"""Value objects for the blockchain-reading contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IncomingTransaction:
    """A single inbound transaction observed on-chain for some address."""

    tx_id: str
    address: str
    network: str
    amount: Decimal
    confirmations: int
