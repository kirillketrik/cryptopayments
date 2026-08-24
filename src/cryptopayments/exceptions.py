"""Exception hierarchy for the cryptopayments library.

All exceptions raised by this library inherit from :class:`PaymentLibraryError`,
so host applications can catch it as a single, broad backstop while still being
able to handle specific failure modes individually.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptopayments.models.invoice import InvoiceStatus


class PaymentLibraryError(Exception):
    """Base class for every exception raised by this library."""


class InvoiceNotFoundError(PaymentLibraryError):
    """Raised when an invoice id has no matching record in the repository."""

    def __init__(self, invoice_id: str) -> None:
        super().__init__(f"Invoice not found: {invoice_id}")
        self.invoice_id = invoice_id


class InvoiceAlreadyExistsError(PaymentLibraryError):
    """Raised when creating an invoice whose id already exists in the repository."""

    def __init__(self, invoice_id: str) -> None:
        super().__init__(f"Invoice already exists: {invoice_id}")
        self.invoice_id = invoice_id


class UnsupportedNetworkError(PaymentLibraryError):
    """Raised when an implementation is asked to act on a network it does not support."""

    def __init__(self, network: str) -> None:
        super().__init__(f"Unsupported network: {network}")
        self.network = network


class InvalidStatusTransitionError(PaymentLibraryError):
    """Raised when an invoice status transition violates the state machine rules."""

    def __init__(self, current_status: "InvoiceStatus", new_status: "InvoiceStatus") -> None:
        super().__init__(f"Cannot transition invoice from {current_status.value!r} to {new_status.value!r}")
        self.current_status = current_status
        self.new_status = new_status


class UnknownTransactionError(PaymentLibraryError):
    """Raised when a chain client is asked about a transaction it has no record of."""

    def __init__(self, tx_id: str) -> None:
        super().__init__(f"Unknown transaction: {tx_id}")
        self.tx_id = tx_id
