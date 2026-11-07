from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
from backend.tests.test_api import client, account
from backend.database import get_db
from backend.main import app
from backend.models import Snapshot, PriceBar, Instrument, CollectionRun
from backend.ingestion import record_post, classify_revision
from backend.eastmoney import IID, SOURCE, BODY_SOURCE


def session():
    generator = app.dependency_overrides[get_db]()
    return generator, next(generator)


def test_live_rules_require_auth_validate_and_isolate_owners(client):
    assert client.get('/api/live/alerts').status_code == 401
    assert client.get('/api/live/watchlist').status_code == 401
    a, b = account(client,'livealice'), account(client,'livebob')
    body = {'instrument_id':IID,'threshold':0}
    result = client.post('/api/live/alerts',headers=a,json=body)
    assert result.status_code == 200
    rule = result.json()['id']
    assert client.post('/api/live/alerts',headers=a,json=body).json()['id'] == rule
    row = client.get('/api/live/alerts',headers=a).json()[0]
    assert row['data_mode']=='live' and row['value'] is None and not row['triggered'] and not row['ready']
    assert row['raw_value'] is None and row['reasons']
    assert client.get('/api/live/alerts',headers=b).json() == []
    client.delete(f'/api/live/alerts/{rule}',headers=b)
    assert len(client.get('/api/live/alerts',headers=a).json()) == 1
    assert client.get('/api/alerts',headers=a).json() == []
    for update in [{'instrument_id':'000001'},{'instrument_id':'XSHG:000001'},
                   {'content':'mixed'},{'days':0},{'threshold':101},{'metric':'score'}]:
        assert client.post('/api/live/alerts',headers=a,json={**body,**update}).status_code==422
    client.delete(f'/api/live/alerts/{rule}',headers=a)
    assert client.get('/api/live/alerts',headers=a).json()==[]


def test_real_watchlist_never_substitutes_demo_and_preserves_unsupported(client):
    a,b=account(client,'watchalice'),account(client,'watchbob')
    assert client.put('/api/live/watchlist',headers=a,json={'instrument_id':'000001'}).status_code==422
    for _ in range(2):
        assert client.put('/api/live/watchlist',headers=a,json={'instrument_id':IID}).status_code==200
    client.put('/api/watchlist',headers=a,json={'instrument_id':'SECTOR:SEMI'})
    rows=client.get('/api/live/watchlist',headers=a).json()['items']
    assert len(rows)==2
    stock=next(r for r in rows if r['id']==IID)
    assert stock['analysis']['effective_posts']==0 and stock['analysis']['opinion_score'] is None
    assert stock['analysis']['reasons']
    sector=next(r for r in rows if r['id']=='SECTOR:SEMI')
    assert not sector['supported'] and sector['analysis'] is None
    assert client.get('/api/live/watchlist',headers=b).json()['items']==[]
    client.delete(f'/api/watchlist?instrument_id={IID}',headers=b)
    assert len(client.get('/api/live/watchlist',headers=a).json()['items'])==2
    client.delete(f'/api/watchlist?instrument_id={IID}',headers=a)
    assert len(client.get('/api/live/watchlist',headers=a).json()['items'])==1


def test_real_alert_quality_gate_content_and_freshness(client):
    a=account(client,'qualityalice')
    now=datetime.now(timezone.utc)
    g,db=session()
    try:
        for i in range(20):
            _,r,_=record_post(db,source=SOURCE,source_id=f'private-{i}',instrument_id=IID,author_id=str(i),
                text=f'{"看多上涨" if i<10 else "普通讨论"} 唯一{i}',published_at=now-timedelta(minutes=5))
            classify_revision(db,r)
        db.commit()
    finally:g.close()
    client.post('/api/live/alerts',headers=a,json={'instrument_id':IID,'metric':'bullish_pct','threshold':40})
    client.post('/api/live/alerts',headers=a,json={'instrument_id':IID,'metric':'bullish_pct','threshold':60})
    client.post('/api/live/alerts',headers=a,json={'instrument_id':IID,'content':'body','threshold':0})
    rows=client.get('/api/live/alerts',headers=a).json()
    assert next(r for r in rows if r['threshold']==40)['triggered']
    assert not next(r for r in rows if r['threshold']==60)['triggered']
    assert next(r for r in rows if r['threshold']==60)['ready']
    assert not next(r for r in rows if r['content']=='body')['ready']
    # A fresh run cannot make an empty publication window eligible.
    client.post('/api/live/alerts',headers=a,json={'instrument_id':IID,'days':1,'threshold':0})
    g,db=session()
    try:
        from backend.models import Post,PostRevision
        for p in db.scalars(select(Post).where(Post.source==SOURCE)):
            p.published_at=now-timedelta(days=2)
        db.add(CollectionRun(instrument_id=IID,started_at=now,finished_at=now,status='success',
            details={'_meta':{'source':SOURCE},'posts':{'status':'ok','observed_at':now.isoformat()}}))
        db.commit()
    finally:g.close()
    row=next(r for r in client.get('/api/live/alerts',headers=a).json() if r['days']==1)
    assert not row['ready'] and not row['triggered'] and row['value'] is None


def test_real_account_features_without_demo_catalog(client):
    a=account(client,'noseedalice')
    g,db=session()
    try:
        db.execute(delete(Snapshot));db.execute(delete(PriceBar));db.execute(delete(Instrument));db.commit()
    finally:g.close()
    assert client.put('/api/live/watchlist',headers=a,json={'instrument_id':IID}).status_code==200
    assert client.post('/api/live/alerts',headers=a,json={'instrument_id':'XSHE:300750','threshold':80}).status_code==200
    assert len(client.get('/api/live/alerts',headers=a).json())==1
    rows=client.get('/api/live/watchlist',headers=a).json()['items']
    assert len(rows)==1 and rows[0]['analysis']['opinion_score'] is None
