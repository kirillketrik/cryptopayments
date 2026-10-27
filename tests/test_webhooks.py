from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from urllib.error import URLError

import pytest

from cryptopayments import Asset, ExactPaymentService, Transfer, WebhookWorker, verify_signature
from cryptopayments.webhooks import signature

SECRET = 'test-signing-secret-' * 3


@pytest.fixture
def setup(tmp_path):
    service = ExactPaymentService(tmp_path / 'payments.sqlite')
    invoice = service.create_invoice(user_reference='order', asset=Asset('test', 'native', 'wallet', 6), amount=Decimal('10'))
    service.ingest(Transfer('tx', invoice.asset, invoice.pay_units, invoice.created_at, 12))
    return service, invoice


def worker(service, transport, **kw):
    return WebhookWorker(service, name='orders', url='https://merchant.example/hook',
                         secret=SECRET, transport=transport, **kw)


def test_signed_delivery_and_restart(setup):
    service, invoice = setup
    received = []
    def send(url, body, headers, timeout):
        received.append(body)
        assert verify_signature(SECRET, headers['X-Cryptopayments-Timestamp'], body,
                                headers['X-Cryptopayments-Signature'], now=1000)
        assert headers['X-Cryptopayments-Event-Id'] == '1'
        assert json.loads(body)['invoice']['id'] == invoice.id
        return 204, None
    first = worker(service, send, clock=lambda: 1000)
    assert first.run_once()
    assert first.deliveries()[0]['state'] == 'delivered'
    restarted = worker(ExactPaymentService(service.database), send, clock=lambda: 1001)
    assert not restarted.run_once()
    assert len(received) == 1


def test_retry_after_and_eventual_success(setup):
    service, _ = setup
    now = [1000]
    calls = []
    def send(url, body, headers, timeout):
        calls.append((body, headers))
        return (429, '120') if len(calls) == 1 else (200, None)
    w = worker(service, send, clock=lambda: now[0], jitter=lambda low, high: high)
    assert w.run_once()
    assert w.deliveries()[0]['next_at'] == 1120
    assert not w.run_once()
    now[0] = 1120
    assert w.run_once()
    assert calls[0][0] == calls[1][0]
    assert calls[0][1]['X-Cryptopayments-Event-Id'] == calls[1][1]['X-Cryptopayments-Event-Id']
    assert calls[0][1]['X-Cryptopayments-Signature'] != calls[1][1]['X-Cryptopayments-Signature']


@pytest.mark.parametrize('status', [301, 400, 401, 404, 410])
def test_permanent_failures_and_manual_replay(setup, status):
    service, _ = setup
    w = worker(service, lambda *args: (status, None))
    assert w.run_once()
    assert w.deliveries()[0]['state'] == 'dead'
    assert not w.run_once()
    assert w.replay(1)
    assert not w.replay(1)
    w.transport = lambda *args: (200, None)
    assert w.run_once()
    assert w.deliveries()[0]['state'] == 'delivered'


def test_network_retry_exhaustion(setup):
    service, _ = setup
    def fail(*args):
        raise URLError('offline')
    now = [1000]
    w = worker(service, fail, max_attempts=2, clock=lambda: now[0])
    assert w.run_once()
    assert w.deliveries()[0]['state'] == 'pending'
    now[0] += 3601
    assert w.run_once()
    assert w.deliveries()[0]['state'] == 'dead'
    assert w.deliveries()[0]['attempts'] == 2


def test_crashed_lease_is_recovered(setup):
    service, _ = setup
    now = [1000]
    def crash(*args):
        raise RuntimeError('process crash')
    w = worker(service, crash, clock=lambda: now[0])
    with pytest.raises(RuntimeError):
        w.run_once()
    w.transport = lambda *args: (200, None)
    assert not w.run_once()
    now[0] += 71
    assert w.run_once()
    assert w.deliveries()[0]['attempts'] == 2


def test_parallel_workers_claim_once(setup):
    service, _ = setup
    calls = []
    def send(*args):
        calls.append(1)
        return 200, None
    workers = [worker(service, send) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda w: w.run_once(), workers))
    assert sum(results) == 1 and len(calls) == 1


def test_event_payload_uses_historical_status(setup):
    service, invoice = setup
    service.ingest(Transfer('tx', invoice.asset, invoice.pay_units, invoice.created_at, 0, True))
    seen = []
    def send(url, body, headers, timeout):
        seen.append(json.loads(body)['invoice']['status'])
        return 200, None
    w = worker(service, send)
    assert w.run_once() and w.run_once()
    assert seen == ['confirmed', 'reorged']


def test_validation_and_signature_replay_window(setup):
    service, _ = setup
    with pytest.raises(ValueError):
        WebhookWorker(service, name='x', url='http://merchant.example', secret=SECRET)
    with pytest.raises(ValueError):
        worker(service, lambda *args: (200, None), max_attempts=0)
    w = worker(service, lambda *args: (200, None))
    with pytest.raises(ValueError):
        WebhookWorker(service, name=w.name, url='https://other.example', secret=SECRET)
    sig = signature(SECRET, 1000, b'body')
    assert verify_signature(SECRET, '1000', b'body', sig, now=1001)
    assert not verify_signature(SECRET, '1000', b'tampered', sig, now=1001)
    assert not verify_signature(SECRET, '1000', b'body', sig, now=2000)
    assert not verify_signature(SECRET, 'invalid', b'body', sig)


def test_real_https_delivery_with_trusted_test_certificate(setup, tmp_path, monkeypatch):
    import http.server
    import ssl
    import subprocess
    import threading
    from cryptopayments.webhooks import send
    key, cert = tmp_path / 'key.pem', tmp_path / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost',
        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
    monkeypatch.setenv('SSL_CERT_FILE', str(cert))
    received = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            received.append((body, dict(self.headers)))
            self.send_response(503 if len(received) == 1 else 204)
            self.send_header('Retry-After', '1')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        now = [1000]
        w = WebhookWorker(setup[0], name='https', url=f'https://localhost:{server.server_port}/hook',
                          secret=SECRET, clock=lambda: now[0], transport=send)
        assert w.run_once()
        assert w.deliveries()[0]['state'] == 'pending'
        now[0] += 10
        assert w.run_once()
        assert w.deliveries()[0]['state'] == 'delivered'
        assert received[0][0] == received[1][0]
        body, headers = received[1]
        assert verify_signature(SECRET, headers['X-Cryptopayments-Timestamp'], body,
                                headers['X-Cryptopayments-Signature'], now=1010)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('supplied', [None, '', 'é'*64, 'z'*64])
def test_malformed_signatures_are_rejected(supplied):
    assert not verify_signature(SECRET, '1000', b'body', supplied, now=1000)
