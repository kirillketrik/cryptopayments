"""Persistent, leased, at-least-once webhook delivery for exact payments."""
from __future__ import annotations

from dataclasses import asdict
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
import random
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from cryptopayments.exact import ExactPaymentService


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(url, body, headers, timeout):
    """Return status and Retry-After; never forward signed bodies on redirects."""
    request = Request(url, data=body, headers=headers, method='POST')
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, response.headers.get('Retry-After')
    except HTTPError as error:
        try:
            return error.code, error.headers.get('Retry-After')
        finally:
            error.close()


def signature(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), str(timestamp).encode() + b'.' + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, supplied: str,
                     *, now: int | None = None, tolerance: int = 300) -> bool:
    """Verify against the raw request body; receiver must also deduplicate event_id."""
    if not isinstance(supplied, str) or len(supplied) != 64 or any(c not in "0123456789abcdef" for c in supplied):
        return False
    try:
        stamp = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs((int(time.time()) if now is None else now) - stamp) > tolerance:
        return False
    return hmac.compare_digest(signature(secret, stamp, body), supplied)


class WebhookWorker:
    """One durable subscription per name. URL changes require a new name.

    Supply a stable secret from environment or a secret manager on every restart.
    Secrets are not stored in SQLite. Endpoint URLs must be operator-controlled.
    Each call to run_once performs at most one network delivery outside the DB lock.
    """
    def __init__(self, service: ExactPaymentService, *, name: str, url: str,
                 secret: str, max_attempts: int = 8, timeout: float = 10,
                 base_delay: float = 5, max_delay: float = 3600,
                 transport=send, clock=time.time, jitter=random.uniform):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or not name):
            raise ValueError('named subscription with an HTTPS URL required')
        if len(secret.encode()) < 32:
            raise ValueError('webhook signing secret must be at least 32 bytes')
        if max_attempts < 1 or timeout <= 0 or base_delay <= 0 or max_delay < base_delay:
            raise ValueError('invalid retry settings')
        self.service, self.name, self.url, self.secret = service, name, url, secret
        self.max_attempts, self.timeout = max_attempts, timeout
        self.base_delay, self.max_delay = base_delay, max_delay
        self.transport, self.clock, self.jitter = transport, clock, jitter
        with service._transaction() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                name TEXT PRIMARY KEY, url TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                subscription TEXT NOT NULL, sequence INTEGER NOT NULL, body BLOB NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                token TEXT, http_status INTEGER,
                PRIMARY KEY(subscription, sequence));
            CREATE INDEX IF NOT EXISTS webhook_due ON webhook_deliveries(subscription,state,next_at);
            ''')
            db.execute('INSERT OR IGNORE INTO webhook_subscriptions(name,url) VALUES (?,?)', (name, url))
            if db.execute('SELECT url FROM webhook_subscriptions WHERE name=?', (name,)).fetchone()['url'] != url:
                raise ValueError('subscription URL changed; use a new subscription name')

    def enqueue(self, limit: int = 100) -> int:
        """Atomically snapshot events and advance cursor; includes historic events."""
        with self.service._transaction() as db:
            cursor = db.execute('SELECT cursor FROM webhook_subscriptions WHERE name=?', (self.name,)).fetchone()['cursor']
            changes = db.execute('SELECT * FROM changes WHERE sequence>? ORDER BY sequence LIMIT ?', (cursor, limit)).fetchall()
            for change in changes:
                invoice = self.service._invoice(db.execute('SELECT * FROM invoices WHERE id=?', (change['invoice_id'],)).fetchone())
                # Event state comes from the immutable journal, never the latest invoice state.
                payload = {'event_id': str(change['sequence']), 'type': 'invoice.status_changed',
                    'created_at': change['created_at'], 'invoice': {
                        'id': invoice.id, 'user_reference': invoice.user_reference,
                        'asset': asdict(invoice.asset), 'payment_amount': str(invoice.payment_amount),
                        'status': change['status']}}
                body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
                db.execute('INSERT OR IGNORE INTO webhook_deliveries(subscription,sequence,body) VALUES (?,?,?)',
                           (self.name, change['sequence'], body))
            if changes:
                db.execute('UPDATE webhook_subscriptions SET cursor=? WHERE name=?', (changes[-1]['sequence'], self.name))
            return len(changes)

    def run_once(self) -> bool:
        self.enqueue()
        now, token = self.clock(), secrets.token_hex(16)
        with self.service._transaction() as db:
            # A crashed worker consumes an attempt. Exhausted leases become dead letters.
            db.execute("UPDATE webhook_deliveries SET state='dead' WHERE subscription=? AND state='pending' AND attempts>=? AND lease_until<=?",
                       (self.name, self.max_attempts, now))
            row = db.execute("""SELECT * FROM webhook_deliveries WHERE subscription=? AND state='pending'
                AND attempts<? AND next_at<=? AND lease_until<=? ORDER BY sequence LIMIT 1""",
                (self.name, self.max_attempts, now, now)).fetchone()
            if row is None:
                return False
            attempt = row['attempts'] + 1
            db.execute('UPDATE webhook_deliveries SET token=?, attempts=?, lease_until=? WHERE subscription=? AND sequence=?',
                (token, attempt, now + self.timeout + 60, self.name, row['sequence']))
        body, timestamp = bytes(row['body']), int(now)
        headers = {'Content-Type': 'application/json', 'User-Agent': 'cryptopayments-webhooks/1',
                   'X-Cryptopayments-Event-Id': str(row['sequence']),
                   'X-Cryptopayments-Timestamp': str(timestamp),
                   'X-Cryptopayments-Signature': signature(self.secret, timestamp, body)}
        retry_after = None
        try:
            status, retry_after = self.transport(self.url, body, headers, self.timeout)
        except (OSError, URLError, TimeoutError):
            status = None
        except Exception:
            # An unexpected transport failure must leave the lease recoverable.
            raise
        success = status is not None and 200 <= status < 300
        retryable = status is None or status in (408, 425, 429) or status >= 500
        state = 'delivered' if success else ('pending' if retryable and attempt < self.max_attempts else 'dead')
        delay = min(self.max_delay, self.base_delay * 2**min(attempt - 1, 30))
        delay = self.jitter(delay / 2, delay)
        if retry_after:
            try:
                requested = float(retry_after)
            except ValueError:
                try:
                    requested = parsedate_to_datetime(retry_after).timestamp() - self.clock()
                except (ValueError, TypeError, OverflowError):
                    requested = 0
            delay = max(delay, min(self.max_delay, max(0, requested)))
        with self.service._transaction() as db:
            db.execute('''UPDATE webhook_deliveries SET state=?, next_at=?, lease_until=0,
                http_status=?, token=NULL WHERE subscription=? AND sequence=? AND token=?''',
                (state, self.clock() + delay, status, self.name, row['sequence'], token))
        return True

    def deliveries(self) -> list[dict]:
        with self.service._transaction() as db:
            return [dict(row) for row in db.execute('''SELECT sequence,state,attempts,next_at,http_status
                FROM webhook_deliveries WHERE subscription=? ORDER BY sequence''', (self.name,))]

    def replay(self, sequence: int) -> bool:
        """Explicitly retry a dead letter, retaining the same payload and event ID."""
        with self.service._transaction() as db:
            return db.execute("""UPDATE webhook_deliveries SET state='pending', attempts=0,
                next_at=0, lease_until=0, token=NULL WHERE subscription=? AND sequence=? AND state='dead'""",
                (self.name, sequence)).rowcount == 1
