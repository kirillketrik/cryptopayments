from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from cryptopayments import Asset, ExactPaymentService, Transfer, AmountSpaceExhausted


@pytest.fixture
def service(tmp_path):
    return ExactPaymentService(tmp_path / 'payments.sqlite')

A = Asset('ethereum:1', 'usdt', 'wallet', 6)


def invoice(service, **kw):
    return service.create_invoice(user_reference='order', asset=A, amount=Decimal('10'), **kw)


def transfer(i, **kw):
    return replace(Transfer('tx:0', i.asset, i.pay_units, i.created_at, 12), **kw)


def test_payment_restart_notifications_and_duplicates(service):
    i = invoice(service)
    assert Decimal('10') < i.payment_amount <= Decimal('10.001')
    assert service.ingest(transfer(i, confirmations=1)).status == 'confirming'
    assert service.ingest(transfer(i)).status == 'confirmed'
    assert service.ingest(transfer(i)).status == 'confirmed'
    assert len(service.changes()) == 2
    restarted = ExactPaymentService(service.database)
    assert restarted.get(i.id).status == 'confirmed'
    assert len(restarted.changes(after=1)) == 1


def test_concurrent_allocation_and_exhaustion(service):
    with ThreadPoolExecutor(max_workers=8) as pool:
        invoices = list(pool.map(lambda _: invoice(service, suffix_slots=20), range(20)))
    assert len({i.pay_units for i in invoices}) == 20
    with pytest.raises(AmountSpaceExhausted):
        invoice(service, suffix_slots=20)


def test_no_reuse_after_paid(service):
    i = invoice(service, suffix_slots=1)
    service.ingest(transfer(i))
    with pytest.raises(AmountSpaceExhausted):
        invoice(service, suffix_slots=1)


def test_wrong_asset_address_amount_and_time(service):
    i = invoice(service)
    for t in (transfer(i, event_id='wrong-amount', units=1), transfer(i, asset=replace(A, address='other')),
              transfer(i, asset=replace(A, asset_id='fake-usdt'))):
        assert service.ingest(t) is None
    assert service.ingest(transfer(i, event_id='late', observed_at=i.expires_at)).status == 'pending'
    assert service.get(i.id).event_id is None


def test_reorg_and_recovery(service):
    i = invoice(service)
    service.ingest(transfer(i))
    assert service.ingest(transfer(i, removed=True)).status == 'reorged'
    assert service.ingest(transfer(i)).status == 'confirmed'
    assert service.ingest(transfer(i, confirmations=0)).status == 'confirming'


def test_event_mutation_rejected(service):
    i = invoice(service)
    service.ingest(transfer(i))
    with pytest.raises(ValueError):
        service.ingest(transfer(i, units=i.pay_units+1))


def test_precision_and_invalid_inputs(service):
    for amount in (Decimal('NaN'), Decimal('Infinity'), Decimal('-1'), Decimal('0'), Decimal('0.0000001'), 1.5):
        with pytest.raises(ValueError):
            service.create_invoice(user_reference='x', asset=A, amount=amount)
    with localcontext() as context:
        context.prec = 2
        assert A.units(Decimal('123456789.123456')) == 123456789123456
        assert A.amount(123456789123456) == Decimal('123456789.123456')
    with pytest.raises(ValueError):
        invoice(service, ttl=timedelta(seconds=0))


def test_two_transfers_not_aggregated(service):
    i = invoice(service)
    assert service.ingest(transfer(i, units=i.pay_units//2)) is None
    assert service.ingest(transfer(i, event_id='tx:1', units=i.pay_units-i.pay_units//2)) is None
    assert service.get(i.id).status == 'pending'


def test_unsolicited_amount_not_allocated(service):
    service.ingest(Transfer('old', A, 10000001, 1, 12))
    with pytest.raises(AmountSpaceExhausted):
        invoice(service, suffix_slots=1)


def test_expiry_and_delayed_indexing(service):
    i = invoice(service)
    with service._transaction() as db:
        db.execute('UPDATE invoices SET expires_at=? WHERE id=?', (i.created_at, i.id))
    assert service.expire() == 1
    assert service.get(i.id).status == 'expired'
    assert service.expire() == 0
    assert service.changes()[0]['status'] == 'expired'


def test_precision_mismatch_and_missing(service):
    invoice(service)
    with pytest.raises(ValueError):
        service.create_invoice(user_reference='x', asset=replace(A, decimals=9), amount=Decimal('1'))
    with pytest.raises(KeyError):
        service.get('missing')


def test_cli(tmp_path):
    import json
    import subprocess
    import sys
    cmd = [sys.executable, '-m', 'cryptopayments', '--database', str(tmp_path/'cli.sqlite')]
    created = json.loads(subprocess.check_output(cmd + ['create', '--network', 'ton', '--address',
        '0:'+'ab'*32, '--amount', '1', '--reference', 'order']))
    fetched = json.loads(subprocess.check_output(cmd + ['get', created['id']]))
    assert fetched['payment_amount'] == created['payment_amount']
    assert subprocess.check_output(cmd + ['expire']).strip() == b'0'
    assert json.loads(subprocess.check_output(cmd + ['changes'])) == []
