from __future__ import annotations

from datetime import datetime, timezone

from cryptopayments.contracts.notifier import Notifier
from cryptopayments.fakes.notifier import InMemoryNotifier
from cryptopayments.models.events import PaymentEvent, PaymentEventType
from cryptopayments.models.invoice import InvoiceStatus


def test_notify_appends_event():
    notifier = InMemoryNotifier()
    event = PaymentEvent(
        invoice_id="inv-1",
        event_type=PaymentEventType.STATUS_CHANGED,
        previous_status=InvoiceStatus.PENDING,
        new_status=InvoiceStatus.CONFIRMED,
        occurred_at=datetime.now(timezone.utc),
    )

    notifier.notify(event)

    assert notifier.events == [event]


def test_in_memory_notifier_satisfies_notifier_protocol():
    assert isinstance(InMemoryNotifier(), Notifier)
