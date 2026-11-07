import io
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from backend import miaoxiang as mx
from backend.models import ProviderQuery
from backend.tests.test_api import client, account


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    monkeypatch.setattr(mx, 'api_key', lambda: 'test-only-credential')
    mx.attempts.clear()


def data_payload(count=2):
    return {'status': 0, 'data': {'status': 0, 'data': {'searchDataResultDTO': {
        'questionId': 'query-123', 'condition': {'query': ['指定股票', '收盘价']},
        'entityTagDTOList': [{'fullName': '指定股票', 'secuCode': '300059', 'entityTypeName': 'A股'}],
        'dataTableDTOList': [{'code': '300059.SZ', 'title': '指定股票收盘价', 'dataType': '日线',
            'table': {'headName': [f'日期{i}' for i in range(count)], 'price': ['18.78元'] * count},
            'nameMap': {'price': '收盘价'}, 'indicatorOrder': ['price'],
            'fieldParameterStr': '不复权；币种人民币'}]}}}}


def test_data_adapter_preserves_units_dates_bounds_and_literal_html():
    payload = data_payload(55)
    payload['data']['data']['searchDataResultDTO']['dataTableDTOList'][0]['table']['price'][0] = '<script>alert(1)</script>'
    result = mx.normalize(payload, 'data')
    assert result['truncated']
    table = result['tables'][0]
    assert table['entity'] == '300059.SZ' and table['total_rows'] == 55 and len(table['rows']) == 50
    assert table['rows'][0] == ['日期0', '<script>alert(1)</script>']
    assert table['rows'][1][1] == '18.78元' and table['columns'][1]['label'] == '收盘价'
    assert result['notes'] == ['不复权；币种人民币']


def test_screen_adapter_preserves_column_dates_and_distinguishes_empty():
    columns = [{'key': 'code', 'title': '股票代码'}, {'key': 'change', 'title': '涨跌幅(%)', 'dateMsg': '2026.11.06'}]
    payload = {'status': 0, 'data': {'data': {'totalCondition': '半导体 前10', 'allResults': {
        'result': {'columns': columns, 'dataList': [{'code': '000001', 'change': '2.1'}], 'total': 80}}}}}
    data = mx.normalize(payload, 'screen')
    assert data['tables'][0]['columns'][1]['date'] == '2026.11.06'
    assert data['tables'][0]['rows'] == [['000001', '2.1']]
    assert data['truncated'] and data['conditions'] == '半导体 前10'
    payload['data']['data']['allResults']['result'].update(dataList=[], total=0)
    empty = mx.normalize(payload, 'screen')
    assert empty['tables'][0]['rows'] == [] and not empty['truncated']
    with pytest.raises(mx.ProviderError, match='没有可展示'):
        mx.normalize({'status': 0, 'data': {}}, 'data')


def test_transport_has_fixed_readonly_url_auth_no_redirect_and_redacts():
    def respond(req):
        assert str(req.url) == mx.BASE + 'query'
        assert req.headers['apikey'] == 'test-only-credential'
        payload = data_payload()
        payload['data']['data']['searchDataResultDTO']['condition'] = 'test-only-credential'
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        result = mx.fetch_result('data', '收盘价', 'test-only-credential', c)
    assert result['conditions'] == '[redacted]'
    for code, expected in [(302, 'http_failure'), (403, 'credentials'), (429, 'quota')]:
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, headers={'Location': 'https://other.invalid'}, text='secret'))) as c:
            with pytest.raises(mx.ProviderError) as exc:
                mx.fetch_result('data', '收盘价', 'test-only-credential', c)
            assert exc.value.code == expected and 'secret' not in exc.value.message
    with pytest.raises(mx.ProviderError) as exc:
        mx.normalize({'status': 0, 'data': {'code': 113}}, 'screen')
    assert exc.value.code == 'quota'


