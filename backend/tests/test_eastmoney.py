import json
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from backend.database import Base
from backend.models import CollectionRun, Post, PostRevision, Snapshot, QuoteObservation, Classification
from backend.eastmoney import parse_titles, parse_bars, SourceError, IID, LIST_URL, KLINE_URL
from backend.collect import collect
from backend.live import live_analysis

NOW = datetime.now(timezone.utc)
CST = timezone(timedelta(hours=8))

def row(pid=1, **changes):
    return {'post_id': pid, 'stockbar_code': '300059', 'post_type': 0, 'post_state': 0,
        'user_id': str(pid), 'post_title': '看多，等待上涨',
        'post_publish_time': (NOW-timedelta(minutes=5)).astimezone(CST).strftime('%Y-%m-%d %H:%M:%S'),
        'post_last_time': '2099-01-01 12:00:00', 'post_click_count': 12, 'post_comment_count': 2, **changes}

def document(rows):
    return '<script>var article_list='+json.dumps({'rc': 1, 'bar_code': '300059', 're': rows})+';</script>'

def quotes():
    return json.dumps({'rc': 0, 'data': {'code': '300059', 'market': 0,
        'klines': [f'{NOW.astimezone(CST).date()},10,11,12,9,100,100000,0,0,0,0']}})

@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session: yield session
    engine.dispose()

def client_for(posts, price_status=200):
    def handler(request):
        if str(request.url) == LIST_URL: return httpx.Response(200, text=posts)
        assert str(request.url) == KLINE_URL
        return httpx.Response(price_status, text=quotes())
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_parser_uses_publication_not_last_update_and_filters_recommendations():
    old = (NOW-timedelta(days=2)).astimezone(CST).strftime('%Y-%m-%d %H:%M:%S')
    rows, stats = parse_titles(document([row(post_publish_time=old), row(2, stockbar_code='000001'),
        row(3, post_type=20), row(4, post_publish_time='2099-01-01 00:00:00'), row(1)]), NOW)
    assert rows[0]['published_at'].day == (NOW-timedelta(days=2)).astimezone(CST).day
    assert stats['accepted'] == 1 and stats['excluded'] == {'other_board':1,'nonordinary':1,'invalid':1,'duplicate_id':1}
    for invalid in ('captcha', 'var article_list={"rc":1,"bar_code":"000001","re":[]}'):
        with pytest.raises(SourceError): parse_titles(invalid, NOW)
    with pytest.raises(SourceError): parse_bars(quotes().replace('300059', '000001'), NOW)
    with pytest.raises(SourceError): parse_bars(quotes().replace('10,11,12,9', '10,11,8,9'), NOW)

def test_incremental_collection_revisions_and_point_in_time_isolation(db):
    first = collect(db, client_for(document([row()])))
    assert first['status'] == 'success'
    cutoff = datetime.now(timezone.utc)
    post = db.scalar(select(Post)); first_seen = post.first_seen_at
    a = live_analysis(db, days=3)
    assert a['counts']['bullish'] == 1 and a['opinion_score'] == 100
    assert a['quote']['bars'][0]['adjustment'] == 'none'
    assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.data_mode=='live')) == 1
    with pytest.raises(ValueError): collect(db, client_for(document([row()])))
    # Advance the run's rate-limit timestamp only in this in-memory test fixture.
    def allow_next():
        db.scalar(select(CollectionRun).order_by(CollectionRun.id.desc())).started_at -= timedelta(minutes=11)
        db.commit()
    allow_next()
    result = collect(db, client_for(document([row()])))
    assert result['details']['posts']['new_revisions'] == 0
    assert datetime.fromisoformat(live_analysis(db,3)['last_post_observation']) >= cutoff
    allow_next()
    result = collect(db, client_for(document([row(post_title='看空，下跌风险', post_comment_count=3)])))
    assert result['details']['posts']['new_revisions'] == 1
    assert db.scalar(select(func.count()).select_from(PostRevision)) == 2
    assert db.scalar(select(func.count()).select_from(Classification)) == 2
    assert post.first_seen_at == first_seen
    assert live_analysis(db,3)['counts']['bearish'] == 1
    assert live_analysis(db,3,as_of=cutoff)['counts']['bullish'] == 1
    assert db.scalar(select(func.count()).select_from(QuoteObservation)) == 3
    assert live_analysis(db,3,as_of=first_seen-timedelta(seconds=1))['effective_posts'] == 0

def test_partial_and_total_failure_preserve_status_without_demo_fill(db):
    result = collect(db, client_for(document([row(),row(2)]), price_status=403))
    assert result['status'] == 'partial'
    assert result['details']['quotes']['status'] == 'failed'
    a = live_analysis(db,3)
    assert a['quote'] is None and a['effective_posts'] == 1
    assert a['removed']['duplicate_content'] == 1
    db.scalar(select(CollectionRun)).started_at -= timedelta(minutes=11); db.commit()
    result = collect(db, client_for('verification page', price_status=429))
    assert result['status'] == 'failed'
    assert live_analysis(db,3)['effective_posts'] == 1 # old observations retained, failure visible
    assert live_analysis(db,3)['status']['latest_run']['status'] == 'failed'


