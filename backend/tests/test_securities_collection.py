import json
import io
import time
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.tests.test_api import client, account
from backend.database import get_db
from backend.main import app
from backend.models import Instrument, CollectionRun
from backend import securities, local_collection
from backend.collect import collector_lock
from backend.eastmoney import stock, list_url, kline_url, detail_url, get_document, SourceError
from openpyxl import load_workbook


def directory_row(code='002594',name='比亚迪',market='0',classification='AStock',security='2',label='深A'):
    return {'Code':code,'Name':name,'MktNum':market,'QuoteID':f'{market}.{code}',
            'Classify':classification,'SecurityType':security,'SecurityTypeName':label,'PinYin':'BYD'}


def directory(rows): return json.dumps({'QuotationCodeTable':{'Status':0,'Data':rows}},ensure_ascii=False)


@pytest.fixture(autouse=True)
def clear_directory_state():
    securities.cache.clear();securities.attempts.clear()
    yield
    securities.cache.clear();securities.attempts.clear()


def test_directory_recognizes_three_exchanges_and_rejects_wrong_identity():
    rows=[directory_row(),directory_row('600036','招商银行','1',security='1',label='沪A'),
          directory_row('920002','万达轴承',classification='NEEQ',security='27',label='京A'),
          directory_row('000001','上证指数','1','Index','5','指数'),
          directory_row('920001','新三板',classification='NEEQ',security='24',label='三板'),
          directory_row('600999','错误市场'),{**directory_row('002999'), 'QuoteID':'1.002999'},
          directory_row('01211','港股','116','HK','19','港股')]
    result=securities.parse_directory(directory(rows))
    assert {r['id'] for r in result}=={'XSHE:002594','XSHG:600036','XBSE:920002','XSHG:000001'}
    with pytest.raises(SourceError): securities.parse_directory('captcha')
    for iid in ('XSHG:000001','XSHE:159001','XSHG:510300','XSHE:600036','XBSE:830001','002594'):
        with pytest.raises(ValueError): stock(iid)


def test_search_persists_verified_candidates_and_falls_back_without_demo(client,monkeypatch):
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200,text=directory([directory_row(),directory_row('000001','上证指数','1','Index','5','指数')]))
    monkeypatch.setattr(securities,'default_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    data=client.get('/api/live/search?q=比亚迪').json()
    assert data['source']=='东方财富证券目录' and len(data['items'])==2
    assert client.get('/api/live/search?q=比亚迪').status_code==200 and len(calls)==1
    assert any(r['id']=='XSHE:002594' for r in client.get('/api/live/instruments').json())
    a=account(client,'directoryowner')
    assert client.put('/api/live/watchlist',headers=a,json={'instrument_id':'XSHE:002594'}).status_code==200
    assert client.post('/api/live/alerts',headers=a,json={'instrument_id':'XSHE:002594','threshold':80}).status_code==200
    assert client.get('/api/live/watchlist',headers=a).json()['items'][0]['supported']
    securities.cache.clear()
    monkeypatch.setattr(securities,'default_client',lambda:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(403))))
    fallback=client.get('/api/live/search?q=002594').json()
    assert fallback['warning'] and fallback['items'][0]['name']=='比亚迪'
    assert client.get('/api/live/search?q=机器人').json()['items']==[]
    assert client.put('/api/live/watchlist',headers=a,json={'instrument_id':'XSHE:002998'}).status_code==422
    assert client.get('/api/live/search?q= ').status_code==422


def test_local_gate_requires_opt_in_loopback_host_and_origin(monkeypatch):
    def request(host='127.0.0.1',origin='http://127.0.0.1:5173',client_host='127.0.0.1'):
        return Request({'type':'http','scheme':'http','path':'/','query_string':b'',
                        'headers':[(b'host',host.encode()),(b'origin',origin.encode())],
                        'client':(client_host,1234),'server':('127.0.0.1',8000),'method':'GET'})
    monkeypatch.delenv('ALLOW_LOCAL_COLLECTION',raising=False)
    assert not local_collection.enabled_for(request())
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION','true')
    assert local_collection.enabled_for(request())
    assert not local_collection.enabled_for(request(host='example.com'))
    assert not local_collection.enabled_for(request(origin='https://example.com'))
    assert not local_collection.enabled_for(request(client_host='192.168.1.1'))
    assert not local_collection.enabled_for(request(origin='null'))


