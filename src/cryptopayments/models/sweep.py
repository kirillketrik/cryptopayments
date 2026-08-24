"""Value objects for the fund-sweeping contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SweepResult:
    """The outcome of attempting to sweep confirmed funds to the main wallet."""

    invoice_id: str
    from_address: str
    network: str
    amount: Decimal
    swept: bool
    reference: str | None = None
