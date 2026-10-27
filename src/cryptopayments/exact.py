"""Durable exact-amount invoices for shared wallets.

Amounts are integer atomic units at the persistence boundary. Reservations are
never reused: an arbitrarily late transfer must not pay a newer invoice.
"""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


class AmountSpaceExhausted(RuntimeError):
    """All suffixes in the requested amount range have already been reserved."""


@dataclass(frozen=True)
class Asset:
    network: str
    asset_id: str  # canonical token contract/mint, or 'native'; never ticker alone
    address: str
    decimals: int

    def __post_init__(self):
        if not all((self.network, self.asset_id, self.address)):
            raise ValueError("network, asset_id and address are required")
        if type(self.decimals) is not int or not 0 <= self.decimals <= 36:
            raise ValueError("decimals must be between 0 and 36")

    def units(self, amount: Decimal) -> int:
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be a finite positive Decimal")
        numerator, denominator = amount.as_integer_ratio()
        units, remainder = divmod(numerator * 10**self.decimals, denominator)
        if remainder:
            raise ValueError("amount exceeds asset precision")
        return units

    def amount(self, units: int) -> Decimal:
        return Decimal(f'{units}e-{self.decimals}')


@dataclass(frozen=True)
class ExactInvoice:
    id: str
    user_reference: str
    asset: Asset
    base_units: int
    pay_units: int
    created_at: int
    expires_at: int
    confirmations_required: int
    status: str
    event_id: str | None

    @property
    def payment_amount(self) -> Decimal:
        return self.asset.amount(self.pay_units)


@dataclass(frozen=True)
class Transfer:
    """Trusted adapter observation, with unique tx-hash + log/output index identity.

    observed_at is the chain inclusion timestamp, not the polling timestamp.
    Only successful transfers to the exact recipient are submitted by adapters.
    """
    event_id: str
    asset: Asset
    units: int
    observed_at: int
    confirmations: int
    removed: bool = False


