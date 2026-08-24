"""Notification contract.

Unlike the other contracts, ``Notifier`` is a :class:`typing.Protocol` rather
than an ``abc.ABC``. It has exactly one method and no shared state or
lifecycle to enforce, so structural typing is a better fit: any object (or
even a plain function wrapped in a small adapter) that exposes a matching
``notify`` method satisfies it, without needing to import or subclass
anything from this library. See the README's "ABC vs Protocol" section for
the full rationale.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cryptopayments.models.events import PaymentEvent


@runtime_checkable
class Notifier(Protocol):
    """Fired whenever an invoice's status changes."""

    def notify(self, event: PaymentEvent) -> None:
        """Handle a single payment event (e.g. deliver a webhook, publish to a queue)."""
        ...
