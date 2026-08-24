from __future__ import annotations

import pytest

from cryptopayments.exceptions import UnsupportedNetworkError
from cryptopayments.fakes.address_provider import FakeAddressProvider
from cryptopayments.models.address import AddressStrategy


def test_hd_derived_allocates_unique_address_per_invoice(make_invoice):
    provider = FakeAddressProvider(default_strategy=AddressStrategy.HD_DERIVED)
    invoice_a = make_invoice(id="inv-a", network="bitcoin")
    invoice_b = make_invoice(id="inv-b", network="bitcoin")

    allocation_a = provider.allocate_address(invoice_a)
    allocation_b = provider.allocate_address(invoice_b)

    assert allocation_a.address != allocation_b.address
    assert allocation_a.strategy is AddressStrategy.HD_DERIVED
    assert allocation_a.address.startswith("fake-addr-bitcoin-")


def test_counterfactual_create2_allocates_unique_address(make_invoice):
    provider = FakeAddressProvider()
    invoice = make_invoice(id="inv-a", network="ethereum")

    allocation = provider.allocate_address(invoice, strategy=AddressStrategy.COUNTERFACTUAL_CREATE2)

    assert allocation.strategy is AddressStrategy.COUNTERFACTUAL_CREATE2
    assert allocation.address.startswith("fake-create2-ethereum-")


def test_pooled_with_memo_shares_address_but_not_memo(make_invoice):
    provider = FakeAddressProvider()
    invoice_a = make_invoice(id="inv-a", network="tron")
    invoice_b = make_invoice(id="inv-b", network="tron")

    allocation_a = provider.allocate_address(invoice_a, strategy=AddressStrategy.POOLED_WITH_MEMO)
    allocation_b = provider.allocate_address(invoice_b, strategy=AddressStrategy.POOLED_WITH_MEMO)

    assert allocation_a.address == allocation_b.address
    assert allocation_a.memo != allocation_b.memo
    assert allocation_a.memo is not None


def test_strategy_by_network_default(make_invoice):
    provider = FakeAddressProvider(
        default_strategy=AddressStrategy.HD_DERIVED,
        strategy_by_network={"tron": AddressStrategy.POOLED_WITH_MEMO},
    )
    tron_invoice = make_invoice(id="inv-a", network="tron")
    btc_invoice = make_invoice(id="inv-b", network="bitcoin")

    assert provider.allocate_address(tron_invoice).strategy is AddressStrategy.POOLED_WITH_MEMO
    assert provider.allocate_address(btc_invoice).strategy is AddressStrategy.HD_DERIVED


def test_get_allocation_returns_recorded_mapping(make_invoice):
    provider = FakeAddressProvider()
    invoice = make_invoice(id="inv-a")

    assert provider.get_allocation("inv-a") is None
    allocation = provider.allocate_address(invoice)
    assert provider.get_allocation("inv-a") == allocation


def test_unsupported_network_raises(make_invoice):
    provider = FakeAddressProvider(supported_networks={"bitcoin"})
    invoice = make_invoice(id="inv-a", network="dogecoin")

    with pytest.raises(UnsupportedNetworkError):
        provider.allocate_address(invoice)
