# Shared-address exact-amount payments

This workflow is separate from the original unique-address `PaymentService`.
Use `ExactPaymentService` for shared wallets; never aggregate all transfers to a
shared wallet through the old service. Contracts and sweep keys are unnecessary.

Supported read adapters: Ethereum mainnet USDT ERC20 (6 decimals), Tron mainnet
USDT TRC20 (6 decimals), native TON mainnet (9 decimals; not USDT jettons).
Token identity includes the network and contract, not the ticker.

## Library

```python
from decimal import Decimal
from cryptopayments import ExactPaymentService
from cryptopayments.adapters import TronUSDT

adapter = TronUSDT('YOUR_BASE58_WALLET')
service = ExactPaymentService('/persistent/path/payments.sqlite')
invoice = service.create_invoice(
    user_reference='order-123', asset=adapter.asset, amount=Decimal('10'),
    suffix_slots=1000, confirmations_required=1,
)
print(invoice.asset.address, str(invoice.payment_amount))
# Display payment_amount verbatim. Do not round or subtract transfer fees.
# Poll from invoice.created_at * 1000, follow all pagination fingerprints.
page, fingerprint = adapter.poll(invoice.created_at * 1000)
for transfer in page:
    service.ingest(transfer)
print(service.get(invoice.id).status)
```

The payable amount is base + 1..suffix_slots atomic units (with the default
USDT configuration, an extra 0.000001..0.001000 USDT). This surcharge belongs in
the displayed price and must be agreed with the customer. Decimal inputs must
fit asset precision; floats, non-finite values and nonpositive amounts fail.
Increase suffix_slots only if the resulting price range is acceptable.

SQLite serializes writers across connections/processes. All reservations are
permanent, including expired/paid invoices. An overlapping price range cannot
reuse reserved amounts. Exhaustion raises AmountSpaceExhausted rather than
miscrediting a late payment. At scale use additional wallets or different base
price ranges; finite fractional precision has finite lifetime capacity.
Back up the database: losing reservations and event history destroys this safety.
Use one authoritative database per wallet; separate databases cannot coordinate.
SQLite should be on local persistent storage, not a distributed network filesystem.

## CLI and scheduled reconciliation

Run from the repository with `uv run --frozen python -m cryptopayments`:

```sh
python -m cryptopayments --database /persistent/payments.sqlite create \
  --network ton --address '0:YOUR_64_HEX_ACCOUNT' --amount 1 --reference order-123
python -m cryptopayments --database /persistent/payments.sqlite get INVOICE_ID
python -m cryptopayments --database /persistent/payments.sqlite changes --after 0
python -m cryptopayments --database /persistent/payments.sqlite expire
```

Scan commands are suitable for a host scheduler. Ethereum: `scan-ethereum
--address 0x... --start BLOCK --end BLOCK`, with HTTPS ETHEREUM_RPC_URL in the
process environment. Tron: `scan-tron --address T... --start UNIX_MILLISECONDS`,
with optional TRONGRID_API_KEY. TON: `scan-ton --address 0:... --start UNIX_SECONDS
--end UNIX_SECONDS`, with optional TONCENTER_API_KEY. Never pass keys in CLI
arguments. No private keys are used. Required egress: your Ethereum RPC hostname,
api.trongrid.io and toncenter.com. Configure those destinations in your environment.

Persist a completed scan range only after the command succeeds; retry failures
with the same range. Overlap time/block windows so indexer lag does not lose
payments. TON offset pagination needs a fixed closed time window. Tron pagination
is followed until exhausted; do not advance a timestamp past unprocessed pages.
The scheduler and its cursor storage belong to the host application. Start at or
before the oldest unpaid invoice, not at the current chain head. Monitor scan
failures and indexer lag. Ethereum scan refreshes all stored events for this
wallet, including reorged events; reduce ranges if the provider limits logs.

## Reconciliation rules

One successful inbound transfer must match the exact integer amount, network,
asset, recipient and invoice time window [created_at, expires_at). Partial,
overpayments, wrong assets and late transfers remain in the transfers table for
manual reconciliation. They are never summed or guessed. Two payments of the
same invoice do not credit two orders. Event identity includes EVM log index or
Tron event index. A changed event payload is rejected. Do not call ingest with
untrusted customer-provided transaction data; only trusted chain observations.

An on-time transfer indexed after expiry can still confirm the original invoice.
A transfer after expiry cannot pay it. Under-confirmed on-time transfers remain
confirming past expiry until finality. Ethereum requires configurable block
confirmations (default 12); Tron and TON adapters use provider solidified/finalized
observations and require confirmations_required=1. This is finality semantics,
not an assertion that one block is sufficient on every chain.

Ethereum receipt refresh can downgrade confirmed to confirming or reorged.
Consumers must handle these changes instead of treating every confirmation as
irreversible. The changes feed is durable and at least once: persist sequence
only after applying a business action, and use sequence as an idempotency key.
Do not deliver goods solely because a client claims to have paid. A public exact
amount identifies an invoice, not the sender's legal identity.

No automatic refunds, signing, fund sweeps, fiat pricing, merchant HTTP API or
webhook delivery is included. Funds already arrive at the shared wallet; a host
integrates the library and consumes the notification feed. Read adapters have
fixture-based tests; production endpoints, rate limits, credentials and actual
mainnet transfers require integration validation before accepting real funds.
