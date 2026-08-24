from __future__ import annotations

import pytest

from cryptopayments.exceptions import InvoiceAlreadyExistsError, InvoiceNotFoundError
from cryptopayments.fakes.repository import InMemoryPaymentRepository
from cryptopayments.models.invoice import InvoiceStatus


@pytest.fixture
def repo() -> InMemoryPaymentRepository:
    return InMemoryPaymentRepository()


def test_create_and_get(repo, make_invoice):
    invoice = make_invoice(id="inv-1")
    repo.create(invoice)
    assert repo.get("inv-1") is invoice


def test_create_duplicate_raises(repo, make_invoice):
    repo.create(make_invoice(id="inv-1"))
    with pytest.raises(InvoiceAlreadyExistsError):
        repo.create(make_invoice(id="inv-1"))


def test_get_missing_raises(repo):
    with pytest.raises(InvoiceNotFoundError):
        repo.get("does-not-exist")


def test_update_persists_changes(repo, make_invoice):
    invoice = make_invoice(id="inv-1", status=InvoiceStatus.PENDING)
    repo.create(invoice)
    invoice.transition_to(InvoiceStatus.PARTIALLY_PAID)
    repo.update(invoice)
    assert repo.get("inv-1").status == InvoiceStatus.PARTIALLY_PAID


def test_update_missing_raises(repo, make_invoice):
    with pytest.raises(InvoiceNotFoundError):
        repo.update(make_invoice(id="ghost"))


def test_find_by_address(repo, make_invoice):
    invoice = make_invoice(id="inv-1", payment_address="fake-addr-x")
    repo.create(invoice)
    assert repo.find_by_address("fake-addr-x") is invoice
    assert repo.find_by_address("unknown-address") is None


def test_list_filters_by_status_and_user(repo, make_invoice):
    repo.create(make_invoice(id="inv-1", user_reference="alice", status=InvoiceStatus.PENDING))
    repo.create(make_invoice(id="inv-2", user_reference="alice", status=InvoiceStatus.CONFIRMED))
    repo.create(make_invoice(id="inv-3", user_reference="bob", status=InvoiceStatus.PENDING))

    assert {i.id for i in repo.list()} == {"inv-1", "inv-2", "inv-3"}
    assert {i.id for i in repo.list(status=InvoiceStatus.PENDING)} == {"inv-1", "inv-3"}
    assert {i.id for i in repo.list(user_reference="alice")} == {"inv-1", "inv-2"}
    assert {i.id for i in repo.list(status=InvoiceStatus.PENDING, user_reference="alice")} == {"inv-1"}
