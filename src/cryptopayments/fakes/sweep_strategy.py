"""In-memory fake for :class:`~cryptopayments.contracts.sweep_strategy.SweepStrategy`."""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import uuid4

from cryptopayments.contracts.sweep_strategy import SweepStrategy
from cryptopayments.models.invoice import Invoice
from cryptopayments.models.sweep import SweepResult

logger = logging.getLogger(__name__)


class FakeSweepStrategy(SweepStrategy):
    """Marks funds as "swept" in-memory and logs the simulated action; no real transfer happens."""

    def __init__(self) -> None:
        self._sweeps: list[SweepResult] = []

    def sweep(self, invoice: Invoice, *, amount: Decimal) -> SweepResult:
        if invoice.payment_address is None:
            raise ValueError(f"Invoice {invoice.id} has no payment address to sweep from")

        result = SweepResult(
            invoice_id=invoice.id,
            from_address=invoice.payment_address,
            network=invoice.network,
            amount=amount,
            swept=True,
            reference=f"fake-sweep-{uuid4().hex[:12]}",
        )
        self._sweeps.append(result)
        logger.info(
            "Simulated sweep of %s %s from %s (invoice=%s, reference=%s)",
            amount,
            invoice.currency,
            invoice.payment_address,
            invoice.id,
            result.reference,
        )
        return result

    @property
    def sweeps(self) -> list[SweepResult]:
        """Test/introspection helper: every sweep performed so far, oldest first."""
        return list(self._sweeps)
