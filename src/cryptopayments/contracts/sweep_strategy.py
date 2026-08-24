"""Fund-sweeping (a.k.a. gas-funding/forwarding) contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from cryptopayments.models.invoice import Invoice
from cryptopayments.models.sweep import SweepResult


class SweepStrategy(ABC):
    """Moves confirmed funds from an invoice's deposit address to the host's main wallet.

    Real implementations vary widely here (direct transfer, gas-funded
    forwarder contract, batched sweeps, ...); Phase 1 only needs the shape of
    "given a confirmed invoice, move its funds and report what happened".
    """

    @abstractmethod
    def sweep(self, invoice: Invoice, *, amount: Decimal) -> SweepResult:
        """Sweep ``amount`` from ``invoice.payment_address`` to the main wallet."""
