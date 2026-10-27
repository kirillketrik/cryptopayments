# cryptopayments

Python 3.12+ library and CLI for accepting cryptocurrency payments on a shared
wallet address. Each invoice reserves a unique exact amount; incoming transfers
are matched by network, token contract, recipient, amount and payment window.
Payment state, transfer history and webhook deliveries are stored in SQLite.
No private keys are needed, and the library does not sign or broadcast transfers.

## Implemented functionality

- Atomic allocation of unique amounts, including concurrent processes.
- Exact integer atomic-unit matching; Decimal at the application boundary.
- Invoice expiry, confirmation tracking and Ethereum reorg receipt refresh.
- Read-only mainnet adapters for **USDT ERC20 (Ethereum)**, **USDT TRC20 (Tron)**
  and **native TON**. TON jettons and other EVM chains are not included.
- Persistent signed outgoing webhooks with retries, jitter, Retry-After,
  worker leases, dead letters and manual replay.
- CLI for creating/querying invoices, explicit-range scans and webhook workers.
- Multi-stage Docker image and Compose configuration for the webhook worker.

This is a library and worker toolkit. It does not expose a merchant HTTP API or
run a chain scheduler automatically. The host application supplies scan ranges,
persists completed cursors and integrates invoice creation into its checkout.
Mainnet adapters are tested with fixtures; validate your provider and real
transfers before accepting production payments.

## Install and test

```sh
uv sync --frozen --dev
uv run --frozen pytest
uv build
```

Runtime uses the Python standard library; there are no third-party runtime
dependencies. Tests also use `openssl` for a local HTTPS delivery integration test.

## Create an invoice

```python
from decimal import Decimal
from cryptopayments import Asset, ExactPaymentService
from cryptopayments.adapters import ERC20_USDT

service = ExactPaymentService('payments.sqlite')
asset = Asset('ethereum:1', ERC20_USDT,
              '0x1111111111111111111111111111111111111111', 6)
invoice = service.create_invoice(
    user_reference='order-123', asset=asset, amount=Decimal('10'),
    suffix_slots=1000, confirmations_required=12,
)
print(invoice.id, invoice.asset.address, str(invoice.payment_amount))
```

Payable amount is the base price plus **1..suffix_slots atomic units**. For USDT
with 6 decimals and 1,000 slots, a 10 USDT invoice requests between 10.000001 and
10.001000 USDT. Display `payment_amount` verbatim and disclose the final price.
The customer must send it in one transfer without subtracting fees. Underpayment,
overpayment or split payments are retained for manual reconciliation, never
summed or guessed. Assets are identified by network and contract, not ticker.

Reservations are **never reused**, including after expiry or payment. Otherwise a
late transfer could pay someone else's order. Capacity is finite; exhaustion
raises `AmountSpaceExhausted`. Expand the acceptable amount range, use another
base price or add wallets. All writers for a wallet must share the same database.
Back it up; use local persistent storage rather than network-mounted SQLite.

Default invoice TTL is 30 minutes. An on-time payment observed after expiry may
still confirm the original invoice. A payment outside the invoice window cannot.
States: `pending`, `confirming`, `confirmed`, `expired`, `reorged`. Confirmations
can be downgraded after a reorg. A confirmed invoice is not irrevocable settlement.

## Scan blockchains

```sh
uv run python -m cryptopayments --database payments.sqlite create \
  --network ethereum --address 0x1111111111111111111111111111111111111111 \
  --amount 10 --reference order-123
uv run python -m cryptopayments --database payments.sqlite get INVOICE_ID
uv run python -m cryptopayments --database payments.sqlite expire
uv run python -m cryptopayments --database payments.sqlite changes --after 0
```

Configure keys through the process environment; do not put credentials in CLI
arguments or commit them. Adapters use HTTPS with certificate verification.

| CLI command | Range units | Configuration | Finality |
| --- | --- | --- | --- |
| `scan-ethereum --address 0x… --start N --end N` | Inclusive block heights | `ETHEREUM_RPC_URL` (HTTPS Ethereum mainnet RPC) | Configurable depth, default 12 |
| `scan-tron --address T… --start N` | Unix milliseconds | Optional `TRONGRID_API_KEY` | TronGrid solidified transfers |
| `scan-ton --address 0:… --start N --end N` | Unix seconds | Optional `TONCENTER_API_KEY` | Toncenter masterchain-referenced transactions |

CLI-created Tron/TON invoices use `confirmations_required=1` to represent the
adapter's finalized observation, not one arbitrary blockchain confirmation.
TON uses a raw `workchain:64_hex_digits` wallet address. Tron uses Base58 addresses;
Ethereum recipients are normalized to lower case.

Start scans at or before the oldest invoice. Retry failed scans without advancing
the cursor, follow pagination, and overlap ranges for indexer lag. Persist a scan
cursor only after the command succeeds. TON pagination needs a fixed closed time
window. Ethereum scans also refresh previously recorded receipts to detect
removed events. Your scheduler must keep scanning and call `expire` regularly.
Required egress: your RPC host, `api.trongrid.io`, and `toncenter.com`.
See [matching and scanning details](docs/exact-amounts.md).

## Outgoing webhooks

Webhook workers consume the exact-payment status journal, including events
recorded before the subscription was created. Each subscription has a stable
name and fixed URL. The queue and its cursor are committed atomically; HTTP calls
happen outside database transactions. Multiple workers coordinate through leases.

