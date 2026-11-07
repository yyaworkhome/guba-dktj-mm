"""The primary action must reach a source-backed result without account setup."""
import json
from datetime import datetime, timedelta, timezone
import httpx
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import get_db
from backend.models import MarketScan,UniverseSync
from backend import local_collection
from backend.collect import collector_lock
from backend.interpretation import interpret_sample
from backend.tests.test_api import client
from backend.tests.test_securities_collection import directory, directory_row


def test_one_click_collect_analyze_and_cooldown_without_login(client, monkeypatch, tmp_path):
    import backend.collect as collector
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION', 'true')
    monkeypatch.setattr(collector, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(local_collection, 'DATA_DIR', tmp_path)
    calls = []
    now = datetime.now(timezone.utc)
    published = (now-timedelta(minutes=5)).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    def handler(req):
        calls.append(str(req.url))
        if req.url.host == 'searchapi.eastmoney.com':
            return httpx.Response(200, text=directory([directory_row()]))
        if req.url.host == 'guba.eastmoney.com':
            return httpx.Response(200, text='<script>var article_list='+json.dumps({'rc':1,'bar_code':'002594','re':[
                {'post_id':12345,'stockbar_code':'002594','post_type':0,'post_state':0,'user_id':'42',
                 'post_title':'看多上涨','post_publish_time':published,'post_click_count':15,'post_comment_count':2}]})+';</script>')
        return httpx.Response(200, text=json.dumps({'rc':0,'data':{'code':'002594','market':0,
            'klines':[f'{now.date()},10,11,12,9,100,100000,0,0,0,0']}}))
    monkeypatch.setattr(local_collection, 'default_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',1234)) as c:
        body = {'instrument_id':'XSHE:002594','days':3,'content':'titles'}
        r = c.post('/api/live/analyze', json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data['collection']['mode'] == 'collected'
        assert data['analysis']['name'] == '比亚迪' and data['analysis']['data_mode'] == 'live'
        assert data['analysis']['counts']['bullish'] == 1
        assert data['analysis']['interpretation']['status'] == 'limited'
        assert data['analysis']['interpretation']['quality_reasons']
        assert c.get('/api/live/timeline?instrument_id=XSHE:002594').json()['points']
        before = len(calls)
        reused = c.post('/api/live/analyze', json=body).json()
        assert reused['collection']['mode'] == 'cooldown' and len(calls) == before
        assert reused['analysis']['last_post_observation'] == data['analysis']['last_post_observation']
        other = c.post('/api/live/analyze', json={**body,'content':'body'}).json()
        assert other['analysis']['effective_posts'] == 0 and other['analysis']['opinion_score'] is None
        assert other['analysis']['interpretation']['status'] == 'empty'
        assert len(calls) == before  # Neither retries nor substitutes title samples for body.


def test_one_click_respects_remote_origin_disabled_service_and_lock(client, monkeypatch, tmp_path):
    import backend.collect as collector
    monkeypatch.setattr(collector, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(local_collection, 'DATA_DIR', tmp_path)
    def forbidden(): raise AssertionError('source client must not open')
    monkeypatch.setattr(local_collection, 'default_client', forbidden)
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION', 'true')
    body = {'instrument_id':'XSHE:300059'}
    assert client.post('/api/live/analyze',json=body).json()['collection']['mode'] == 'saved'
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',1234)) as c:
        assert c.post('/api/live/analyze',headers={'Origin':'https://example.com'},json=body).json()['collection']['mode'] == 'saved'
        with collector_lock():
            r = c.post('/api/live/analyze',json=body).json()
            assert r['collection']['mode'] == 'busy' and r['analysis']['effective_posts'] == 0
        monkeypatch.delenv('ALLOW_LOCAL_COLLECTION')
        assert c.post('/api/live/analyze',json=body).json()['collection']['mode'] == 'saved'
        for bad in ({'days':0},{'instrument_id':'XSHG:000001'},{'content':'combined'}):
            assert c.post('/api/live/analyze',json={**body,**bad}).status_code == 422


def test_source_rejection_is_visible_and_never_creates_demo_results(client, monkeypatch, tmp_path):
    import backend.collect as collector
    monkeypatch.setattr(collector, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(local_collection, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION', 'true')
    monkeypatch.setattr(local_collection, 'default_client', lambda: httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(403))))
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',1234)) as c:
        response = c.post('/api/live/analyze',json={'instrument_id':'XSHE:300059'})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['collection']['run']['status'] == 'failed'
        assert '403' in data['collection']['run']['details']['posts']['error']
        assert data['analysis']['effective_posts'] == 0 and data['analysis']['opinion_score'] is None
        assert c.get('/api/live/timeline').json()['points'] == []


def test_source_verification_pause_reads_saved_analysis_without_opening_network(client, monkeypatch):
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION', 'true')
    def forbidden(): raise AssertionError('paused source must not be requested')
    monkeypatch.setattr(local_collection, 'default_client', forbidden)
    with next(app.dependency_overrides[get_db]()) as db:
        universe=UniverseSync(status='success',total=1,members=['XSHE:300059'])
        db.add(universe);db.flush()
        db.add(MarketScan(universe_id=universe.id,active_key='active',status='paused',total=1,
                          error='源站要求身份核实或验证码，已暂停'))
        db.commit()
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as local:
        result=local.post('/api/live/analyze',json={'instrument_id':'XSHE:300059'}).json()
        assert result['collection']['mode']=='source_paused'
        assert '更新行情' in result['collection']['message']
        assert result['collection']['run'] is None
        assert result['analysis']['effective_posts']==0 and result['analysis']['opinion_score'] is None
        assert local.get('/api/live/status').json()['latest_run'] is None


def test_interpretation_distinguishes_unrecognized_limited_and_observed_samples():
    empty = interpret_sample({}, 0, 0, True, None)
    assert empty['status'] == 'empty' and empty['direction_coverage'] is None
    neutral = interpret_sample({'neutral':30}, 30, 30, False, None)
    assert neutral['status'] == 'insufficient' and neutral['direction_coverage'] == 0
    limited = interpret_sample({'bullish':1,'neutral':29}, 30, 30, False, None)
    assert limited['status'] == 'limited' and '覆盖不足' in limited['headline']
    observed = interpret_sample({'bullish':12,'bearish':8,'neutral':10}, 30, 30, False,
        {'observed_at':'2026-11-07T01:00:00+00:00','bars':[{'date':'2026-11-05','close':10},{'date':'2026-11-06','close':11}]})
    assert observed['status'] == 'observed' and not observed['quality_reasons']
    assert observed['price_observation']['change_pct'] == 10
    assert '24小时' in interpret_sample({'bullish':20},20,20,True,None)['quality_reasons'][-1]
