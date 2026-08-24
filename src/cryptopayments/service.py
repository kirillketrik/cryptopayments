"""``PaymentService``: the orchestrator that wires the contracts together.

This is the single convenient entry point a host application uses. It is
constructed with concrete implementations of each contract (dependency
injection, not hardcoded to the fakes) and knows nothing about whether those
implementations are fake or real.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from cryptopayments.contracts.address_provider import AddressProvider
from cryptopayments.contracts.chain_client import ChainClient
from cryptopayments.contracts.notifier import Notifier
from cryptopayments.contracts.repository import PaymentRepository
from cryptopayments.contracts.sweep_strategy import SweepStrategy
from cryptopayments.models.address import AddressStrategy
from cryptopayments.models.events import PaymentEvent, PaymentEventType
from cryptopayments.models.invoice import Invoice, InvoiceStatus

_TERMINAL_STATUSES = frozenset({InvoiceStatus.EXPIRED, InvoiceStatus.OVERPAID})
_CONFIRMED_LIKE_STATUSES = frozenset({InvoiceStatus.CONFIRMED, InvoiceStatus.OVERPAID})
_EXPIRABLE_STATUSES = frozenset({InvoiceStatus.PENDING, InvoiceStatus.PARTIALLY_PAID})


class PaymentService:
    """Orchestrates invoice creation and payment checking across the injected contracts."""

    def __init__(
        self,
        *,
        repository: PaymentRepository,
        address_provider: AddressProvider,
        chain_client: ChainClient,
        sweep_strategy: SweepStrategy,
        notifier: Notifier | None = None,
    ) -> None:
        self._repository = repository
        self._address_provider = address_provider
        self._chain_client = chain_client
        self._sweep_strategy = sweep_strategy
        self._notifier = notifier

    def create_invoice(
        self,
        *,
        user_reference: str,
        currency: str,
        network: str,
        requested_amount: Decimal,
        confirmations_required: int = 1,
        expires_at: datetime | None = None,
        address_strategy: AddressStrategy | None = None,
    ) -> Invoice:
        """Create an invoice, allocate it a receiving address, and persist it."""
        invoice = Invoice(
            id=str(uuid4()),
            user_reference=user_reference,
            currency=currency,
            network=network,
            requested_amount=requested_amount,
            confirmations_required=confirmations_required,
            expires_at=expires_at,
        )
        allocation = self._address_provider.allocate_address(invoice, strategy=address_strategy)
        invoice.payment_address = allocation.address
        self._repository.create(invoice)
        return invoice

    def check_payment(self, invoice_id: str) -> Invoice:
        """Re-check an invoice's on-chain state and advance its status if warranted.

        Queries the chain client for incoming transactions to the invoice's
        address, updates the invoice's status through the state machine,
        triggers the sweep strategy the moment the invoice first reaches a
        confirmed-or-better status, and fires the notifier on any status
        change (including expiry).
        """
        invoice = self._repository.get(invoice_id)
        previous_status = invoice.status

        if previous_status in _TERMINAL_STATUSES:
            return invoice

        previously_received = invoice.amount_received

        transactions = self._chain_client.list_incoming_transactions(invoice.payment_address, invoice.network)
        total_received = sum((tx.amount for tx in transactions), start=Decimal("0"))
        confirmations = min((tx.confirmations for tx in transactions), default=0)

        invoice.amount_received = total_received
        invoice.confirmations_received = confirmations

        new_status = self._determine_status(invoice, total_received, confirmations)

        if new_status == previous_status and self._has_expired(invoice, previous_status):
            new_status = InvoiceStatus.EXPIRED

        if new_status != previous_status:
            invoice.transition_to(new_status)

        self._repository.update(invoice)

        if new_status != previous_status:
            if new_status in _CONFIRMED_LIKE_STATUSES:
                # Sweep only the funds newly accounted for since the last check, so a
                # later top-up (CONFIRMED -> OVERPAID) sweeps the top-up amount rather
                # than re-sweeping funds an earlier check_payment call already moved.
                newly_available = total_received - previously_received
                self._sweep_strategy.sweep(invoice, amount=newly_available)
            self._notify(invoice, previous_status, new_status)

        return invoice

    @staticmethod
    def _determine_status(invoice: Invoice, total_received: Decimal, confirmations: int) -> InvoiceStatus:
        if total_received <= 0:
            return invoice.status

        meets_confirmations = confirmations >= invoice.confirmations_required
        if meets_confirmations and total_received > invoice.requested_amount:
            return InvoiceStatus.OVERPAID
        if meets_confirmations and total_received >= invoice.requested_amount:
            return InvoiceStatus.CONFIRMED
        return InvoiceStatus.PARTIALLY_PAID

    @staticmethod
    def _has_expired(invoice: Invoice, status: InvoiceStatus) -> bool:
        if invoice.expires_at is None or status not in _EXPIRABLE_STATUSES:
            return False
        return datetime.now(timezone.utc) >= invoice.expires_at

    def _notify(self, invoice: Invoice, previous_status: InvoiceStatus, new_status: InvoiceStatus) -> None:
        if self._notifier is None:
            return
        event = PaymentEvent(
            invoice_id=invoice.id,
            event_type=PaymentEventType.STATUS_CHANGED,
            previous_status=previous_status,
            new_status=new_status,
            occurred_at=datetime.now(timezone.utc),
        )
        self._notifier.notify(event)
