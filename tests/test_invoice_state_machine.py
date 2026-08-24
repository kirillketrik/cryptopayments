from __future__ import annotations

import pytest

from cryptopayments.exceptions import InvalidStatusTransitionError
from cryptopayments.models.invoice import InvoiceStatus


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (InvoiceStatus.PENDING, InvoiceStatus.PARTIALLY_PAID),
        (InvoiceStatus.PENDING, InvoiceStatus.CONFIRMED),
        (InvoiceStatus.PENDING, InvoiceStatus.EXPIRED),
        (InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.CONFIRMED),
        (InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERPAID),
        (InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.EXPIRED),
        (InvoiceStatus.CONFIRMED, InvoiceStatus.OVERPAID),
    ],
)
def test_valid_transitions(make_invoice, start, target):
    invoice = make_invoice(status=start)
    invoice.transition_to(target)
    assert invoice.status == target


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (InvoiceStatus.PENDING, InvoiceStatus.OVERPAID),
        (InvoiceStatus.CONFIRMED, InvoiceStatus.PENDING),
        (InvoiceStatus.CONFIRMED, InvoiceStatus.PARTIALLY_PAID),
        (InvoiceStatus.CONFIRMED, InvoiceStatus.EXPIRED),
        (InvoiceStatus.EXPIRED, InvoiceStatus.PENDING),
        (InvoiceStatus.EXPIRED, InvoiceStatus.CONFIRMED),
        (InvoiceStatus.OVERPAID, InvoiceStatus.CONFIRMED),
        (InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.PENDING),
        (InvoiceStatus.PENDING, InvoiceStatus.PENDING),
    ],
)
def test_invalid_transitions_raise(make_invoice, start, target):
    invoice = make_invoice(status=start)
    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        invoice.transition_to(target)
    assert exc_info.value.current_status == start
    assert exc_info.value.new_status == target
    assert invoice.status == start  # unchanged on failure


def test_can_transition_to_does_not_raise(make_invoice):
    invoice = make_invoice(status=InvoiceStatus.PENDING)
    assert invoice.can_transition_to(InvoiceStatus.CONFIRMED) is True
    assert invoice.can_transition_to(InvoiceStatus.OVERPAID) is False


def test_terminal_statuses_have_no_outgoing_transitions(make_invoice):
    for terminal in (InvoiceStatus.EXPIRED, InvoiceStatus.OVERPAID):
        invoice = make_invoice(status=terminal)
        for target in InvoiceStatus:
            assert invoice.can_transition_to(target) is False