def test_web_collection_verifies_new_stock_persists_real_samples_and_locks(client,monkeypatch,tmp_path):
    import backend.collect as collector
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION','true')
    monkeypatch.setattr(collector,'DATA_DIR',tmp_path);monkeypatch.setattr(local_collection,'DATA_DIR',tmp_path)
    requests=[]
    now=datetime.now(timezone.utc)
    published=(now-timedelta(minutes=5)).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    def handler(req):
        requests.append(str(req.url))
        if req.url.host=='searchapi.eastmoney.com':return httpx.Response(200,text=directory([directory_row()]))
        if req.url.host=='guba.eastmoney.com':return httpx.Response(200,text='<script>var article_list='+json.dumps({'rc':1,'bar_code':'002594','re':[
            {'post_id':12345,'stockbar_code':'002594','post_type':0,'post_state':0,'user_id':'42',
             'post_title':'看多上涨','post_publish_time':published,'post_click_count':15,'post_comment_count':2}]})+';</script>')
        return httpx.Response(200,text=json.dumps({'rc':0,'data':{'code':'002594','market':0,
            'klines':[f'{now.date()},10,11,12,9,100,100000,0,0,0,0']}}))
    monkeypatch.setattr(local_collection,'default_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    c=TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234))
    a=account(c,'collectionowner');body={'instrument_id':'XSHE:002594'}
    assert c.post('/api/live/collection',json=body).status_code==401
    with collector_lock():
        assert c.get('/api/live/collection?instrument_id=XSHE:002594').json()['locked']
        assert c.post('/api/live/collection',headers=a,json=body).status_code==409
    assert requests==[]
    result=c.post('/api/live/collection',headers=a,json=body)
    assert result.status_code==200 and result.json()['status']=='success',result.text
    actual=c.get('/api/live/analysis?instrument_id=XSHE:002594&days=3').json()
    assert actual['name']=='比亚迪' and actual['effective_posts']==1 and actual['counts']['bullish']==1
    assert actual['data_mode']=='live' and actual['quote']['bars'][0]['close']==11
    assert c.get('/api/live/timeline?instrument_id=XSHE:002594').json()['points']
    assert c.get('/api/live/rankings').json()['scope_size']==6
    before=len(requests)
    assert c.post('/api/live/collection',headers=a,json={**body,'content':'body'}).status_code==409
    assert len(requests)==before and c.get('/api/live/collection?instrument_id=XSHE:002594').json()['next_allowed_at']
    assert not (tmp_path/'collector.lock').exists()
    monkeypatch.delenv('ALLOW_LOCAL_COLLECTION')
    assert c.post('/api/live/collection',headers=a,json=body).status_code==403
    c.close()


def test_generated_urls_never_accept_foreign_hosts_or_custom_parameters():
    called=[]
    def handler(req):called.append(str(req.url));return httpx.Response(200,text='ok')
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        for iid in ('XSHE:002594','XSHG:600036','XBSE:920002'):
            for url in (list_url(iid),kline_url(iid),detail_url(iid,'12345')):
                assert get_document(c,url)=='ok'
        before=len(called)
        for url in ('https://example.com/list,002594.html',list_url('XSHE:002594')+'?x=1',
                    kline_url('XSHE:002594')+'&extra=1','https://guba.eastmoney.com/list,000001.html/../secret'):
            with pytest.raises(SourceError):get_document(c,url)
        assert len(called)==before


def test_search_rate_limit_and_unverified_code_never_collects(client,monkeypatch,tmp_path):
    import backend.collect as collector
    securities.attempts['testclient'].extend([time.monotonic()]*30)
    assert client.get('/api/live/search?q=002594').status_code==429
    requests=[]
    with httpx.Client(transport=httpx.MockTransport(lambda r:(requests.append(str(r.url)) or httpx.Response(200,text=directory([]))))) as source:
        generator=app.dependency_overrides[get_db]();db=next(generator)
        try:
            with pytest.raises(ValueError):collector.collect(db,source,'XSHE:002998')
            assert db.scalar(select(CollectionRun).where(CollectionRun.instrument_id=='XSHE:002998')) is None
        finally:generator.close()
    assert len(requests)==1 and requests[0].startswith(securities.SEARCH_URL)


def test_new_live_stock_does_not_break_demo_catalog_signals_or_shared_watchlist(client):
    generator=app.dependency_overrides[get_db]();db=next(generator)
    try: securities.register(db,[{**directory_row(), 'id':'XSHE:002594','code':'002594','name':'比亚迪','kind':'stock','exchange':'XSHE','pinyin':'BYD','market':0}])
    finally:generator.close()
    assert len(client.get('/api/instruments').json())==15
    assert client.get('/api/signals').status_code==200
    assert all(r['id']!='XSHE:002594' for r in client.get('/api/rankings').json())
    a=account(client,'mixedwatchowner')
    client.put('/api/live/watchlist',headers=a,json={'instrument_id':'XSHE:002594'})
    client.put('/api/watchlist',headers=a,json={'instrument_id':'XSHE:000001'})
    watches=client.get('/api/watchlist',headers=a).json()
    assert next(r for r in watches if r['id']=='XSHE:002594')['demo_available'] is False
    assert next(r for r in watches if r['id']=='XSHE:000001')['demo_available'] is True
    assert len(client.get('/api/live/watchlist',headers=a).json()['items'])==2


def test_live_export_preserves_missing_values_and_text_cells(client):
    from backend.ingestion import record_post,classify_revision
    generator=app.dependency_overrides[get_db]();db=next(generator)
    try:
        _,revision,_=record_post(db,source='eastmoney-title-v1',source_id='987654',instrument_id='XSHE:300059',author_id='1',
            text='=SUM(1,2) 普通事实讨论',published_at=datetime.now(timezone.utc)-timedelta(minutes=1))
        classify_revision(db,revision);db.commit()
    finally:generator.close()
    r=client.get('/api/live/export?instrument_id=XSHE:300059&days=3')
    assert r.status_code==200 and r.headers['cache-control']=='no-store'
    book=load_workbook(io.BytesIO(r.content))
    assert book.sheetnames==['真实采样摘要','按发帖日期分组','最近5条有效样本','日线观察','采集时点快照']
    stats={r[0]:r[1] for r in book.worksheets[0].iter_rows(values_only=True)}
    assert stats['有效样本']==1 and stats['舆论分（无方向留空）'] is None
    cell=book['最近5条有效样本']['B2']
    assert cell.value.startswith('=SUM') and cell.data_type=='s'
    assert book['采集时点快照'].max_row==1
    assert client.get('/api/live/export?instrument_id=XSHG:000001').status_code==422
    assert client.get('/api/live/export?days=0').status_code==422
    empty=load_workbook(io.BytesIO(client.get('/api/live/export?content=body').content))
    assert dict((r[0],r[1]) for r in empty.worksheets[0].iter_rows(values_only=True))['有效样本']==0
