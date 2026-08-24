"""In-memory fake for :class:`~cryptopayments.contracts.repository.PaymentRepository`."""

from __future__ import annotations

from cryptopayments.contracts.repository import PaymentRepository
from cryptopayments.exceptions import InvoiceAlreadyExistsError, InvoiceNotFoundError
from cryptopayments.models.invoice import Invoice, InvoiceStatus


class InMemoryPaymentRepository(PaymentRepository):
    """A dict-backed :class:`PaymentRepository` for tests and local development.

    Invoices are stored by reference (not deep-copied), so mutating an
    ``Invoice`` obtained from :meth:`get` and then calling :meth:`update`
    with it is safe and is the pattern :class:`~cryptopayments.service.PaymentService`
    uses. A real, out-of-process backend would not have this property, so
    callers should not rely on it.
    """

    def __init__(self) -> None:
        self._invoices: dict[str, Invoice] = {}

    def create(self, invoice: Invoice) -> None:
        if invoice.id in self._invoices:
            raise InvoiceAlreadyExistsError(invoice.id)
        self._invoices[invoice.id] = invoice

    def get(self, invoice_id: str) -> Invoice:
        try:
            return self._invoices[invoice_id]
        except KeyError:
            raise InvoiceNotFoundError(invoice_id) from None

    def update(self, invoice: Invoice) -> None:
        if invoice.id not in self._invoices:
            raise InvoiceNotFoundError(invoice.id)
        self._invoices[invoice.id] = invoice

    def find_by_address(self, address: str) -> Invoice | None:
        for invoice in self._invoices.values():
            if invoice.payment_address == address:
                return invoice
        return None

    def list(
        self,
        *,
        status: InvoiceStatus | None = None,
        user_reference: str | None = None,
    ) -> list[Invoice]:
        results = list(self._invoices.values())
        if status is not None:
            results = [invoice for invoice in results if invoice.status == status]
        if user_reference is not None:
            results = [invoice for invoice in results if invoice.user_reference == user_reference]
        return results
