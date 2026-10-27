from cryptopayments.adapters import EthereumUSDT, TronUSDT, TON, ERC20_USDT, TRC20_USDT, TRANSFER_TOPIC, tron_address


def test_ethereum_logs_and_removal():
    address = '0x' + '12'*20
    log = {'address': ERC20_USDT, 'topics': [TRANSFER_TOPIC, '0x'+'0'*64, '0x'+address[2:].zfill(64)],
           'transactionHash': '0xabc', 'logIndex': '0x2', 'blockNumber': '0xa', 'blockHash': '0xbeef', 'data': hex(10000001)}
    def rpc(url, data):
        return {'result': {'eth_chainId': '0x1', 'eth_blockNumber': '0x15', 'eth_getLogs': [log],
            'eth_getBlockByNumber': {'timestamp': '0x64', 'hash': '0xbeef'},
            'eth_getTransactionReceipt': None}[data['method']]}
    adapter = EthereumUSDT('https://rpc.example', address, transport=rpc)
    t = adapter.poll(10, 21)[0]
    assert (t.event_id, t.units, t.confirmations, t.observed_at) == ('0xabc:2', 10000001, 12, 100)
    assert adapter.refresh(t).removed


def test_tron_indexed_events_and_pagination():
    address = TRC20_USDT
    def api(url, headers):
        if '/events?' in url:
            return {'success': True, 'data': [{'contract_address': TRC20_USDT, 'event_name': 'Transfer',
                'event_index': n, 'block_timestamp': 100000, 'result': {'to': address, 'value': '10000001'}} for n in (1,2)]}
        return {'success': True, 'data': [{'transaction_id': 'tx'}], 'meta': {'fingerprint': 'page2', 'links': {'next': 'url'}}}
    batch, cursor = TronUSDT(address, transport=api).poll(0)
    assert cursor == 'page2'
    assert [t.event_id for t in batch] == ['tx:1', 'tx:2']
    assert tron_address('41ea51342dabbb928ae1e576bd39eff8aaf070a8c6') == 'TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj'


def test_ton_rejects_bounces_aborts_and_nonfinalized():
    address = '0:' + 'ab'*32
    def item(**kw):
        return dict(account=address, description={'aborted': False}, in_msg={'source': 'source',
            'destination': address, 'value': '1000000001', 'bounced': False}, hash='hash', now=100, mc_block_seqno=10, **kw)
    good = item()
    aborted = {**good, 'description': {'aborted': True}}
    bounced = {**good, 'in_msg': {**good['in_msg'], 'bounced': True}}
    unfinalized = {**good, 'mc_block_seqno': None}
    adapter = TON(address, transport=lambda url, headers: {'transactions': [good, aborted, bounced, unfinalized]})
    batch, cursor = adapter.poll(0, 200)
    assert len(batch) == 1 and cursor is None
    assert batch[0].units == 1000000001
