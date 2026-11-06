from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.database import Base
from backend.eastmoney import IID, SOURCE, BODY_SOURCE
from backend.models import Instrument, Snapshot, CollectionRun
from backend.ingestion import record_post, classify_revision
from backend.live import snapshot_timeline, live_rankings
from backend.tests.test_api import client

NOW = datetime.now(timezone.utc)

@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        s.add(Instrument(id=IID,code='300059',name='东方财富',kind='stock',exchange='XSHE'))
        s.commit()
        yield s
    engine.dispose()

def snapshot(db, available, score=70, source=SOURCE, content='titles', mode='live', created=None, n=20, directional=10):
    row = Snapshot(instrument_id=IID, session_date=available.date().isoformat(), available_at=available,
        created_at=created or available, version=f'{mode}-{source}-{available.isoformat()}', data_mode=mode,
        metrics={'source':source,'content':content,'instrument_id':IID,'opinion_score':score,
        'effective_posts':n,'authors':12,'counts':{'bullish':directional,'bearish':0,'neutral':n-directional},
        'days':1,'range':[available.date().isoformat()]*2})
    db.add(row); db.commit()
    return row

def test_timeline_only_saved_matching_source_and_available_time(db):
    early = snapshot(db,NOW-timedelta(hours=2),score=70)
    snapshot(db,NOW-timedelta(hours=1),score=5,source=BODY_SOURCE,content='body')
    snapshot(db,NOW-timedelta(minutes=40),score=1,mode='demo')
    snapshot(db,NOW-timedelta(minutes=30),score=20,created=NOW+timedelta(minutes=1))
    snapshot(db,NOW+timedelta(minutes=2),score=3)
    snapshot(db,NOW-timedelta(days=31),score=8)
    points = snapshot_timeline(db,as_of=NOW)['points']
    assert len(points)==1 and points[0]['id']==early.id and points[0]['opinion_score']==70
    # Later post classification cannot revise already saved snapshot metrics.
    _,r,_=record_post(db,source=SOURCE,source_id='1',instrument_id=IID,author_id='test',
                      text='看空下跌',published_at=NOW-timedelta(minutes=10))
    classify_revision(db,r);db.commit()
    assert snapshot_timeline(db,as_of=NOW)['points'][0]['opinion_score']==70
    assert len(snapshot_timeline(db,content='body',as_of=NOW)['points'])==1

def test_timeline_preserves_null_and_bounds_without_averaging(db):
    for i in range(202): snapshot(db,NOW-timedelta(minutes=203-i),score=i%101)
    snapshot(db,NOW-timedelta(seconds=1),score=50,n=0,directional=0)
    data=snapshot_timeline(db,as_of=NOW)
    assert data['truncated'] and data['total_points']==203 and len(data['points'])==200
    assert data['points'][-1]['opinion_score'] is None
    assert data['points'][0]['opinion_score']==3

def add_samples(db,source=SOURCE,n=20,directional=10,authors=20):
    for i in range(n):
        _,r,_=record_post(db,source=source,source_id=f'{source}-{i}',instrument_id=IID,author_id=str(i%authors),
            text=f'{"看多上涨" if i<directional else "讨论记录"} 唯一{i}',published_at=NOW-timedelta(minutes=10))
        classify_revision(db,r)
    db.commit()

def test_rankings_thresholds_source_and_stale_never_fill_from_demo(db):
    add_samples(db)
    data=live_rankings(db,days=3,as_of=datetime.now(timezone.utc))
    assert len(data['ranked'])==1 and data['ranked'][0]['id']==IID
    assert data['ranked'][0]['rank']==1 and data['ranked'][0]['directional_posts']==10
    assert len(data['unranked'])==4 and data['scope_size']==5
    assert not live_rankings(db,content='body')['ranked']
    # A future read may include the publication window yet must exclude stale observations.
    future=live_rankings(db,days=5,as_of=NOW+timedelta(days=2))
    assert not future['ranked'] and any('24小时' in s for s in future['unranked'][0]['reasons'])

def test_fresh_collection_does_not_make_insufficient_direction_samples_rankable(db):
    add_samples(db,directional=2)
    db.add(CollectionRun(instrument_id=IID,started_at=NOW,finished_at=NOW,status='success',
        details={'_meta':{'source':SOURCE},'posts':{'status':'ok','observed_at':NOW.isoformat()}}))
    db.commit()
    data=live_rankings(db)
    assert not data['ranked']
    item=next(r for r in data['unranked'] if r['id']==IID)
    assert item['effective_posts']==20 and not item['stale']
    assert item['reasons']==['方向样本2，少于10']

def test_live_research_api_validation_and_empty_coverage(client):
    assert client.get('/api/live/timeline').json()['points']==[]
    result=client.get('/api/live/rankings').json()
    assert result['ranked']==[] and len(result['unranked'])==5 and result['data_mode']=='live'
    for url in ['/api/live/timeline?instrument_id=000001','/api/live/timeline?content=unknown',
                '/api/live/rankings?days=0','/api/live/rankings?sort=prediction']:
        assert client.get(url).status_code==422