def test_query_auth_owner_cache_and_append_only_history(client, monkeypatch):
    calls = []
    def fetch(kind, query, key):
        calls.append(query)
        return mx.normalize(data_payload(), kind)
    monkeypatch.setattr(mx, 'fetch_result', fetch)
    assert client.post('/api/mx/query', json={'query': '东方财富最新价'}).status_code == 401
    alice, bob = account(client, 'mxalice'), account(client, 'mxbob')
    first = client.post('/api/mx/query', headers=alice, json={'query': '东方财富最新价'}).json()
    second = client.post('/api/mx/query', headers=alice, json={'query': '东方财富最新价'}).json()
    assert first['id'] == second['id'] and second['cached']
    assert first['observed_at'] == second['observed_at'] and len(calls) == 1
    assert first['data_mode'] == 'provider-observation'
    assert client.get('/api/mx/history', headers=bob).json() == []
    assert client.get(f'/api/mx/history/{first["id"]}', headers=bob).status_code == 404
    assert client.get(f'/api/mx/history/{first["id"]}/export', headers=bob).status_code == 404
    import backend.main as main
    with main.SessionLocal() as db:
        record = db.get(ProviderQuery, first['id'])
        record.observed_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        db.commit()
    third = client.post('/api/mx/query', headers=alice, json={'query': '东方财富最新价'}).json()
    assert third['id'] != first['id'] and not third['cached'] and len(calls) == 2
    assert len(client.get('/api/mx/history', headers=alice).json()) == 2
    assert client.get('/api/live/analysis').json()['effective_posts'] == 0
    assert client.get('/api/analysis').json()['data_mode'] == 'demo'


def test_failed_queries_saved_without_provider_message_or_cached_fallback(client, monkeypatch):
    headers = account(client, 'mxfail')
    def fail(*args): raise mx.ProviderError('quota', '调用额度不足', 503)
    monkeypatch.setattr(mx, 'fetch_result', fail)
    response = client.post('/api/mx/query', headers=headers, json={'kind': 'screen', 'query': '板块前十'})
    assert response.status_code == 503
    history = client.get('/api/mx/history', headers=headers).json()
    assert history[0]['status'] == 'failed'
    saved = client.get(f'/api/mx/history/{history[0]["id"]}', headers=headers).json()
    assert saved['error_code'] == 'quota' and not saved.get('tables')
    assert client.get(f'/api/mx/history/{saved["id"]}/export', headers=headers).status_code == 422
    assert client.post('/api/mx/query', headers=headers, json={'kind': 'trade', 'query': '买入股票'}).status_code == 422
    assert client.post('/api/mx/query', headers=headers, json={'query': 'mkt_do-not-send'}).status_code == 422
    assert len(client.get('/api/mx/history', headers=headers).json()) == 1


def test_export_is_real_workbook_and_untrusted_formula_is_text(client, monkeypatch):
    result = mx.normalize(data_payload(), 'data')
    result['tables'][0]['rows'][0][1] = '=HYPERLINK("https://example.invalid")'
    monkeypatch.setattr(mx, 'fetch_result', lambda *args: result)
    headers = account(client, 'mxexport')
    row = client.post('/api/mx/query', headers=headers, json={'query': '=测试查询'}).json()
    exported = client.get(f'/api/mx/history/{row["id"]}/export', headers=headers)
    book = load_workbook(io.BytesIO(exported.content))
    assert book['数据1']['B3'].value.startswith('=HYPERLINK')
    assert book['数据1']['B3'].data_type == 's'
    assert book['查询记录']['B2'].value == '=测试查询' and book['查询记录']['B2'].data_type == 's'


def test_missing_key_and_rate_limits_do_not_create_observations(client, monkeypatch):
    headers = account(client, 'mxlimits')
    monkeypatch.setattr(mx, 'api_key', lambda: '')
    assert not client.get('/api/mx/status').json()['configured']
    assert client.post('/api/mx/query', headers=headers, json={'query': '东方财富'}).status_code == 503
    monkeypatch.setattr(mx, 'api_key', lambda: 'test-only-credential')
    mx.attempts.extend([mx.time.monotonic()] * 5)
    assert client.post('/api/mx/query', headers=headers, json={'query': '东方财富'}).status_code == 429
    assert client.get('/api/mx/history', headers=headers).json() == []