class ExactPaymentService:
    """SQLite-backed allocator and reconciler. Safe across processes and restarts.

    Each operation opens a connection; do not use ':memory:'. No funds are moved.
    Adapters must submit confirmation updates and reorg removals for known events.
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        if self.database == ':memory:':
            raise ValueError('use a persistent SQLite file')
        with self._transaction() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY, user_reference TEXT NOT NULL,
                network TEXT NOT NULL, asset_id TEXT NOT NULL, address TEXT NOT NULL,
                decimals INTEGER NOT NULL, base_units TEXT NOT NULL, pay_units TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                confirmations_required INTEGER NOT NULL, status TEXT NOT NULL,
                event_id TEXT,
                UNIQUE(network, asset_id, address, pay_units));
            CREATE TABLE IF NOT EXISTS transfers (
                network TEXT NOT NULL, asset_id TEXT NOT NULL, event_id TEXT NOT NULL,
                address TEXT NOT NULL, units TEXT NOT NULL, observed_at INTEGER NOT NULL,
                confirmations INTEGER NOT NULL, removed INTEGER NOT NULL,
                PRIMARY KEY(network, asset_id, event_id));
            CREATE TABLE IF NOT EXISTS changes (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT NOT NULL,
                status TEXT NOT NULL, created_at INTEGER NOT NULL);
            ''')

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _invoice(row):
        return ExactInvoice(row['id'], row['user_reference'],
            Asset(row['network'], row['asset_id'], row['address'], row['decimals']),
            int(row['base_units']), int(row['pay_units']), row['created_at'],
            row['expires_at'], row['confirmations_required'], row['status'], row['event_id'])

    def create_invoice(self, *, user_reference: str, asset: Asset,
                       amount: Decimal, suffix_slots: int = 1000,
                       ttl: timedelta = timedelta(minutes=30),
                       confirmations_required: int = 12) -> ExactInvoice:
        base = asset.units(amount)
        if type(suffix_slots) is not int or not 1 <= suffix_slots <= 1_000_000:
            raise ValueError('suffix_slots must be 1..1000000')
        if type(confirmations_required) is not int or confirmations_required < 1:
            raise ValueError('confirmations_required must be positive')
        if ttl.total_seconds() < 1 or not user_reference:
            raise ValueError('positive TTL and user_reference required')
        now = int(datetime.now(timezone.utc).timestamp())
        invoice_id = secrets.token_hex(16)
        with self._transaction() as db:
            existing = db.execute('SELECT decimals FROM invoices WHERE network=? AND asset_id=? LIMIT 1',
                                  (asset.network, asset.asset_id)).fetchone()
            if existing and existing['decimals'] != asset.decimals:
                raise ValueError('asset precision mismatch')
            # BEGIN IMMEDIATE serializes competing allocators, including overlapping ranges.
            start = secrets.randbelow(suffix_slots)
            for offset in range(suffix_slots):
                units = base + 1 + (start + offset) % suffix_slots
                reserved = db.execute('SELECT 1 FROM invoices WHERE network=? AND asset_id=? AND address=? AND pay_units=?',
                    (asset.network, asset.asset_id, asset.address, str(units))).fetchone()
                # Never allocate a value already observed as an unsolicited payment either.
                seen = db.execute('SELECT 1 FROM transfers WHERE network=? AND asset_id=? AND address=? AND units=?',
                    (asset.network, asset.asset_id, asset.address, str(units))).fetchone()
                if reserved or seen:
                    continue
                db.execute('INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (invoice_id, user_reference, asset.network, asset.asset_id, asset.address,
                     asset.decimals, str(base), str(units), now, now + int(ttl.total_seconds()),
                     confirmations_required, 'pending', None))
                break
            else:
                raise AmountSpaceExhausted('choose another base amount or wallet; reservations cannot be reused safely')
        return self.get(invoice_id)

    def get(self, invoice_id: str) -> ExactInvoice:
        with self._transaction() as db:
            row = db.execute('SELECT * FROM invoices WHERE id=?', (invoice_id,)).fetchone()
            if row is None:
                raise KeyError(invoice_id)
            return self._invoice(row)

    def ingest(self, transfer: Transfer) -> ExactInvoice | None:
        if (not transfer.event_id or type(transfer.units) is not int or transfer.units <= 0
                or type(transfer.confirmations) is not int or transfer.confirmations < 0
                or type(transfer.observed_at) is not int or transfer.observed_at < 0):
            raise ValueError('invalid transfer')
        a = transfer.asset
        key = (a.network, a.asset_id, transfer.event_id)
        with self._transaction() as db:
            old = db.execute('SELECT * FROM transfers WHERE network=? AND asset_id=? AND event_id=?', key).fetchone()
            if old and (old['address'], old['units'], old['observed_at']) != (a.address, str(transfer.units), transfer.observed_at):
                raise ValueError('event identity reused with different immutable fields')
            db.execute('''INSERT INTO transfers VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(network,asset_id,event_id) DO UPDATE SET
                confirmations=excluded.confirmations, removed=excluded.removed''',
                (*key, a.address, str(transfer.units), transfer.observed_at, transfer.confirmations, int(transfer.removed)))
            row = db.execute('SELECT * FROM invoices WHERE network=? AND asset_id=? AND address=? AND pay_units=?',
                (a.network, a.asset_id, a.address, str(transfer.units))).fetchone()
            if row is None:
                return None  # retained for reconciliation; never guess an invoice
            if row['decimals'] != a.decimals:
                raise ValueError('asset precision mismatch')
            # Multiple exact transfers do not add together. Select one eligible event.
            candidates = db.execute('''SELECT * FROM transfers WHERE network=? AND asset_id=?
                AND address=? AND units=? AND removed=0 AND observed_at>=? AND observed_at<?
                ORDER BY confirmations DESC, event_id''',
                (a.network, a.asset_id, a.address, str(transfer.units), row['created_at'], row['expires_at'])).fetchall()
            now = int(datetime.now(timezone.utc).timestamp())
            if candidates:
                best = candidates[0]
                status = 'confirmed' if best['confirmations'] >= row['confirmations_required'] else 'confirming'
                event_id = best['event_id']
            else:
                status = 'expired' if now >= row['expires_at'] else 'pending'
                if row['status'] in ('confirmed', 'reorged'):
                    status = 'reorged'
                event_id = None
            if status != row['status']:
                db.execute('INSERT INTO changes(invoice_id,status,created_at) VALUES (?,?,?)', (row['id'], status, now))
            db.execute('UPDATE invoices SET status=?, event_id=? WHERE id=?', (status, event_id, row['id']))
            return self._invoice(db.execute('SELECT * FROM invoices WHERE id=?', (row['id'],)).fetchone())

    def expire(self) -> int:
        now = int(datetime.now(timezone.utc).timestamp())
        with self._transaction() as db:
            rows = db.execute("SELECT id FROM invoices WHERE status='pending' AND expires_at<=?", (now,)).fetchall()
            for row in rows:
                db.execute("UPDATE invoices SET status='expired' WHERE id=?", (row['id'],))
                db.execute('INSERT INTO changes(invoice_id,status,created_at) VALUES (?,?,?)', (row['id'], 'expired', now))
            return len(rows)

    def changes(self, after: int = 0, limit: int = 100) -> list[dict]:
        """Durable notification feed; consumer persists sequence after processing.

        Delivery is at least once; consumers use sequence as their idempotency key.
        """
        if not 1 <= limit <= 10000:
            raise ValueError('limit must be 1..10000')
        with self._transaction() as db:
            return [dict(row) for row in db.execute('SELECT * FROM changes WHERE sequence>? ORDER BY sequence LIMIT ?', (after, limit))]
