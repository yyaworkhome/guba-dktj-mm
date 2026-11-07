import json
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from backend.database import Base
from backend.models import Instrument, UniverseSync, MarketScan, ScanItem, CollectionRun
from backend.universe import sync_universe
from backend.scanner import queue_scan, process_item
from backend.market_scan import rankings, job_dict
from backend.eastmoney import SOURCE
from backend.tests.test_api import client


@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as session: yield session
    engine.dispose()


def universe(db,ids):
    for iid in ids:
        db.add(Instrument(id=iid,code=iid[5:],name=iid,kind='stock',exchange=iid[:4],scope={'live_directory':{'name':iid,'market':0,'pinyin':''}}))
    row=UniverseSync(status='success',total=len(ids),members=ids,finished_at=datetime.now(timezone.utc))
    db.add(row);db.commit();return row


def test_directory_only_publishes_complete_consistent_pages(db):
    universe(db,['XSHE:000001'])
    def handler(request):
        page=int(request.url.params['pn'])
        rows=[{'f12':f'{n:06d}','f13':0,'f14':f'证券{n}'} for n in (range(1,101) if page==1 else [101])]
        return httpx.Response(200,json={'data':{'total':101,'diff':rows}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as source:
        result=sync_universe(db,source,pause=lambda _:None,provider='eastmoney')
    assert result.status=='success' and result.total==101 and len(set(result.members))==101
    before=db.scalar(select(func.count()).select_from(Instrument))
    def bad(request): return httpx.Response(200,json={'data':{'total':101,'diff':[{'f12':'000001','f13':0,'f14':'样本'}]}})
    with httpx.Client(transport=httpx.MockTransport(bad)) as source: rejected=sync_universe(db,source,pause=lambda _:None,provider='eastmoney')
    assert rejected.status=='failed' and rejected.members==[]
    assert db.scalar(select(func.count()).select_from(Instrument))==before
    assert db.scalar(select(UniverseSync).where(UniverseSync.status=='success').order_by(UniverseSync.id.desc())).id==result.id


def test_queue_is_durable_idempotent_and_snapshots_directory(db):
    universe(db,['XSHE:000001','XSHG:600900','XBSE:920002'])
    first=queue_scan(db, '2026-11-07')
    assert queue_scan(db).id==first.id
    assert first.total==3 and db.scalar(select(func.count()).select_from(ScanItem))==3
    first.status='completed';first.active_key=None;db.commit()
    assert queue_scan(db,'2026-11-07').id==first.id
    assert queue_scan(db,'2026-11-08').id!=first.id


def test_scan_failure_cannot_rank_old_or_demo_results_and_cooldown_does_not_request(db,monkeypatch):
    import backend.scanner as scanner
    universe(db,['XSHE:000001']);job=queue_scan(db);item=db.scalar(select(ScanItem))
    now=datetime.now(timezone.utc)
    run=CollectionRun(instrument_id=item.instrument_id,started_at=now,finished_at=now,status='failed',details={'_meta':{'source':SOURCE},'posts':{'status':'failed','error':'HTTP 403'}})
    db.add(run);db.commit()
    monkeypatch.setattr(scanner,'collect',lambda *a,**k: (_ for _ in ()).throw(AssertionError('cooldown source call')))
    assert process_item(db,job,item,None) is False and item.status=='pending'
    run.started_at=now-timedelta(minutes=11);db.commit()
    monkeypatch.setattr(scanner,'collect',lambda *a,**k:{'details':{'posts':{'status':'failed','error':'HTTP 403'}}})
    assert process_item(db,job,item,None) and item.status=='failed'
    result=rankings(scan_id=job.id,limit=30,db=db)
    assert result['bullish']==result['bearish']==[]
    assert result['job']['counts']['failed']==1


def test_ranking_uses_saved_scan_summary_not_later_analysis(db):
    universe(db,['XSHE:000001','XSHG:600900']);job=queue_scan(db)
    for n,item in enumerate(db.scalars(select(ScanItem))):
        item.status='eligible';item.result={'id':item.instrument_id,'opinion_score':20+60*n,'effective_posts':25}
    db.commit()
    result=rankings(scan_id=job.id,limit=30,db=db)
    assert [r['opinion_score'] for r in result['bullish']]==[80,20]
    assert [r['opinion_score'] for r in result['bearish']]==[20,80]
    assert job_dict(db,job)['processed']==2


def test_remote_controls_are_rejected_and_empty_status_is_honest(client,monkeypatch):
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION','true')
    assert client.get('/api/live/market/status').json()['universe'] is None
    for path,body in [('start',{}),('directory',{}),('control',{'action':'resume'})]:
        assert client.post('/api/live/market/'+path,json=body).status_code==403
    assert client.put('/api/live/market/schedule',json={'enabled':True}).status_code==403
    assert client.get('/api/live/market/rankings').json()['bullish']==[]
    assert client.get('/api/live/market/export').status_code==404


def test_sina_directory_rejects_non_a_security_without_replacing_good_directory(db):
    universe(db,['XSHE:000001'])
    def source(request):
        if request.url.path.endswith('getHQNodeStockCount'):
            return httpx.Response(200,text='"4000"')
        return httpx.Response(200,json=[{'symbol':'bj810011','code':'810011','name':'优机定转'}])
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        run=sync_universe(db,client,pause=lambda _:None)
    assert run.status=='failed' and run.members==[]
    good=db.scalar(select(UniverseSync).where(UniverseSync.status=='success').order_by(UniverseSync.id.desc()))
    assert good.members==['XSHE:000001']


def test_cached_search_excludes_previously_imported_non_a_security(db):
    from backend.securities import local_matches
    from backend.live import live_catalog
    universe(db,['XSHE:000001','XBSE:810011'])
    assert local_matches(db,'810011')==[]
    assert live_catalog('810011',db)==[]


def test_scan_only_stops_for_actual_access_barrier_or_sustained_failure():
    from backend.scanner import source_pause_reason
    assert source_pause_reason({'error_code':'source_format','error':'单股列表结构异常'},3) is None
    assert '验证' in source_pause_reason({'error_code':'access_required','error':'身份核实'},1)
    assert '网络' in source_pause_reason({'error_code':'network','error':'网络错误'},3)
    assert source_pause_reason({'error_code':'source_format','error':'结构异常'},10)
    assert source_pause_reason({'error_code':'source_unavailable','error':'HTTP 404'},10) is None


def test_pending_selection_balances_exchanges_and_skips_future_retries(db):
    from backend.scanner import next_pending_item
    universe(db,['XBSE:920002','XBSE:920016','XSHG:600900','XSHG:600519','XSHE:000001','XSHE:300059'])
    job=queue_scan(db)
    first=next_pending_item(db,job);assert first.instrument_id.startswith('XSHG:')
    first.status='insufficient';db.commit()
    second=next_pending_item(db,job);assert second.instrument_id.startswith('XSHE:')
    second.status='insufficient';db.commit()
    third=next_pending_item(db,job);assert third.instrument_id.startswith('XBSE:')
    third.result={'next_retry_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()};db.commit()
    other=next_pending_item(db,job)
    assert other.instrument_id.startswith('XBSE:') and other.id!=third.id


def test_coverage_separates_collected_classified_and_ranked_without_inventing_scores(db):
    universe(db,['XSHE:000001','XSHG:600900','XBSE:920002'])
    job=queue_scan(db)
    rows=list(db.scalars(select(ScanItem).order_by(ScanItem.id)))
    rows[0].status='awaiting_ai';rows[0].result={'probe_id':1,'collection_status':'samples','raw_posts':30}
    rows[1].status='eligible';rows[1].result={'effective_posts':22,'opinion_score':80,'analysis_id':1}
    rows[2].status='failed';rows[2].result={'error_code':'source_unavailable'};db.commit()
    data=job_dict(db,job)['coverage']
    assert data['collected']==2 and data['classified']==1 and data['eligible']==1
    assert data['awaiting_ai']==1 and data['source_failed']==1
    assert sum(r['total'] for r in data['exchanges'])==3
    assert data['collected_pct']==66.67 and data['classified_pct']==33.33
    from backend.market_scan import items
    waiting=items(scan_id=job.id,offset=0,limit=30,state='awaiting_ai',db=db)['items']
    assert len(waiting)==1 and waiting[0]['status']=='awaiting_ai' and 'opinion_score' not in waiting[0]
