from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptopayments.exceptions import InvoiceNotFoundError
from cryptopayments.fakes.address_provider import FakeAddressProvider
from cryptopayments.fakes.chain_client import FakeChainClient
from cryptopayments.fakes.notifier import InMemoryNotifier
from cryptopayments.fakes.repository import InMemoryPaymentRepository
from cryptopayments.fakes.sweep_strategy import FakeSweepStrategy
from cryptopayments.models.events import PaymentEventType
from cryptopayments.models.invoice import InvoiceStatus
from cryptopayments.service import PaymentService


@pytest.fixture
def repository() -> InMemoryPaymentRepository:
    return InMemoryPaymentRepository()


@pytest.fixture
def address_provider() -> FakeAddressProvider:
    return FakeAddressProvider()


@pytest.fixture
def chain_client() -> FakeChainClient:
    return FakeChainClient()


@pytest.fixture
def sweep_strategy() -> FakeSweepStrategy:
    return FakeSweepStrategy()


@pytest.fixture
def notifier() -> InMemoryNotifier:
    return InMemoryNotifier()


@pytest.fixture
def service(repository, address_provider, chain_client, sweep_strategy, notifier) -> PaymentService:
    return PaymentService(
        repository=repository,
        address_provider=address_provider,
        chain_client=chain_client,
        sweep_strategy=sweep_strategy,
        notifier=notifier,
    )


def test_create_invoice_allocates_address_and_persists(service, repository):
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
    )

    assert invoice.payment_address is not None
    assert invoice.status == InvoiceStatus.PENDING
    assert repository.get(invoice.id) is invoice


def test_check_payment_unknown_invoice_raises(service):
    with pytest.raises(InvoiceNotFoundError):
        service.check_payment("does-not-exist")


