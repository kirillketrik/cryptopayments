"""The Invoice model and its status state machine.

``Invoice`` is a mutable dataclass rather than a frozen one: its status and
received-amount fields are updated in place as payments arrive, and
``transition_to`` is the single choke point that enforces which status
changes are legal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from cryptopayments.exceptions import InvalidStatusTransitionError


class InvoiceStatus(str, Enum):
    """Lifecycle states for an invoice."""

    PENDING = "pending"
    PARTIALLY_PAID = "partially_paid"
    CONFIRMED = "confirmed"
    OVERPAID = "overpaid"
    EXPIRED = "expired"


# Allowed forward transitions. Two deliberate additions beyond the literal
# spec are documented in the README ("State machine" section):
#   * PENDING -> CONFIRMED directly (a single transaction can fully pay and
#     confirm an invoice without ever passing through PARTIALLY_PAID).
#   * PARTIALLY_PAID -> OVERPAID directly (a top-up transaction can push a
#     partially paid invoice straight past the requested amount).
_ALLOWED_TRANSITIONS: dict[InvoiceStatus, frozenset[InvoiceStatus]] = {
    InvoiceStatus.PENDING: frozenset(
        {InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.CONFIRMED, InvoiceStatus.EXPIRED}
    ),
    InvoiceStatus.PARTIALLY_PAID: frozenset(
        {InvoiceStatus.CONFIRMED, InvoiceStatus.OVERPAID, InvoiceStatus.EXPIRED}
    ),
    InvoiceStatus.CONFIRMED: frozenset({InvoiceStatus.OVERPAID}),
    InvoiceStatus.OVERPAID: frozenset(),
    InvoiceStatus.EXPIRED: frozenset(),
}


@dataclass
class Invoice:
    """A request for payment in a specific currency/network, tracked to completion."""

    id: str
    user_reference: str
    currency: str
    network: str
    requested_amount: Decimal
    payment_address: str | None = None
    status: InvoiceStatus = InvoiceStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    confirmations_required: int = 1
    confirmations_received: int = 0
    amount_received: Decimal = Decimal("0")

    def transition_to(self, new_status: InvoiceStatus) -> None:
        """Move this invoice to ``new_status``, or raise if the move is illegal."""
        allowed = _ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise InvalidStatusTransitionError(self.status, new_status)
        self.status = new_status

    def can_transition_to(self, new_status: InvoiceStatus) -> bool:
        """Return whether ``new_status`` is a legal move from the current status."""
        return new_status in _ALLOWED_TRANSITIONS.get(self.status, frozenset())
