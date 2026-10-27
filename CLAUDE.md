# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --dev                          # install deps (dev group includes pytest, pytest-cov)
uv run pytest                          # run full test suite with coverage (config in pyproject.toml)
uv run pytest tests/test_payment_service.py                    # run one test file
uv run pytest tests/test_payment_service.py::test_check_payment_overpaid_after_confirmed_sweeps_again  # run one test
uv run pytest -k "sweep"               # run tests matching a keyword
```

Coverage is enforced via `addopts = "--cov=cryptopayments --cov-report=term-missing"` in `pyproject.toml` — every `uv run pytest` invocation reports coverage automatically. The project currently sits at 100% coverage; treat a drop as a signal to add a test, not to ignore.

There is no configured linter/formatter yet — none is set up in `pyproject.toml`.

## Architecture

This is Phase 1 of a pluggable crypto-payment library: the full contract layer plus in-memory fakes for every contract, wired together through one orchestrator. **No real blockchain, database, HD-wallet, or webhook I/O exists yet** — everything is a fake, and the entire point of Phase 1 is proving the seams are right before real implementations land behind them. When extending this codebase, preserve that separation: business logic in `service.py` must only ever call the abstract contracts in `contracts/`, never a concrete fake directly.

### The five contracts (`src/cryptopayments/contracts/`)

`PaymentRepository`, `AddressProvider`, `ChainClient`, `SweepStrategy` are `abc.ABC`s; `Notifier` is a `typing.Protocol` (`@runtime_checkable`). The rule: multi-method stateful "service" contracts are ABCs, single-method callback-shaped contracts are Protocols. Follow this rule for any new contract rather than defaulting to one or the other — see the "abc.ABC vs typing.Protocol" section in [README.md](README.md) for the full rationale.

Each contract has exactly one fake in `src/cryptopayments/fakes/`, used both by the test suite and as a reference for what a real implementation must satisfy (raised exceptions, method signatures). `InMemoryNotifier` deliberately does *not* subclass `Notifier` — it satisfies the Protocol structurally, demonstrating that host apps don't need to import/subclass anything for that one contract.

### `PaymentService` (`src/cryptopayments/service.py`)

The orchestrator. Constructed with all five contracts injected — never hardcode a fake or a concrete implementation inside it. Two entry points:

- `create_invoice(...)` — allocates an address via `AddressProvider` and persists via `PaymentRepository`.
- `check_payment(invoice_id)` — **idempotent and safe to call repeatedly** (e.g. from a poller or webhook handler). It re-derives status from `ChainClient`'s current view every call rather than tracking a delta itself, so calling it twice with no new chain activity is a no-op. On an actual status change it updates the repository, sweeps via `SweepStrategy`, and notifies via `Notifier`.

The one non-obvious invariant in `check_payment`: `SweepStrategy.sweep()` is always called with the *incrementally new* amount seen since the previous check (`total_received - previously_received`), not the invoice's cumulative total. This is what lets a `CONFIRMED -> OVERPAID` top-up sweep correctly without re-sweeping already-moved funds — get this wrong and either double-sweeps or missed sweeps result. See `test_check_payment_overpaid_after_confirmed_sweeps_again` in [tests/test_payment_service.py](tests/test_payment_service.py) as the regression test for this.

### Invoice state machine (`src/cryptopayments/models/invoice.py`)

`Invoice.status` must only ever be changed via `Invoice.transition_to()`, which checks the `_ALLOWED_TRANSITIONS` table and raises `InvalidStatusTransitionError` on an illegal move. Never set `invoice.status = ...` directly. Two transitions exist beyond the naive linear pipeline — `PENDING -> CONFIRMED` (a single tx can fully pay and confirm at once) and `PARTIALLY_PAID -> OVERPAID` (a top-up can skip past `CONFIRMED`) — both are intentional, not bugs. `OVERPAID` and `EXPIRED` are terminal; `check_payment` treats terminal statuses as a no-op.

### Exceptions

All library exceptions subclass `PaymentLibraryError` (`src/cryptopayments/exceptions.py`) and carry structured context (e.g. `InvoiceNotFoundError.invoice_id`, `InvalidStatusTransitionError.current_status`/`new_status`) rather than only a message string. Any new fake/implementation should raise these rather than bare `ValueError`/`KeyError` where a matching exception exists.

### Money handling

Every monetary field is `Decimal`, never `float`, anywhere in this library — this is a hard rule (`Invoice.requested_amount`/`amount_received`, `IncomingTransaction.amount`, `SweepResult.amount`).

### Testing pattern

Each fake is tested in isolation (`tests/test_fake_*.py`); `tests/test_invoice_state_machine.py` covers the transition table exhaustively; `tests/test_payment_service.py` wires all fakes together via fixtures and tests the orchestrator end-to-end, including multi-step scenarios (progressive confirmations, overpay-after-confirm, expiry races). `FakeChainClient.inject_transaction()` / `.set_confirmations()` are the hand-driving primitives used to simulate chain state changes across a `check_payment` sequence — reach for these rather than adding new fake surface when writing a new service-level test.

## Keeping this file current

When a change alters the contract surface (adding/removing a contract or fake), changes the state machine's transition table, or changes an orchestration invariant in `PaymentService` (e.g. the sweep-delta behavior above), update the corresponding section of this file as part of that change rather than leaving it to drift.

## Exact-amount shared-wallet workflow

`exact.py` is a separate durable SQLite workflow, exported as ExactPaymentService.
Reservations never expire or recycle: late payments must not pay another invoice.
Use integer atomic units and Decimal conversion; scope identity by network, token
contract and recipient. Serialize allocation and reconciliation transactions.
`adapters.py` provides read-only Ethereum USDT, Tron USDT and native TON adapters;
`__main__.py` supplies CLI commands. Never route shared wallets through the old
PaymentService's address-wide aggregation. State changes for this workflow are
pending/confirming/confirmed/expired/reorged, with downgrades for chain reorgs.
The changes feed uses persistent sequence IDs for consumer idempotency.
See docs/exact-amounts.md for finality, pagination and integration limitations.

## Webhook delivery and container runtime

`webhooks.py` consumes the exact-payment journal into persistent per-subscription
queues. Preserve stable event IDs/bodies, HMAC over timestamp.raw_body, retry
classification and lease-token fencing. Network calls must remain outside SQLite
transactions. Delivery is at least once and can be out of order. Redirects must
not forward signed payloads. CLI `webhooks` supports continuous/one-shot operation,
inspection and dead-letter replay; it does not scan chains. Docker runs as UID
10001 with a writable /data volume and no network listener. README documents the
actual API, queue semantics and integration limits.