def test_check_payment_no_transactions_stays_pending(service, notifier, sweep_strategy):
    invoice = service.create_invoice(
        user_reference="user-1", currency="BTC", network="bitcoin", requested_amount=Decimal("1.0")
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.PENDING
    assert notifier.events == []
    assert sweep_strategy.sweeps == []


def test_check_payment_partial_amount_transitions_to_partially_paid(service, chain_client, notifier):
    invoice = service.create_invoice(
        user_reference="user-1", currency="BTC", network="bitcoin", requested_amount=Decimal("1.0")
    )
    chain_client.inject_transaction(
        tx_id="tx-1",
        address=invoice.payment_address,
        network="bitcoin",
        amount=Decimal("0.4"),
        confirmations=1,
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.PARTIALLY_PAID
    assert result.amount_received == Decimal("0.4")
    assert [e.new_status for e in notifier.events] == [InvoiceStatus.PARTIALLY_PAID]


def test_check_payment_full_amount_but_unconfirmed_is_partially_paid(service, chain_client):
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
        confirmations_required=3,
    )
    chain_client.inject_transaction(
        tx_id="tx-1", address=invoice.payment_address, network="bitcoin", amount=Decimal("1.0"), confirmations=1
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.PARTIALLY_PAID


def test_end_to_end_full_payment_confirms_sweeps_and_notifies(service, chain_client, sweep_strategy, notifier):
    """create_invoice -> inject tx -> check_payment -> confirmed + swept + notified."""
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
        confirmations_required=2,
    )
    assert invoice.status == InvoiceStatus.PENDING

    chain_client.inject_transaction(
        tx_id="tx-1",
        address=invoice.payment_address,
        network="bitcoin",
        amount=Decimal("1.0"),
        confirmations=2,
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.CONFIRMED
    assert result.amount_received == Decimal("1.0")
    assert result.confirmations_received == 2

    assert len(sweep_strategy.sweeps) == 1
    assert sweep_strategy.sweeps[0].invoice_id == invoice.id
    assert sweep_strategy.sweeps[0].amount == Decimal("1.0")

    assert len(notifier.events) == 1
    event = notifier.events[0]
    assert event.event_type == PaymentEventType.STATUS_CHANGED
    assert event.previous_status == InvoiceStatus.PENDING
    assert event.new_status == InvoiceStatus.CONFIRMED


def test_check_payment_progressive_confirmations_sweeps_only_once(service, chain_client, sweep_strategy, notifier):
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
        confirmations_required=3,
    )
    chain_client.inject_transaction(
        tx_id="tx-1", address=invoice.payment_address, network="bitcoin", amount=Decimal("1.0"), confirmations=0
    )

    assert service.check_payment(invoice.id).status == InvoiceStatus.PARTIALLY_PAID

    chain_client.set_confirmations("tx-1", "bitcoin", 3)
    assert service.check_payment(invoice.id).status == InvoiceStatus.CONFIRMED

    # No new transactions/confirmations: re-checking must be a no-op.
    result = service.check_payment(invoice.id)
    assert result.status == InvoiceStatus.CONFIRMED

    assert len(sweep_strategy.sweeps) == 1
    assert [e.new_status for e in notifier.events] == [InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.CONFIRMED]


def test_check_payment_overpaid_after_confirmed_sweeps_again(service, chain_client, sweep_strategy, notifier):
    invoice = service.create_invoice(
        user_reference="user-1", currency="BTC", network="bitcoin", requested_amount=Decimal("1.0")
    )
    chain_client.inject_transaction(
        tx_id="tx-1", address=invoice.payment_address, network="bitcoin", amount=Decimal("1.0"), confirmations=1
    )
    assert service.check_payment(invoice.id).status == InvoiceStatus.CONFIRMED

    chain_client.inject_transaction(
        tx_id="tx-2", address=invoice.payment_address, network="bitcoin", amount=Decimal("0.5"), confirmations=1
    )
    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.OVERPAID
    assert result.amount_received == Decimal("1.5")
    # Each sweep moves only the funds newly seen since the previous check, not the
    # cumulative total, so the second sweep should be the 0.5 top-up, not 1.5 again.
    assert [s.amount for s in sweep_strategy.sweeps] == [Decimal("1.0"), Decimal("0.5")]
    assert [e.new_status for e in notifier.events] == [InvoiceStatus.CONFIRMED, InvoiceStatus.OVERPAID]


def test_check_payment_expires_when_past_expiry_with_no_funds(service, notifier):
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.EXPIRED
    assert [e.new_status for e in notifier.events] == [InvoiceStatus.EXPIRED]


def test_check_payment_ignores_expiry_once_fully_paid(service, chain_client):
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    chain_client.inject_transaction(
        tx_id="tx-1", address=invoice.payment_address, network="bitcoin", amount=Decimal("1.0"), confirmations=1
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.CONFIRMED


def test_check_payment_on_terminal_status_is_a_no_op(service, chain_client, notifier, sweep_strategy):
    invoice = service.create_invoice(
        user_reference="user-1",
        currency="BTC",
        network="bitcoin",
        requested_amount=Decimal("1.0"),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    service.check_payment(invoice.id)  # -> EXPIRED
    assert notifier.events[-1].new_status == InvoiceStatus.EXPIRED

    chain_client.inject_transaction(
        tx_id="tx-1", address=invoice.payment_address, network="bitcoin", amount=Decimal("1.0"), confirmations=1
    )
    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.EXPIRED
    assert sweep_strategy.sweeps == []
    assert len(notifier.events) == 1


def test_service_works_without_a_notifier(repository, address_provider, chain_client, sweep_strategy):
    service = PaymentService(
        repository=repository,
        address_provider=address_provider,
        chain_client=chain_client,
        sweep_strategy=sweep_strategy,
    )
    invoice = service.create_invoice(
        user_reference="user-1", currency="BTC", network="bitcoin", requested_amount=Decimal("1.0")
    )
    chain_client.inject_transaction(
        tx_id="tx-1", address=invoice.payment_address, network="bitcoin", amount=Decimal("1.0"), confirmations=1
    )

    result = service.check_payment(invoice.id)

    assert result.status == InvoiceStatus.CONFIRMED
    assert len(sweep_strategy.sweeps) == 1
