"""Value objects for the address-allocation contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AddressStrategy(str, Enum):
    """How a receiving address is produced for an invoice.

    A real implementation would pick between these based on network support
    and operational cost; Phase 1 fakes simulate all three without doing any
    real cryptography.
    """

    HD_DERIVED = "hd_derived"
    """A fresh address per invoice, derived from an HD wallet."""

    COUNTERFACTUAL_CREATE2 = "counterfactual_create2"
    """A deterministic contract address (e.g. CREATE2) that is only deployed once funded."""

    POOLED_WITH_MEMO = "pooled_with_memo"
    """A single shared address per network, disambiguated by a per-invoice memo/tag."""


@dataclass(frozen=True)
class AddressAllocation:
    """The result of allocating a receiving address to an invoice."""

    address: str
    network: str
    strategy: AddressStrategy
    memo: str | None = None
