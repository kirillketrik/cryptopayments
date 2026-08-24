"""In-memory chain simulator fake for :class:`~cryptopayments.contracts.chain_client.ChainClient`."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from cryptopayments.contracts.chain_client import ChainClient
from cryptopayments.exceptions import UnknownTransactionError
from cryptopayments.models.chain import IncomingTransaction


class FakeChainClient(ChainClient):
    """A chain simulator tests can drive by hand.

    Real chain state can't be produced deterministically in tests, so this
    fake exposes :meth:`inject_transaction` and :meth:`set_confirmations` to
    let a test place a transaction on the "chain" and then advance its
    confirmation count, exercising :class:`~cryptopayments.service.PaymentService`'s
    polling logic without any network access.
    """

    def __init__(self) -> None:
        self._transactions: dict[tuple[str, str], IncomingTransaction] = {}

    def inject_transaction(
        self,
        *,
        tx_id: str,
        address: str,
        network: str,
        amount: Decimal,
        confirmations: int = 0,
    ) -> IncomingTransaction:
        """Simulate a new inbound transaction appearing on-chain."""
        tx = IncomingTransaction(
            tx_id=tx_id,
            address=address,
            network=network,
            amount=amount,
            confirmations=confirmations,
        )
        self._transactions[(network, tx_id)] = tx
        return tx

    def set_confirmations(self, tx_id: str, network: str, confirmations: int) -> IncomingTransaction:
        """Simulate additional blocks being mined on top of a previously injected transaction."""
        key = (network, tx_id)
        existing = self._transactions.get(key)
        if existing is None:
            raise UnknownTransactionError(tx_id)
        updated = dataclasses.replace(existing, confirmations=confirmations)
        self._transactions[key] = updated
        return updated

    def get_balance(self, address: str, network: str) -> Decimal:
        return sum(
            (tx.amount for tx in self._transactions.values() if tx.network == network and tx.address == address),
            start=Decimal("0"),
        )

    def list_incoming_transactions(self, address: str, network: str) -> list[IncomingTransaction]:
        return [
            tx for tx in self._transactions.values() if tx.network == network and tx.address == address
        ]

    def get_confirmations(self, tx_id: str, network: str) -> int:
        try:
            return self._transactions[(network, tx_id)].confirmations
        except KeyError:
            raise UnknownTransactionError(tx_id) from None
