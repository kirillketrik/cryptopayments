"""In-memory fake for :class:`~cryptopayments.contracts.address_provider.AddressProvider`."""

from __future__ import annotations

from uuid import uuid4

from cryptopayments.contracts.address_provider import AddressProvider
from cryptopayments.exceptions import UnsupportedNetworkError
from cryptopayments.models.address import AddressAllocation, AddressStrategy
from cryptopayments.models.invoice import Invoice


class FakeAddressProvider(AddressProvider):
    """Generates fake-looking addresses without doing any real cryptography.

    Simulates all three :class:`AddressStrategy` values:

    * ``HD_DERIVED`` / ``COUNTERFACTUAL_CREATE2``: a fresh, unique fake address per invoice.
    * ``POOLED_WITH_MEMO``: one shared fake address per network, with a unique memo per invoice.

    ``supported_networks``, if given, restricts which networks this provider
    will allocate for, raising :class:`UnsupportedNetworkError` otherwise —
    this exercises that error path even though Phase 1 has no real
    per-network configuration.
    """

    def __init__(
        self,
        *,
        default_strategy: AddressStrategy = AddressStrategy.HD_DERIVED,
        strategy_by_network: dict[str, AddressStrategy] | None = None,
        supported_networks: set[str] | None = None,
    ) -> None:
        self._default_strategy = default_strategy
        self._strategy_by_network = dict(strategy_by_network or {})
        self._supported_networks = supported_networks
        self._allocations_by_invoice: dict[str, AddressAllocation] = {}
        self._pooled_addresses: dict[str, str] = {}

    def allocate_address(
        self,
        invoice: Invoice,
        *,
        strategy: AddressStrategy | None = None,
    ) -> AddressAllocation:
        if self._supported_networks is not None and invoice.network not in self._supported_networks:
            raise UnsupportedNetworkError(invoice.network)

        chosen = strategy or self._strategy_by_network.get(invoice.network, self._default_strategy)

        if chosen is AddressStrategy.POOLED_WITH_MEMO:
            address = self._pooled_addresses.setdefault(
                invoice.network, f"fake-pool-{invoice.network}-{uuid4().hex[:8]}"
            )
            allocation = AddressAllocation(
                address=address,
                network=invoice.network,
                strategy=chosen,
                memo=f"memo-{uuid4().hex[:12]}",
            )
        else:
            prefix = "fake-create2" if chosen is AddressStrategy.COUNTERFACTUAL_CREATE2 else "fake-addr"
            allocation = AddressAllocation(
                address=f"{prefix}-{invoice.network}-{uuid4().hex[:16]}",
                network=invoice.network,
                strategy=chosen,
            )

        self._allocations_by_invoice[invoice.id] = allocation
        return allocation

    def get_allocation(self, invoice_id: str) -> AddressAllocation | None:
        """Test/introspection helper: look up the allocation previously made for an invoice."""
        return self._allocations_by_invoice.get(invoice_id)
