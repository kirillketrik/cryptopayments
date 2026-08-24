"""In-memory fake for the :class:`~cryptopayments.contracts.notifier.Notifier` protocol."""

from __future__ import annotations

from cryptopayments.models.events import PaymentEvent


class InMemoryNotifier:
    """Records every event in-memory so tests can assert on them.

    Deliberately does not inherit from ``Notifier`` — it satisfies the
    protocol structurally, which is the point of using ``Protocol`` for this
    contract. See :mod:`cryptopayments.contracts.notifier`.
    """

    def __init__(self) -> None:
        self.events: list[PaymentEvent] = []

    def notify(self, event: PaymentEvent) -> None:
        self.events.append(event)