Set `WEBHOOK_URL` to an operator-controlled HTTPS endpoint and `WEBHOOK_SECRET`
to a securely generated secret of at least 32 bytes. The secret is supplied at
runtime and is not stored in SQLite. Start a worker:

```sh
uv run python -m cryptopayments --database payments.sqlite webhooks --name orders
# Process at most one delivery (also enqueues the next batch of journal events):
uv run python -m cryptopayments --database payments.sqlite webhooks --name orders --once
# Inspect delivery states or retry a dead letter:
uv run python -m cryptopayments --database payments.sqlite webhooks --name orders --list
uv run python -m cryptopayments --database payments.sqlite webhooks --name orders --replay 42
```

SIGTERM/SIGINT stop the worker after the current HTTP attempt. Restarting resumes
persistent work. The default limit is 8 attempts, configurable with
`--max-attempts`. Retryable failures are network/timeouts, HTTP 408/425/429 and 5xx.
Other responses, including redirects, become dead letters. Only 2xx acknowledges
delivery. Requests time out after 10 seconds. Exponential retry windows start at
5 seconds, use jitter and cap at 1 hour. Retry-After seconds and HTTP dates are
honored up to the same cap. Library constructor parameters customize these values.
A crashed attempt counts toward the retry limit; its lease expires automatically.
No redirect following: signatures and bodies stay on the configured endpoint.

Payload:

```json
{
  "event_id": "42",
  "type": "invoice.status_changed",
  "created_at": 1793090000,
  "invoice": {
    "id": "invoice-id",
    "user_reference": "order-123",
    "asset": {
      "network": "ethereum:1",
      "asset_id": "0xdac17f958d2ee523a2206206994597c13d831ec7",
      "address": "0x1111111111111111111111111111111111111111",
      "decimals": 6
    },
    "payment_amount": "10.000123",
    "status": "confirmed"
  }
}
```

Headers: `X-Cryptopayments-Event-Id`, `X-Cryptopayments-Timestamp`, and
`X-Cryptopayments-Signature`. Signature is hex HMAC-SHA256 over
`timestamp + "." + raw_body`. Verify before parsing or acting:

```python
from cryptopayments import verify_signature

valid = verify_signature(
    secret, timestamp_header, raw_request_body, signature_header,
)  # default timestamp tolerance: 300 seconds
```

Retries retain the event ID and exact body; timestamp/signature are refreshed.
Delivery is **at least once**, not exactly once: a receiver may succeed before a
sender crashes. Deduplicate by event ID within this database/source, transactionally
with the business action. Events can arrive out of order because retries do not
block newer events. Use sequence IDs to avoid applying an older status over a
newer one, and handle `reorged`/confirmation downgrades. Keep clocks synchronized.
A new subscription name replays the journal; changing its URL under the existing
name fails. For secret rotation, coordinate the receiver and all workers; payloads
are signed with the currently supplied secret. Monitor `dead` deliveries and replay
only after resolving the endpoint problem. No inbound webhook receiver is exposed.

## Docker

```sh
docker build -t cryptopayments .
docker volume create payments-data
docker run --rm --mount source=payments-data,target=/data cryptopayments \
  --database /data/payments.sqlite create --network ethereum \
  --address 0x1111111111111111111111111111111111111111 --amount 10 --reference order-123
# With WEBHOOK_URL and WEBHOOK_SECRET already exported in the host environment:
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount source=payments-data,target=/data \
  -e WEBHOOK_URL -e WEBHOOK_SECRET cryptopayments \
  --database /data/payments.sqlite webhooks --name orders
```

Alternatively, `docker compose up -d --build` starts the webhook worker using the
required environment variables and a Compose-managed `payments` volume. Create
invoices in that same database with:

```sh
docker compose run --rm webhooks --database /data/payments.sqlite create \
  --network ethereum --address 0x1111111111111111111111111111111111111111 \
  --amount 10 --reference order-123
```

The image runs as UID/GID 10001 and installs the frozen runtime environment without
dev dependencies. Use a writable persistent `/data` volume; bind mounts must be
owned by UID 10001. Everything else can be read-only. There is no web server or
port to expose. Scanners must run separately against the same volume; the Compose
worker only delivers existing events and does not watch a blockchain or expire
invoices. Image builds do not include Foundry or Solidity sources.

## Original unique-address workflow and Solidity contracts

The original `PaymentService` remains available with pluggable repository,
address provider, chain client, sweep strategy and notifier interfaces. Its
bundled implementations are in-memory fakes, suitable for tests. **Do not use its
address-wide aggregation on a shared wallet**; use `ExactPaymentService` instead.

`contracts/` contains an independent EVM CREATE2 forwarder/factory implementation,
with Foundry tests and a deployment script. It is optional for shared-address
payments and has no Python RPC bridge. See [contracts README](contracts/README.md).

## Scope and validation

No automatic refunds, fund sweeps, fiat conversion, exchange integration, merchant
HTTP API, hosted receiver, or built-in chain polling scheduler is included.
Outgoing webhook delivery has automated retry, concurrency, restart and local
HTTPS tests. These checks do not validate live mainnet integrations or merchant
endpoints. SQLite reservations, events and delivery history must be backed up
together; history is not automatically pruned.
