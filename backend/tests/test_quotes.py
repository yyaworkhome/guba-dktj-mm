import json
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from backend.database import Base
from backend.models import Instrument,QuoteFetch,CollectionRun,Snapshot
from backend.quotes import parse_tencent,refresh_quote,saved_quote,quote_analysis
from backend.eastmoney import SourceError
from backend.decision import technical_levels
from backend.main import app
from backend.tests.test_api import client

NOW=datetime.now(timezone.utc)


def payload(symbol='sh600900',extended=True):
    rows=[]
    for n in range(30):
        close=10+n*.03
        row=[(NOW-timedelta(days=30-n)).date().isoformat(),str(close-.01),str(close),str(close+.05),str(close-.05),'100000000' if symbol.startswith('sh68') else '1000000']
        if extended:row.extend([{},'1.0',str(close*10000),'0','0'])
        rows.append(row)
    return {'code':0,'data':{symbol:{'day':rows,'qt':{symbol:['1','测试样本',symbol[2:]]}}}}


@pytest.fixture
def db():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        db.add(Instrument(id='XSHG:600900',code='600900',name='测试',kind='stock',exchange='XSHG',scope={'live_directory':{'name':'测试'}}));db.commit()
        yield db
    engine.dispose()


def test_tencent_identity_price_units_and_missing_fields_are_checked():
    body=payload();bars=parse_tencent(json.dumps(body),'XSHG:600900',NOW)
    assert len(bars)==30 and bars[-1]['volume_lots']==1000000
    assert bars[-1]['amount_cny']==pytest.approx(bars[-1]['close']*100000000)
    star=payload('sh688981');star['data']['sh688981']['day'][-1][5]='100000000'
    assert parse_tencent(json.dumps(star),'XSHG:688981',NOW)[-1]['volume_lots']==1000000
    for kind in ['wrong_identity','future','bad_price','bad_amount','duplicate']:
        invalid=deepcopy(body);data=invalid['data']['sh600900'];row=data['day'][-1]
        if kind=='wrong_identity':data['qt']['sh600900'][2]='000001'
        elif kind=='future':row[0]=(NOW+timedelta(days=2)).date().isoformat()
        elif kind=='bad_price':row[4]='999'
        elif kind=='bad_amount':row[8]='1'
        else:row[0]=data['day'][-2][0]
        with pytest.raises(SourceError):parse_tencent(json.dumps(invalid),'XSHG:600900',NOW)
    bars=parse_tencent(json.dumps(payload(extended=False)),'XSHG:600900',NOW)
    technical,reasons=technical_levels({'observed_at':NOW.isoformat(),'bars':bars,'source':'腾讯财经未复权日线'},NOW)
    assert not reasons and technical['regime']=='趋势偏多'
    assert technical['average_amount20'] is None and technical['trade_reasons']
    assert not technical.get('conditional_levels')


def test_independent_market_refresh_never_creates_forum_samples_and_has_its_own_cooldown(db):
    calls=[]
    def handler(request):
        calls.append(request.url.host)
        assert request.url.host=='proxy.finance.qq.com'
        return httpx.Response(200,json=payload())
    before=datetime.now(timezone.utc)
    with httpx.Client(transport=httpx.MockTransport(handler)) as source:
        run,reused=refresh_quote(db,source,'XSHG:600900')
        again,cached=refresh_quote(db,source,'XSHG:600900')
    assert run.status=='success' and not reused and cached and again.id==run.id
    assert calls==['proxy.finance.qq.com']
    assert db.scalar(select(func.count()).select_from(CollectionRun))==0
    assert db.scalar(select(func.count()).select_from(Snapshot))==0
    assert saved_quote(db,'XSHG:600900',before) is None
    result=quote_analysis(db,'XSHG:600900')
    assert result['technical']['regime']=='趋势偏多' and not result['reasons']


def test_fallback_and_failed_refresh_preserve_previous_observation(db):
    def handler(request):
        if request.url.host=='proxy.finance.qq.com':return httpx.Response(503)
        day=(NOW-timedelta(days=1)).date().isoformat()
        return httpx.Response(200,json={'rc':0,'data':{'code':'600900','market':1,'klines':[f'{day},10,11,12,9,100,100000']}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as source:run,_=refresh_quote(db,source,'XSHG:600900')
    assert run.source=='东方财富未复权日线' and len(run.details)==2
    run.started_at-=timedelta(minutes=11);db.commit()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503))) as source:failed,_=refresh_quote(db,source,'XSHG:600900')
    assert failed.status=='failed' and failed.bars==[]
    assert saved_quote(db,'XSHG:600900',datetime.now(timezone.utc))['bars']==run.bars
    assert quote_analysis(db,'XSHG:600900')['refresh']['status']=='failed'


def test_quote_only_endpoint_is_local_and_keeps_sentiment_empty(client,monkeypatch,tmp_path):
    import backend.quotes as quotes
    import backend.collect as collect
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION','true');monkeypatch.setattr(collect,'DATA_DIR',tmp_path)
    calls=[]
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200,json=payload('sz300059'))
    monkeypatch.setattr(quotes,'default_client',lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.post('/api/live/quotes',json={'instrument_id':'XSHE:300059'}).status_code==403
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as local:
        result=local.post('/api/live/quotes',json={'instrument_id':'XSHE:300059'}).json()
        assert result['technical']['regime']=='趋势偏多'
        live=local.get('/api/live/analysis?instrument_id=XSHE:300059').json()
        assert live['effective_posts']==0 and live['opinion_score'] is None
        assert live['quote']['source']=='腾讯财经未复权日线'
        decision=local.get('/api/live/decision?instrument_id=XSHE:300059').json()
        assert decision['score'] is None and decision['plan'] is None
        assert decision['data_status']=={'price':'技术数据可用','sentiment':'样本待补充'}
        assert local.get('/api/live/timeline?instrument_id=XSHE:300059').json()['points']==[]
        assert local.post('/api/live/quotes',json={'instrument_id':'XSHG:000001'}).status_code==409
    assert calls==['proxy.finance.qq.com']
