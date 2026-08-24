"""Value objects for the notification contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from cryptopayments.models.invoice import InvoiceStatus


class PaymentEventType(str, Enum):
    """The kind of event a :class:`~cryptopayments.contracts.notifier.Notifier` is told about."""

    STATUS_CHANGED = "status_changed"


@dataclass(frozen=True)
class PaymentEvent:
    """A single notification about an invoice status change."""

    invoice_id: str
    event_type: PaymentEventType
    previous_status: InvoiceStatus | None
    new_status: InvoiceStatus
    occurred_at: datetime
