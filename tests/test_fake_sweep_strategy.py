from __future__ import annotations

from decimal import Decimal

import pytest

from cryptopayments.fakes.sweep_strategy import FakeSweepStrategy


def test_sweep_records_result_and_marks_swept(make_invoice):
    strategy = FakeSweepStrategy()
    invoice = make_invoice(id="inv-1", payment_address="fake-addr-bitcoin-1", network="bitcoin")

    result = strategy.sweep(invoice, amount=Decimal("1.5"))

    assert result.swept is True
    assert result.invoice_id == "inv-1"
    assert result.from_address == "fake-addr-bitcoin-1"
    assert result.network == "bitcoin"
    assert result.amount == Decimal("1.5")
    assert result.reference is not None


def test_sweeps_accumulate_in_order(make_invoice):
    strategy = FakeSweepStrategy()
    invoice_a = make_invoice(id="inv-a")
    invoice_b = make_invoice(id="inv-b")

    strategy.sweep(invoice_a, amount=Decimal("1"))
    strategy.sweep(invoice_b, amount=Decimal("2"))

    assert [s.invoice_id for s in strategy.sweeps] == ["inv-a", "inv-b"]


def test_sweep_without_payment_address_raises(make_invoice):
    strategy = FakeSweepStrategy()
    invoice = make_invoice(id="inv-1", payment_address=None)

    with pytest.raises(ValueError, match="no payment address"):
        strategy.sweep(invoice, amount=Decimal("1"))
