"""Address-allocation contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from cryptopayments.models.address import AddressAllocation, AddressStrategy
from cryptopayments.models.invoice import Invoice


class AddressProvider(ABC):
    """Allocates a receiving address for an invoice on a given network.

    A network may support more than one allocation strategy (see
    :class:`~cryptopayments.models.address.AddressStrategy`); callers may pin
    a strategy explicitly, or let the implementation pick a sensible default
    for the invoice's network.
    """

    @abstractmethod
    def allocate_address(
        self,
        invoice: Invoice,
        *,
        strategy: AddressStrategy | None = None,
    ) -> AddressAllocation:
        """Allocate (or look up) a receiving address for ``invoice``.

        Args:
            invoice: the invoice to allocate an address for.
            strategy: force a specific strategy; if omitted, the
                implementation chooses one (e.g. based on ``invoice.network``).

        Raises:
            UnsupportedNetworkError: if ``invoice.network`` is not supported.
        """
