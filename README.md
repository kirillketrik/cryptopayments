# cryptopayments

A pluggable Python library for accepting cryptocurrency payments. A host application
swaps storage, address generation, blockchain access, and fund-sweeping logic in and
out by implementing a small set of contracts — business logic (`PaymentService`) never
changes.

**This is Phase 1.** It defines the full contract layer and ships fake, in-memory
implementations of every contract, wired together through `PaymentService`. There is no
real blockchain integration, database backend, or HD wallet/CREATE2 logic yet — the goal
here is a working, fully testable skeleton that proves the abstractions hold together
end to end. Later phases replace the fakes with real implementations behind the same
interfaces.

## Layout

```
src/cryptopayments/
  exceptions.py        # PaymentLibraryError and subclasses
  models/               # dataclasses / enums shared across contracts
    invoice.py            # Invoice + InvoiceStatus state machine
    address.py            # AddressAllocation, AddressStrategy
    chain.py               # IncomingTransaction
    sweep.py                # SweepResult
    events.py                # PaymentEvent, PaymentEventType
  contracts/            # abstract interfaces a host application implements
    repository.py         # PaymentRepository
    address_provider.py   # AddressProvider
    chain_client.py        # ChainClient
    sweep_strategy.py       # SweepStrategy
    notifier.py               # Notifier
  fakes/                # in-memory implementations of every contract, for tests/dev
  service.py            # PaymentService, the orchestrator
tests/                  # unit tests per fake + state machine + end-to-end pipeline tests
```

## The contracts

### `Invoice` and its status state machine (`models/invoice.py`)

An `Invoice` tracks a request for payment: who it's for, what currency/network/amount,
the address assigned to receive it, and its status. Status is only ever changed through
`Invoice.transition_to()`, which enforces the legal state graph and raises
`InvalidStatusTransitionError` on anything else:

```
PENDING ──────► PARTIALLY_PAID ──────► CONFIRMED ──────► OVERPAID
   │                  │                                     ▲
   │                  └─────────────────────────────────────┘
   ├──────────────────────────────────────────────► CONFIRMED
   └──────────────────────────────────────────────► EXPIRED
   PARTIALLY_PAID ────────────────────────────────► EXPIRED
```

Two transitions beyond the literal spec are included deliberately, flagged here rather
than silently added:

- `PENDING → CONFIRMED` directly — a single transaction can fully pay *and* confirm an
  invoice in one shot without ever being partially paid.
- `PARTIALLY_PAID → OVERPAID` directly — a top-up transaction can push a partially paid
  invoice straight past the requested amount without passing through `CONFIRMED`.

`EXPIRED` and `OVERPAID` are terminal: nothing transitions out of them.

### `PaymentRepository` (`contracts/repository.py`)

Storage for invoices: `create`, `get`, `update`, `find_by_address` (used by
`PaymentService` — well, in this phase invoices are looked up by id, but real chain
listeners/webhook handlers will typically need to map an incoming-tx address back to an
invoice), and `list` with optional `status`/`user_reference` filters.

**Fake:** `InMemoryPaymentRepository` — a `dict[str, Invoice]`. It stores invoices *by
reference*, so mutating an `Invoice` fetched via `get()` and then calling `update()` with
it is safe and is exactly the pattern `PaymentService` uses. A real, out-of-process
backend won't have that property — don't rely on it outside tests.

**Swapping in a real implementation:** wrap your ORM/database — e.g. a Django model
manager, a SQLAlchemy repository, or a thin wrapper around Redis/Mongo. `get`/`update`
must raise `InvoiceNotFoundError`; `create` must raise `InvoiceAlreadyExistsError` on a
duplicate id.

### `AddressProvider` (`contracts/address_provider.py`)

Allocates a receiving address for an invoice on a network. Real deployments pick between
multiple *strategies* per network — modeled here as `AddressStrategy`:

- `HD_DERIVED` — a fresh address per invoice, derived from an HD wallet.
- `COUNTERFACTUAL_CREATE2` — a deterministic contract address that's only deployed once
  funded.
- `POOLED_WITH_MEMO` — one shared address per network, disambiguated by a per-invoice
  memo/tag (common for chains like Tron/Stellar/XRP where per-invoice addresses are
  impractical).

**Fake:** `FakeAddressProvider` simulates all three without any real cryptography —
`fake-addr-<network>-<hex>` / `fake-create2-<network>-<hex>` for the first two, and a
shared `fake-pool-<network>-<hex>` address with a unique memo for the third. It accepts
an optional `supported_networks` set purely to exercise `UnsupportedNetworkError`; Phase
1 otherwise has no real per-network configuration.

**Swapping in a real implementation:** wrap actual HD wallet derivation (BIP32/44) for
`HD_DERIVED`, a CREATE2 factory contract call for `COUNTERFACTUAL_CREATE2`, or a
configured pool address + memo/tag generator for `POOLED_WITH_MEMO`.

### `ChainClient` (`contracts/chain_client.py`)

Read-only access to chain state: `get_balance`, `list_incoming_transactions`,
`get_confirmations`. Deliberately excludes anything that writes to a chain — that's
`SweepStrategy`'s job.

**Fake:** `FakeChainClient` is a hand-drivable chain simulator. Tests call
`inject_transaction(...)` to place a transaction on the "chain" and `set_confirmations(...)`
to advance it, so `PaymentService`'s polling/checking logic can be exercised
deterministically without any network access.