def test_identity_challenge_is_recorded_as_access_requirement_not_parse_failure(db):
    page='<html><head><title>身份核实</title></head><body><script src="captcha.js"></script></body></html>'
    result=collect(db,client_for(page))
    posts=result['details']['posts']
    assert posts['status']=='failed' and posts['error_code']=='access_required'
    assert '验证码' in posts['error']
    assert live_analysis(db,3)['effective_posts']==0

def test_author_cap_and_empty_analysis(db):
    assert live_analysis(db)['effective_posts'] == 0
    assert live_analysis(db)['opinion_score'] is None
    assert live_analysis(db)['quote'] is None
    rows = [row(i, user_id='77', post_title=f'看多，第{i}次讨论') for i in range(1,6)]
    collect(db, client_for(document(rows)))
    assert live_analysis(db,3)['effective_posts'] == 3
    assert live_analysis(db,3)['removed']['author_cap'] == 2

def test_beijing_midnight_and_seeded_demo_do_not_pollute_real_sample(db):
    from backend.seed import seed
    # Publication is 00:05 CST; it belongs to that Beijing day even though UTC
    # is still the previous date. A first-seen time is never backdated.
    morning = datetime(2026, 1, 2, 0, 5, tzinfo=CST)
    rows, _ = parse_titles(document([row(post_publish_time=morning.strftime('%Y-%m-%d %H:%M:%S'))]), NOW)
    from backend.ingestion import record_post, classify_revision
    from backend.models import Instrument
    from backend.eastmoney import SOURCE
    db.add(Instrument(id=IID, code='300059', name='东方财富', kind='stock', exchange='XSHE')); db.flush()
    _, revision, _ = record_post(db, source=SOURCE, instrument_id=IID, **rows[0])
    classify_revision(db, revision); db.commit()
    stored = db.scalar(select(Post))
    assert stored.published_at.astimezone(timezone.utc).date().isoformat() == '2026-01-01'
    assert live_analysis(db, as_of=datetime(2026,1,2,8,tzinfo=timezone.utc))['sampled_posts'] == 0
    collect(db, client_for(document([row(2)])))
    before = live_analysis(db,3)['counts']
    seed(db)
    assert live_analysis(db,3)['counts'] == before
    assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.data_mode=='demo')) == 300

def test_beijing_window_uses_utc_storage_boundaries(db):
    from backend.models import Instrument
    from backend.ingestion import record_post, classify_revision
    from backend.eastmoney import SOURCE
    db.add(Instrument(id=IID, code='300059', name='东方财富', kind='stock', exchange='XSHE')); db.flush()
    day = NOW.astimezone(CST).date()
    before = datetime.combine(day, datetime.min.time(), CST) - timedelta(seconds=1)
    after = before + timedelta(seconds=2)
    # Run only with times already in the past (normal test execution is after 00:01).
    if after >= NOW: pytest.skip('midnight boundary not yet in the past')
    for pid, published in [(1, before),(2, after)]:
        _, revision, _ = record_post(db, source=SOURCE, instrument_id=IID, source_id=str(pid),
            author_id=str(pid), published_at=published, text=f'看多 {pid}')
        classify_revision(db,revision)
    db.commit()
    assert live_analysis(db,1)['sampled_posts'] == 1
    assert live_analysis(db,3)['sampled_posts'] == 2

def body_document(candidate, **changes):
    data = {**candidate, 'post_user': {'user_id': candidate['user_id']},
        'post_guba': {'stockbar_code': candidate['stockbar_code']},
        'post_content': '<p>看空，下跌风险</p>', 'post_title': '', **changes}
    return '<script>var post_article='+json.dumps(data)+';</script>'

def test_body_parser_rejects_identity_mismatch_and_preserves_plain_text():
    from backend.eastmoney import parse_body
    candidate, _ = parse_titles(document([row()]), NOW)
    result = parse_body(body_document(row(), post_content='<p>看空</p><script>看多</script><p>下跌风险</p>'), NOW, candidate[0])
    assert result['text'] == '看空\n\n下跌风险' and '看多' not in result['text']
    assert result['engagement']['content_scope'] == 'body'
    prefixed = parse_body(body_document(row(),post_title='看空风险',post_content='<p>看空风险</p><p>下跌</p>'),NOW,candidate[0])
    assert prefixed['text'].count('看空风险')==1
    for changes in ({'post_id':2}, {'post_guba':{'stockbar_code':'000001'}},
        {'post_user':{'user_id':'999'}}, {'post_type':20}, {'post_content':''},
        {'post_publish_time':'2000-01-01 00:00:00'}):
        with pytest.raises(SourceError): parse_body(body_document(row(), **changes), NOW, candidate[0])

