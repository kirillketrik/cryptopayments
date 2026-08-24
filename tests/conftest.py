from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cryptopayments.models.invoice import Invoice, InvoiceStatus


@pytest.fixture
def make_invoice():
    def _make(
        *,
        id: str = "inv-1",
        user_reference: str = "user-1",
        currency: str = "BTC",
        network: str = "bitcoin",
        requested_amount: Decimal = Decimal("1.0"),
        payment_address: str | None = "fake-addr-bitcoin-0001",
        status: InvoiceStatus = InvoiceStatus.PENDING,
        confirmations_required: int = 1,
        expires_at: datetime | None = None,
    ) -> Invoice:
        return Invoice(
            id=id,
            user_reference=user_reference,
            currency=currency,
            network=network,
            requested_amount=requested_amount,
            payment_address=payment_address,
            status=status,
            confirmations_required=confirmations_required,
            expires_at=expires_at,
        )

    return _make


@pytest.fixture
def utc_now() -> datetime:
    return datetime.now(timezone.utc)