**Swapping in a real implementation:** wrap an RPC client / indexer API (e.g.
`web3.py`, a block explorer API, `bitcoinrpc`, ...), typically with a background
poller or webhook listener calling `PaymentService.check_payment` when new blocks land.

### `SweepStrategy` (`contracts/sweep_strategy.py`)

Moves confirmed funds from an invoice's deposit address to the host's main wallet.
`PaymentService` calls it with the *incrementally new* amount seen since the last check
(not the invoice's running total), so it's called again — with just the top-up amount —
if an invoice goes from `CONFIRMED` to `OVERPAID` later.

**Fake:** `FakeSweepStrategy` just records a `SweepResult` in-memory and logs the
simulated action; no real transfer happens.

**Swapping in a real implementation:** a direct on-chain transfer from the deposit
address (if the host controls its key), a call to a gas-funded forwarder contract, or a
batched/scheduled sweep job.

### `Notifier` (`contracts/notifier.py`)

Fired whenever an invoice's status changes. This is the one contract defined as a
`typing.Protocol` rather than an `abc.ABC` — see below for why.

**Fake:** `InMemoryNotifier` appends every `PaymentEvent` to a list so tests can assert
on it. It deliberately does *not* subclass `Notifier`, to demonstrate that it doesn't
need to.

**Swapping in a real implementation:** deliver a webhook, publish to a queue (Celery
task, SQS, ...), or push over a Django Channels group — see "Sync now, async later" below.

### `PaymentService` (`service.py`)

The orchestrator. Constructed with all five contracts injected (no hardcoded fakes):

```python
service = PaymentService(
    repository=InMemoryPaymentRepository(),
    address_provider=FakeAddressProvider(),
    chain_client=FakeChainClient(),
    sweep_strategy=FakeSweepStrategy(),
    notifier=InMemoryNotifier(),  # optional — omit to run without notifications
)

invoice = service.create_invoice(
    user_reference="user-123",
    currency="BTC",
    network="bitcoin",
    requested_amount=Decimal("0.5"),
)

# ... later, e.g. from a poller or a webhook handler ...
invoice = service.check_payment(invoice.id)
```

`check_payment` re-derives status from the chain client's current view every time it's
called (not from a delta since the last call), so it's safe to call repeatedly/idempotently
— it's a no-op unless something on-chain actually changed. On a status change it: updates
the invoice via the repository, sweeps via `SweepStrategy` the moment the invoice reaches
`CONFIRMED`/`OVERPAID`, and fires `Notifier`.

## Design notes

### `abc.ABC` vs `typing.Protocol`

The rule applied consistently: **multi-method, stateful "service" contracts are `ABC`s;
single-method, callback-shaped contracts are `Protocol`s.**

`PaymentRepository`, `AddressProvider`, `ChainClient`, and `SweepStrategy` are all `ABC`s.
They each expose more than one related method, a host implementation genuinely "is a"
repository/provider/client, and `abc`'s enforcement (you can't instantiate an incomplete
implementation) is a feature, not friction, for something a codebase will implement once
and reuse everywhere.

`Notifier` is a `Protocol`. It has exactly one method and no shared lifecycle or state to
enforce. Structural typing means a host application's existing webhook dispatcher, a
Celery task wrapper, or even a plain function adapted with a one-line class already
satisfies it — no need to import and subclass a library base class just to hand over a
callback. `@runtime_checkable` is added so `isinstance()` checks against it work, which
is convenient in tests (see `test_fake_notifier.py`).

If this library grows, other single-method contracts (e.g. a currency-rate lookup) should
follow `Notifier`'s lead; anything that accumulates more than one closely-related method
should follow the others.

### Sync now, async later

All contracts are synchronous. The host stack (Django Channels/Celery) is
async-friendly elsewhere, so I/O-bound methods (`ChainClient`, `Notifier`) are kept to
single, narrow calls with no internal state — easy to wrap with `asyncio.to_thread` or
run inside a Celery task without an interface change. I didn't add `async def` variants
now because Phase 1 has nothing that actually awaits anything (the fakes are pure
in-memory), and speculative async would just be surface area to get wrong before a real
I/O-bound implementation exists to validate it against.

### Amounts are always `Decimal`

Every monetary field (`Invoice.requested_amount`, `amount_received`,
`IncomingTransaction.amount`, `SweepResult.amount`) is `Decimal`. `float` never appears in
a monetary context anywhere in this library — floating-point error is not an acceptable
risk for payment amounts.

### A contract boundary worth flagging

`PaymentRepository.find_by_address` isn't used by `PaymentService` in Phase 1 —
`check_payment` takes an invoice id, not an address. It's included because it's the
obvious method a real chain listener or inbound-webhook handler will need immediately
("a transaction just landed on address X, which invoice is that?"), and it's a repository
concern, not an orchestrator one. If Phase 2 doesn't end up needing it in that shape,
it should move rather than linger.

## Development

```bash
uv sync --dev       # install
uv run pytest        # run tests with coverage (see pyproject.toml for config)
```

## Explicitly out of scope for Phase 1

Real network/RPC calls, real HD wallet derivation, real CREATE2/contract deployment,
real database drivers, real webhook delivery, and any multi-network business rules
beyond accepting a `network`/`currency` parameter.