def test_multistock_body_isolation_no_title_fallback_and_source_status(db, monkeypatch):
    from backend.eastmoney import list_url, kline_url, detail_url
    from backend.live import source_status
    monkeypatch.setattr('backend.collect.time.sleep', lambda _: None)
    # Legacy 300059 title samples coexist with newer body samples.
    collect(db, client_for(document([row()])))
    iid='XSHE:000001'
    r1=row(101,stockbar_code='000001',post_title='看多')
    r2=row(102,stockbar_code='000001',post_title='上涨')
    requested=[]
    def handler(req):
        requested.append(str(req.url))
        if str(req.url)==list_url(iid): return httpx.Response(200,text=document([r1,r2]).replace('"bar_code": "300059"','"bar_code": "000001"'))
        if str(req.url)==kline_url(iid): return httpx.Response(200,text=quotes().replace('300059','000001'))
        if str(req.url)==detail_url(iid,102): return httpx.Response(200,text=body_document(r2))
        if str(req.url)==detail_url(iid,101): return httpx.Response(403)
        raise AssertionError(str(req.url))
    result=collect(db,httpx.Client(transport=httpx.MockTransport(handler)),iid,'body',3)
    assert result['status']=='partial' and result['details']['posts']['accepted']==1
    assert result['details']['posts']['status']=='partial'
    a=live_analysis(db,3,iid=iid,content='body')
    assert a['counts']['bearish']==1 and a['counts']['bullish']==0
    assert a['examples'][0]['source_id']=='102' and a['examples'][0]['excerpt']=='看空，下跌风险'
    assert a['quote']['bars']
    assert live_analysis(db,3,iid=iid,content='titles')['effective_posts']==0
    assert source_status(db,iid,'titles')['latest_run'] is None
    assert source_status(db,iid,'body')['latest_run']['status']=='partial'
    assert live_analysis(db,3)['counts']['bullish']==1
    assert live_analysis(db,3,content='body')['effective_posts']==0
    assert len(requested)==4

def test_shanghai_stock_market_and_url_allowlist(db):
    from backend.eastmoney import parse_bars, get_document, kline_url, list_url, detail_url
    iid='XSHG:600519'
    assert 'secid=1.600519' in kline_url(iid)
    assert parse_bars(quotes().replace('300059','600519').replace('"market": 0','"market": 1'),NOW,iid)
    with pytest.raises(SourceError): parse_bars(quotes().replace('300059','600519'),NOW,iid)
    calls=[]
    client=httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(302,headers={'Location':'http://localhost/'})))
    for url in ('http://localhost/', detail_url(iid,1)+'?redirect=evil',list_url(iid).replace('https:','http:')):
        with pytest.raises(SourceError): get_document(client,url)
    assert not calls
    with pytest.raises(SourceError): get_document(client,list_url(iid))
    assert len(calls)==1
    with pytest.raises(ValueError): collect(db,client,'XSHG:000001')
    assert len(calls)==1

def test_body_failure_saves_no_title_and_stops_at_first_rejection(db, monkeypatch):
    from backend.eastmoney import detail_url
    monkeypatch.setattr('backend.collect.time.sleep',lambda _: None)
    requests=[]
    rows=[row(i) for i in range(1,5)]
    def handler(req):
        requests.append(str(req.url))
        if str(req.url)==LIST_URL: return httpx.Response(200,text=document(rows))
        if str(req.url)==KLINE_URL: return httpx.Response(200,text=quotes())
        assert str(req.url)==detail_url(IID,4)
        return httpx.Response(429)
    result=collect(db,httpx.Client(transport=httpx.MockTransport(handler)),content='body',limit=5)
    assert result['details']['posts']['status']=='failed'
    assert result['details']['posts']['not_attempted']==3
    assert live_analysis(db,3,content='body')['sampled_posts']==0
    assert live_analysis(db,3)['sampled_posts']==0
    assert len(requests)==3

def test_detail_only_reposts_are_excluded_without_aborting_other_candidates(db, monkeypatch):
    from backend.eastmoney import detail_url
    monkeypatch.setattr('backend.collect.time.sleep',lambda _:None)
    def handler(req):
        if str(req.url)==LIST_URL: return httpx.Response(200,text=document([row(1),row(2)]))
        if str(req.url)==KLINE_URL: return httpx.Response(200,text=quotes())
        if str(req.url)==detail_url(IID,2): return httpx.Response(200,text=body_document(row(2),source_post_id=999))
        assert str(req.url)==detail_url(IID,1)
        return httpx.Response(200,text=body_document(row(1)))
    result=collect(db,httpx.Client(transport=httpx.MockTransport(handler)),content='body')
    assert result['status']=='success'
    assert len(result['details']['posts']['detail_excluded'])==1
    assert result['details']['posts']['accepted']==1
    assert result['details']['posts']['not_attempted']==0
    assert live_analysis(db,3,content='body')['counts']['bearish']==1
