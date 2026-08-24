"""Storage contract for invoices."""

from __future__ import annotations

from abc import ABC, abstractmethod

from cryptopayments.models.invoice import Invoice, InvoiceStatus


class PaymentRepository(ABC):
    """Persists and queries invoices.

    Implementations are free to back this with anything (a dict, SQL, a
    document store, ...). Phase 1 ships an in-memory fake; a real
    implementation would typically wrap a host application's existing
    database/ORM.
    """

    @abstractmethod
    def create(self, invoice: Invoice) -> None:
        """Persist a brand-new invoice.

        Raises:
            InvoiceAlreadyExistsError: if an invoice with this id already exists.
        """

    @abstractmethod
    def get(self, invoice_id: str) -> Invoice:
        """Fetch an invoice by id.

        Raises:
            InvoiceNotFoundError: if no invoice with this id exists.
        """

    @abstractmethod
    def update(self, invoice: Invoice) -> None:
        """Persist changes to an invoice that already exists.

        Raises:
            InvoiceNotFoundError: if no invoice with this id exists.
        """

    @abstractmethod
    def find_by_address(self, address: str) -> Invoice | None:
        """Return the invoice assigned to ``address``, or ``None`` if none matches."""

    @abstractmethod
    def list(
        self,
        *,
        status: InvoiceStatus | None = None,
        user_reference: str | None = None,
    ) -> list[Invoice]:
        """Return invoices matching the given filters (all filters are optional and AND-ed)."""
