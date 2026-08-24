"""Blockchain-reading contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from cryptopayments.models.chain import IncomingTransaction


class ChainClient(ABC):
    """Reads blockchain state for a network.

    This is a read-only contract by design: writing to a chain (sweeping
    funds) is a separate concern, see
    :class:`~cryptopayments.contracts.sweep_strategy.SweepStrategy`.
    """

    @abstractmethod
    def get_balance(self, address: str, network: str) -> Decimal:
        """Return the total confirmed-and-unconfirmed balance held at ``address``."""

    @abstractmethod
    def list_incoming_transactions(self, address: str, network: str) -> list[IncomingTransaction]:
        """Return every inbound transaction observed for ``address``."""

    @abstractmethod
    def get_confirmations(self, tx_id: str, network: str) -> int:
        """Return the current confirmation count for ``tx_id``.

        Raises:
            UnknownTransactionError: if ``tx_id`` has not been observed.
        """
