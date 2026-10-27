"""python -m cryptopayments: local invoice and reconciliation CLI."""
import argparse
from dataclasses import asdict
from decimal import Decimal
import json
import os

from cryptopayments import Asset, ExactPaymentService
from cryptopayments.adapters import EthereumUSDT, TronUSDT, TON


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--network', choices=['ethereum', 'tron', 'ton'], required=True)
    create.add_argument('--address', required=True)
    create.add_argument('--amount', required=True)
    create.add_argument('--reference', required=True)
    create.add_argument('--slots', type=int, default=1000)
    show = sub.add_parser('get')
    show.add_argument('id')
    sub.add_parser('expire')
    changes = sub.add_parser('changes')
    changes.add_argument('--after', type=int, default=0)
    webhook = sub.add_parser('webhooks')
    webhook.add_argument('--name', default='default')
    webhook.add_argument('--url-env', default='WEBHOOK_URL')
    webhook.add_argument('--secret-env', default='WEBHOOK_SECRET')
    webhook.add_argument('--once', action='store_true')
    webhook.add_argument('--list', action='store_true')
    webhook.add_argument('--replay', type=int)
    webhook.add_argument('--max-attempts', type=int, default=8)
    for network in ('ethereum', 'tron', 'ton'):
        scan = sub.add_parser('scan-' + network)
        scan.add_argument('--address', required=True)
        scan.add_argument('--start', type=int, required=True)
        scan.add_argument('--end', type=int, required=network != 'tron')
        if network == 'ethereum':
            scan.add_argument('--rpc-env', default='ETHEREUM_RPC_URL')
    args = parser.parse_args()
    service = ExactPaymentService(args.database)
    if args.command == 'webhooks':
        import signal
        import threading
        from cryptopayments.webhooks import WebhookWorker
        worker = WebhookWorker(service, name=args.name, url=os.environ[args.url_env],
                               secret=os.environ[args.secret_env], max_attempts=args.max_attempts)
        if args.list:
            print(json.dumps(worker.deliveries()))
            return
        if args.replay is not None:
            print(json.dumps({'replayed': worker.replay(args.replay)}))
            return
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        while not stopped.is_set():
            worked = worker.run_once()
            if args.once:
                return
            if not worked:
                stopped.wait(1)
        return
    if args.command == 'create':
        from cryptopayments.adapters import ERC20_USDT, TRC20_USDT
        config = {'ethereum': ('ethereum:1', ERC20_USDT, 6, 12),
                  'tron': ('tron:mainnet', TRC20_USDT, 6, 1),
                  'ton': ('ton:mainnet', 'native', 9, 1)}[args.network]
        address = args.address.lower() if args.network in ('ethereum', 'ton') else args.address
        result = service.create_invoice(user_reference=args.reference,
            asset=Asset(config[0], config[1], address, config[2]),
            amount=Decimal(args.amount), suffix_slots=args.slots, confirmations_required=config[3])
        print(json.dumps({**asdict(result), 'payment_amount': str(result.payment_amount)}))
    elif args.command == 'get':
        result = service.get(args.id)
        print(json.dumps({**asdict(result), 'payment_amount': str(result.payment_amount)}))
    elif args.command == 'expire':
        print(service.expire())
    elif args.command == 'changes':
        print(json.dumps(service.changes(args.after)))
    else:
        if args.command == 'scan-ethereum':
            adapter = EthereumUSDT(os.environ[args.rpc_env], args.address)
            batches = [adapter.poll(args.start, args.end)]
            # Refresh previously observed events, including removed ones, before scanning.
            from cryptopayments import Transfer
            with service._transaction() as db:
                rows = db.execute('SELECT * FROM transfers WHERE network=? AND asset_id=? AND address=?',
                    (adapter.asset.network, adapter.asset.asset_id, adapter.asset.address)).fetchall()
            for row in rows:
                service.ingest(adapter.refresh(Transfer(row['event_id'], adapter.asset, int(row['units']),
                    row['observed_at'], row['confirmations'], bool(row['removed']))))
            for batch in batches:
                for transfer in batch:
                    service.ingest(transfer)
        else:
            adapter = (TronUSDT(args.address, os.environ.get('TRONGRID_API_KEY'))
                       if args.command == 'scan-tron' else TON(args.address, os.environ.get('TONCENTER_API_KEY')))
            cursor = None
            while True:
                batch, cursor = (adapter.poll(args.start, cursor) if args.command == 'scan-tron'
                                 else adapter.poll(args.start, args.end, cursor or 0))
                for transfer in batch:
                    service.ingest(transfer)
                if cursor is None:
                    break
        service.expire()
        print('Scan completed. Persist the scanned range only after this command succeeds.')


if __name__ == '__main__':
    main()
