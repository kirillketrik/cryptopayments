from __future__ import annotations

from decimal import Decimal

import pytest

from cryptopayments.exceptions import UnknownTransactionError
from cryptopayments.fakes.chain_client import FakeChainClient


@pytest.fixture
def chain() -> FakeChainClient:
    return FakeChainClient()


def test_inject_transaction_is_visible_in_incoming_transactions(chain):
    chain.inject_transaction(
        tx_id="tx-1", address="addr-1", network="bitcoin", amount=Decimal("0.5"), confirmations=0
    )
    txs = chain.list_incoming_transactions("addr-1", "bitcoin")
    assert len(txs) == 1
    assert txs[0].tx_id == "tx-1"
    assert txs[0].amount == Decimal("0.5")


def test_get_balance_sums_transactions_for_address_and_network(chain):
    chain.inject_transaction(tx_id="tx-1", address="addr-1", network="bitcoin", amount=Decimal("0.5"))
    chain.inject_transaction(tx_id="tx-2", address="addr-1", network="bitcoin", amount=Decimal("0.25"))
    chain.inject_transaction(tx_id="tx-3", address="addr-2", network="bitcoin", amount=Decimal("10"))
    chain.inject_transaction(tx_id="tx-4", address="addr-1", network="ethereum", amount=Decimal("99"))

    assert chain.get_balance("addr-1", "bitcoin") == Decimal("0.75")


def test_get_balance_unknown_address_is_zero(chain):
    assert chain.get_balance("nobody", "bitcoin") == Decimal("0")


def test_set_confirmations_advances_existing_transaction(chain):
    chain.inject_transaction(
        tx_id="tx-1", address="addr-1", network="bitcoin", amount=Decimal("1"), confirmations=0
    )
    chain.set_confirmations("tx-1", "bitcoin", 6)
    assert chain.get_confirmations("tx-1", "bitcoin") == 6


def test_set_confirmations_unknown_transaction_raises(chain):
    with pytest.raises(UnknownTransactionError):
        chain.set_confirmations("ghost", "bitcoin", 1)


def test_get_confirmations_unknown_transaction_raises(chain):
    with pytest.raises(UnknownTransactionError):
        chain.get_confirmations("ghost", "bitcoin")


def test_transactions_scoped_by_network(chain):
    chain.inject_transaction(tx_id="tx-1", address="addr-1", network="bitcoin", amount=Decimal("1"))
    assert chain.list_incoming_transactions("addr-1", "ethereum") == []
