"""cryptopayments: a pluggable library for accepting cryptocurrency payments.

Phase 1 ships the full contract layer plus in-memory fake implementations of
every contract, wired together through :class:`~cryptopayments.service.PaymentService`.
See the README for how a host application swaps in real implementations.
"""

from cryptopayments.exceptions import (
    InvalidStatusTransitionError,
    InvoiceAlreadyExistsError,
    InvoiceNotFoundError,
    PaymentLibraryError,
    UnknownTransactionError,
    UnsupportedNetworkError,
)
from cryptopayments.models import (
    AddressAllocation,
    AddressStrategy,
    IncomingTransaction,
    Invoice,
    InvoiceStatus,
    PaymentEvent,
    PaymentEventType,
    SweepResult,
)
from cryptopayments.service import PaymentService

__all__ = [
    "AddressAllocation",
    "AddressStrategy",
    "IncomingTransaction",
    "InvalidStatusTransitionError",
    "Invoice",
    "InvoiceAlreadyExistsError",
    "InvoiceNotFoundError",
    "InvoiceStatus",
    "PaymentEvent",
    "PaymentEventType",
    "PaymentLibraryError",
    "PaymentService",
    "SweepResult",
    "UnknownTransactionError",
    "UnsupportedNetworkError",
]

from cryptopayments.exact import (
    AmountSpaceExhausted, Asset, ExactInvoice, ExactPaymentService, Transfer,
)

__all__ += ['AmountSpaceExhausted', 'Asset', 'ExactInvoice', 'ExactPaymentService', 'Transfer']

from cryptopayments.webhooks import WebhookWorker, verify_signature

__all__ += ['WebhookWorker', 'verify_signature']
