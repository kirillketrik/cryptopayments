"""Read-only HTTP adapters. No private keys or transaction broadcasting.

Pollers intentionally scan explicit ranges/pages: the caller persists its cursor
only after ingesting the entire returned batch. Rescan overlapping ranges and
refresh tracked EVM receipts to detect reorganizations. TLS verification is on.
"""
from __future__ import annotations

import json
import hashlib
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from cryptopayments.exact import Asset, Transfer

ERC20_USDT = '0xdac17f958d2ee523a2206206994597c13d831ec7'
TRC20_USDT = 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


def http_json(url: str, payload: dict | None = None, headers: dict | None = None):
    if urlparse(url).scheme != 'https':
        raise ValueError('HTTPS endpoint required')
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=body, headers={'Content-Type': 'application/json', **(headers or {})})
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    return result


class EthereumUSDT:
    """Ethereum mainnet USDT. Validates chain ID; confirmations from block height.

    Recipient must be lower-case 0x address. poll() includes unfinalized logs;
    refresh() must be called for tracked events on each polling cycle.
    """
    def __init__(self, rpc_url: str, address: str, *, transport=http_json):
        if len(address) != 42 or not address.startswith('0x'):
            raise ValueError('20-byte EVM recipient required')
        int(address[2:], 16)
        self.asset = Asset('ethereum:1', ERC20_USDT, address.lower(), 6)
        self.url, self.transport = rpc_url, transport
        if int(self._rpc('eth_chainId', []), 16) != 1:
            raise ValueError('expected Ethereum mainnet')

    def _rpc(self, method, params):
        result = self.transport(self.url, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        if 'error' in result:
            raise RuntimeError(f'RPC {method} failed: {result["error"]}')
        return result['result']

    def _transfer(self, log, head, timestamp):
        if log['address'].lower() != self.asset.asset_id or log['topics'][0].lower() != TRANSFER_TOPIC:
            raise ValueError('unexpected token log')
        if '0x' + log['topics'][2][-40:].lower() != self.asset.address:
            raise ValueError('unexpected recipient')
        return Transfer(f'{log["transactionHash"].lower()}:{int(log["logIndex"],16)}', self.asset,
                        int(log['data'], 16), timestamp,
                        max(0, head - int(log['blockNumber'], 16) + 1), log.get('removed', False))

    def poll(self, from_block: int, to_block: int) -> list[Transfer]:
        if not 0 <= from_block <= to_block:
            raise ValueError('invalid block range')
        head = int(self._rpc('eth_blockNumber', []), 16)
        if to_block > head:
            raise ValueError('range exceeds chain head')
        logs = self._rpc('eth_getLogs', [{'address': self.asset.asset_id,
            'topics': [TRANSFER_TOPIC, None, '0x' + self.asset.address[2:].zfill(64)],
            'fromBlock': hex(from_block), 'toBlock': hex(to_block)}])
        blocks = {}
        result = []
        for log in logs:
            number = log['blockNumber']
            if number not in blocks:
                blocks[number] = self._rpc('eth_getBlockByNumber', [number, False])
            block = blocks[number]
            if block['hash'].lower() != log['blockHash'].lower():
                raise RuntimeError('chain changed while scanning; retry range')
            transfer = self._transfer(log, head, int(block['timestamp'], 16))
            if transfer.units > 0:
                result.append(transfer)
        return result

    def refresh(self, previous: Transfer) -> Transfer:
        if previous.asset != self.asset:
            raise ValueError('wrong asset')
        tx_hash, index = previous.event_id.rsplit(':', 1)
        receipt = self._rpc('eth_getTransactionReceipt', [tx_hash])
        if receipt and int(receipt.get('status', '0x0'), 16) == 1:
            for log in receipt['logs']:
                if int(log['logIndex'], 16) == int(index):
                    head = int(self._rpc('eth_blockNumber', []), 16)
                    block = self._rpc('eth_getBlockByNumber', [log['blockNumber'], False])
                    if block['hash'].lower() != log['blockHash'].lower():
                        raise RuntimeError('chain changed while refreshing')
                    return self._transfer(log, head, int(block['timestamp'], 16))
        return Transfer(previous.event_id, previous.asset, previous.units, previous.observed_at, 0, True)


def tron_address(value: str) -> str:
    """Normalize decoded event hex addresses to Base58Check."""
    if value.startswith('T'):
        return value
    raw = value.removeprefix('0x')
    if len(raw) == 40:
        raw = '41' + raw
    if len(raw) != 42 or not raw.startswith('41'):
        raise ValueError('invalid Tron address')
    data = bytes.fromhex(raw)
    checked = data + hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
    n = int.from_bytes(checked, 'big')
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    result = ''
    while n:
        n, digit = divmod(n, 58)
        result = alphabet[digit] + result
    return result


class TronUSDT:
    """TronGrid mainnet account TRC20 feed, solidified transactions only.

    Use invoices with confirmations_required=1: '1' denotes the provider's
    solidified finality, not a fabricated block depth. Follow every next link.
    Each discovered transaction is expanded into indexed Transfer events so
    multiple transfers in one transaction remain independently identifiable.
    """
    def __init__(self, address: str, api_key: str | None = None, *, transport=http_json):
        if not address.startswith('T') or len(address) != 34:
            raise ValueError('Tron base58 recipient required')
        self.asset = Asset('tron:mainnet', TRC20_USDT, address, 6)
        self.transport = transport
        self.headers = {'TRON-PRO-API-KEY': api_key} if api_key else {}

    def poll(self, min_timestamp_ms: int, fingerprint: str | None = None):
        params = {'only_confirmed': 'true', 'only_to': 'true', 'contract_address': TRC20_USDT,
                  'min_timestamp': min_timestamp_ms, 'limit': 200, 'order_by': 'block_timestamp,asc'}
        if fingerprint:
            params['fingerprint'] = fingerprint
        response = self.transport('https://api.trongrid.io/v1/accounts/' + self.asset.address + '/transactions/trc20?' + urlencode(params), headers=self.headers)
        if response.get('success') is not True:
            raise RuntimeError('TronGrid request failed')
        transfers = []
        seen = set()
        for item in response['data']:
            tx_id = item['transaction_id']
            if tx_id in seen:
                continue
            seen.add(tx_id)
            events = self.transport('https://api.trongrid.io/v1/transactions/' + tx_id + '/events?only_confirmed=true&limit=200', headers=self.headers)
            if events.get('success') is not True or events.get('meta', {}).get('links', {}).get('next'):
                raise RuntimeError('incomplete Tron event response; do not advance cursor')
            for event in events['data']:
                if event['contract_address'] != TRC20_USDT or event['event_name'] != 'Transfer':
                    continue
                # TronGrid decoded addresses may be hex; refuse ambiguous encoding.
                recipient = tron_address(event['result']['to'])
                if recipient != self.asset.address:
                    continue
                units = int(event['result']['value'])
                if units > 0:
                    transfers.append(Transfer(f"{tx_id}:{event['event_index']}", self.asset, units,
                                              int(event['block_timestamp']) // 1000, 1))
        return transfers, response.get('meta', {}).get('fingerprint') if response.get('meta', {}).get('links', {}).get('next') else None


class TON:
    """Toncenter v3 mainnet, finalized native inbound TON (not jettons).

    Provide a canonical raw workchain:hex address and confirmations_required=1.
    Pagination uses offset within a fixed start/end timestamp window. The adapter
    credits only successful, non-bounced internal messages, excluding fees.
    """
    def __init__(self, address: str, api_key: str | None = None, *, transport=http_json):
        workchain, account = address.split(':')
        if workchain not in ('0', '-1') or len(account) != 64:
            raise ValueError('canonical raw TON address required')
        int(account, 16)
        self.asset = Asset('ton:mainnet', 'native', address.lower(), 9)
        self.transport = transport
        self.headers = {'X-API-Key': api_key} if api_key else {}

    def poll(self, start_time: int, end_time: int, offset: int = 0):
        if not 0 <= start_time <= end_time or offset < 0:
            raise ValueError('invalid scan window')
        params = {'account': self.asset.address, 'start_utime': start_time,
                  'end_utime': end_time, 'limit': 100, 'offset': offset, 'sort': 'asc'}
        response = self.transport('https://toncenter.com/api/v3/transactions?' + urlencode(params), headers=self.headers)
        transfers = []
        items = response['transactions']
        for item in items:
            message = item.get('in_msg')
            if (item['account'].lower() != self.asset.address or item['description'].get('aborted', True)
                    or not message or not message.get('source') or message.get('bounced', False)
                    or message.get('destination', '').lower() != self.asset.address):
                continue
            # Fail closed for transactions without a masterchain reference.
            if item.get('mc_block_seqno') is None:
                continue
            units = int(message.get('value') or 0)
            if units > 0:
                transfers.append(Transfer(item['hash'], self.asset, units, int(item['now']), 1))
        return transfers, offset + len(items) if len(items) == 100 else None
