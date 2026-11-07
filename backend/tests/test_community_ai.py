import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import Session
from backend.database import Base
from backend.models import Instrument,CommunityProbe,CommunityAnalysis,CollectionRun,ScanItem,MarketScan,UniverseSync,ScanMethod
from backend.community import parse_page,probe,source_status
from backend.sentiment_ai import analyze_probe,configuration,prepare,settings
from backend.tests.test_api import client


@pytest.fixture
def db(tmp_path,monkeypatch):
    import backend.scanner as scanner
    import backend.collect as collector
    monkeypatch.setattr(scanner,'DATA_DIR',tmp_path)
    monkeypatch.setattr(collector,'DATA_DIR',tmp_path)
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as db:
        db.add(Instrument(id='XSHG:600519',code='600519',name='贵州茅台',kind='stock',exchange='XSHG',scope={'live_directory':True}));db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def ai_config(monkeypatch,tmp_path):
    import backend.sentiment_ai as ai
    path=tmp_path/'private.env';path.write_text('SENTIMENT_AI_API_KEY=local-test-key\n',encoding='utf-8')
    monkeypatch.setattr(ai,'ENV_FILE',path)
    for name in ('SENTIMENT_AI_API_KEY','SENTIMENT_AI_MODEL','SENTIMENT_AI_BASE_URL','SENTIMENT_AI_DAILY_CALL_LIMIT'):monkeypatch.delenv(name,raising=False)
    return path


def sina(code='600519'):
    return f'''<html><script>var PAGE_CONFIG={{"stockCode":"{code}","stockMarket":"sh"}};</script>
    <article class="j-post" data-bid="240" data-tid="123" data-uid="456"><time class="time">12:39</time><div class="title">贵州茅台看涨</div><div class="content">看好公司</div></article></html>'''


def observation(db,count=1):
    now=datetime.now(timezone.utc)
    samples=[{'id':str(i),'author_hash':str(i),'text':f'贵州茅台观点{i}','url':f'https://guba.sina.cn/view_240_{i}.html','published_at':None} for i in range(count)]
    row=CommunityProbe(source='sina',instrument_id='XSHG:600519',status='samples',http_status=200,started_at=now,finished_at=now,samples=samples)
    db.add(row);db.commit();return row


def response_for(request,label='bullish'):
    body=json.loads(request.content)
    posts=json.loads(body['messages'][1]['content'])['posts']
    assert request.headers['Authorization']=='Bearer local-test-key'
    assert 'author_hash' not in body['messages'][1]['content']
    assert body['response_format']=={'type':'json_object'}
    rows=[{'id':p['id'],'label':label,'confidence':.9,'relevant':True,'advertising':False,'tags':['贪婪'],'reason':'表达看好'} for p in posts]
    return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'items':rows})}}],'usage':{'total_tokens':300}})


def test_parse_identity_time_and_no_script_execution():
    samples,_=parse_page('sina',sina(),'XSHG:600519')
    assert len(samples)==1 and samples[0]['source_time']=='12:39' and samples[0]['published_at'] is None
    assert 'script' not in samples[0]['text']
    with pytest.raises(ValueError):parse_page('sina',sina('600900'),'XSHG:600519')
    with pytest.raises(ValueError):parse_page('oops',sina(),'XSHG:600519')
    with pytest.raises(ValueError):parse_page('sina','<script>var PAGE_CONFIG=run();</script>','XSHG:600519')


def test_taoguba_excludes_replies_and_preserves_activity_time():
    row={'rType':'T','deleteFlag':'N','rID':123,'userID':456,'newTopicID':'abc123','subject':'茅台看涨','body':'正文','actionDate':'2026-11-07T08:00:00+08:00'}
    document='var stockInfo={"code":"600519","fullCode":"sh600519"};var coolAttr='+json.dumps([row,{**row,'rType':'R'}])+';'
    posts,meta=parse_page('taoguba',document,'XSHG:600519')
    assert len(posts)==1 and posts[0]['published_at'] is None and meta['excluded']['reply_or_other']==1
    beijing=document.replace('"code":"600519","fullCode":"sh600519"','"code":"bj920002","fullCode":"bj920002"')
    assert len(parse_page('taoguba',beijing,'XBSE:920002')[0])==1


def test_gbk_probe_reuse_and_counters_do_not_pollute_eastmoney(db):
    calls=[]
    def source(request):calls.append(request.url);return httpx.Response(200,content=sina().encode('gbk'),headers={'content-type':'text/html; charset=gbk'})
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        first,reused=probe(db,client,'sina','XSHG:600519')
        second,reused=probe(db,client,'sina','XSHG:600519')
    assert first.status=='samples' and reused and first.id==second.id and len(calls)==1
    assert db.scalar(select(func.count()).select_from(CollectionRun))==0
    status=source_status(db,'XSHG:600519')['sources'][0]
    assert status['attempts']==1 and status['sample_success_rate']==100 and status['covered_stocks']==1
    assert 'author_hash' not in status['latest']['samples'][0]


def test_blocked_source_is_not_followed_or_counted_as_sample(db):
    calls=[]
    def source(request):calls.append(request.url);return httpx.Response(302,headers={'Location':'https://untrusted.example/'})
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        row,_=probe(db,client,'sina','XSHG:600519')
    assert row.status=='blocked' and len(calls)==1
    status=source_status(db,'XSHG:600519')['sources'][0]
    assert status['reachable_rate']==0 and status['sample_success_rate']==0


def test_ths_empty_entry_is_not_zero_sentiment():
    posts,_=parse_page('ths','<input id="globalParam" data-code="600519"/>','XSHG:600519')
    assert posts==[]


def test_classifier_strict_ids_cache_budget_and_no_secret_disclosure(db,ai_config):
    row=observation(db,22);calls=[]
    def handler(request):calls.append(request);return response_for(request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result,reused=analyze_probe(db,client,row)
        repeat,reused=analyze_probe(db,client,row)
    assert reused and repeat.id==result.id and len(calls)==1
    assert result.result['effective_posts']==22 and result.result['eligible'] and result.result['opinion_score']==100
    assert configuration(db)['calls_today']==1
    assert 'local-test-key' not in json.dumps(result.result) and 'local-test-key' not in json.dumps(configuration(db))


@pytest.mark.parametrize('content,finish',[('', 'stop'),('{"items":[]}','stop'),('{"items":[]}','length'),('{"items":[{"id":"invented"}]}','stop')])
def test_bad_model_output_never_publishes_or_overwrites(db,ai_config,content,finish):
    row=observation(db)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'finish_reason':finish,'message':{'content':content}}]}))) as client:
        result,_=analyze_probe(db,client,row)
    assert result.status=='invalid_response' and 'counts' not in result.result


def test_auth_failure_backoff_and_daily_limit(db,ai_config,monkeypatch):
    row=observation(db)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='sensitive failure'))) as client:
        result,_=analyze_probe(db,client,row)
        assert result.status=='auth_failed' and 'sensitive' not in json.dumps(result.result)
        with pytest.raises(ValueError,match='暂停'):analyze_probe(db,client,row)
    monkeypatch.setenv('SENTIMENT_AI_DAILY_CALL_LIMIT','0')
    result.created_at=datetime.now(timezone.utc)-timedelta(days=1);db.commit()
    with pytest.raises(ValueError,match='上限'):analyze_probe(db,None,row)


def test_quality_excludes_duplicate_ad_flood_and_low_confidence(db,ai_config):
    row=observation(db,6)
    rows=row.samples
    rows[1]['text']=rows[0]['text']
    rows[2]['text']='加微信进群领取免费荐股'
    for s in rows:s['author_hash']='same'
    row.samples=rows;db.commit()
    samples,excluded,floods=prepare(rows)
    assert len(samples)==3 and excluded=={'duplicate':1,'advertising_rule':1,'author_cap':1} and floods==1
    with httpx.Client(transport=httpx.MockTransport(response_for)) as client:result,_=analyze_probe(db,client,row)
    assert result.result['effective_posts']==3 and not result.result['eligible']


def test_h_share_opinion_is_not_assigned_to_a_share_even_for_same_company():
    rows=[{'id':'1','author_hash':'1','text':'$中芯国际(00981)$跌穿41，升穿49才转强'},
        {'id':'2','author_hash':'2','text':'$中芯国际(SH688981)$ A股继续看多'}]
    kept,excluded,_=prepare(rows,'XSHG:688981')
    assert [r['id'] for r in kept]==['2'] and excluded=={'different_listing':1}


def test_local_file_reloaded_and_external_key_destination_rejected(ai_config):
    assert settings()['key']=='local-test-key'
    ai_config.write_text('SENTIMENT_AI_API_KEY=reloaded-key\nSENTIMENT_AI_BASE_URL=https://evil.example\n',encoding='utf-8')
    with pytest.raises(ValueError,match='官方'):settings()


def test_remote_api_cannot_call_model_or_change_source(client,monkeypatch):
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION','true')
    assert client.post('/api/live/community/probe',json={'source':'sina','instrument_id':'XSHG:600519'}).status_code==403
    assert client.post('/api/live/community/analyze',json={'probe_id':1}).status_code==403
    assert client.get('/api/live/community/ai').json()['configured'] is False


def test_retry_is_bounded_and_persistent_never_for_access_denial():
    from backend.scanner import schedule_retry
    now=datetime.now(timezone.utc)
    item=ScanItem(status='failed',result={'error':'network','error_code':'network'})
    schedule_retry(item,now)
    assert item.status=='pending' and len(item.result['retry_history'])==1
    item.status='failed';schedule_retry(item,now)
    assert len(item.result['retry_history'])==2
    item.status='failed';schedule_retry(item,now)
    assert item.status=='failed'
    blocked=ScanItem(status='failed',result={'error':'denied','error_code':'access_required'})
    schedule_retry(blocked,now);assert blocked.status=='failed' and 'next_retry_at' not in blocked.result


def test_scan_method_is_frozen_and_cannot_mix_sources(db,ai_config):
    from backend.scanner import queue_scan,method_config
    directory=UniverseSync(status='success',total=1,members=['XSHG:600519'])
    db.add(directory);db.commit()
    method=method_config('sina','ai');job=queue_scan(db,method=method)
    assert db.get(ScanMethod,job.id).config==method
    with pytest.raises(ValueError,match='不能混用'):queue_scan(db,method=method_config('taoguba','ai'))


def test_community_scan_uses_its_own_observation_and_classifier(db,ai_config,monkeypatch):
    import backend.community as community
    from backend.scanner import queue_scan,method_config,process_item
    row=observation(db,22)
    db.add(UniverseSync(status='success',total=1,members=['XSHG:600519']));db.commit()
    job=queue_scan(db,method=method_config('sina','ai'));item=db.scalar(select(ScanItem))
    monkeypatch.setattr(community,'probe',lambda *a:(row,True))
    with httpx.Client(transport=httpx.MockTransport(response_for)) as client:assert process_item(db,job,item,client)
    assert item.status=='eligible' and item.result['source']=='sina' and item.result['model_version']=='deepseek-flash'
    assert db.scalar(select(func.count()).select_from(CollectionRun))==0


def test_report_only_compares_same_method_and_does_not_invent_market_index(db,ai_config):
    from backend.scanner import queue_scan,method_config
    from backend.market_scan import report
    db.add(UniverseSync(status='success',total=1,members=['XSHG:600519']));db.commit()
    method=method_config('sina','ai')
    first=queue_scan(db,method=method);first.status='completed';first.active_key=None
    item=db.scalar(select(ScanItem).where(ScanItem.scan_id==first.id));item.status='eligible';item.result={'id':item.instrument_id,'opinion_score':30,'effective_posts':22}
    db.commit()
    second=queue_scan(db,method=method)
    item=db.scalar(select(ScanItem).where(ScanItem.scan_id==second.id));item.status='eligible';item.result={'id':item.instrument_id,'opinion_score':95,'effective_posts':22}
    db.commit()
    result=report(scan_id=second.id,db=db)
    assert result['mean_opinion_score']==95 and result['comparison_scan_id']==first.id
    assert result['changes'][0]['change']==65 and result['extremes'][0]['signal']=='方向样本极端偏多'
    db.get(ScanMethod,first.id).config=method_config('taoguba','ai');db.commit()
    result=report(scan_id=second.id,db=db)
    assert result['changes']==[] and result['comparison_scan_id'] is None


def test_collector_lock_recovers_after_abrupt_process_exit(tmp_path,monkeypatch):
    import subprocess,sys
    import backend.collect as collector
    script='\n'.join(['import os','from pathlib import Path','import backend.collect as collector',
        'collector.DATA_DIR=Path('+repr(str(tmp_path))+')','with collector.collector_lock():','    os._exit(0)'])
    subprocess.run([sys.executable,'-c',script],check=True,timeout=10)
    assert (tmp_path/'collector.lock').exists() # simulated crash leaves only a marker
    monkeypatch.setattr(collector,'DATA_DIR',tmp_path)
    assert not collector.collector_busy()
    assert not (tmp_path/'collector.lock').exists()
    with collector.collector_lock():assert collector.collector_busy()


def test_completed_report_is_immutable_and_retry_completion_appends(db):
    from backend.scanner import queue_scan
    from backend.market_scan import report,save_report
    from backend.models import MarketReport
    db.add(UniverseSync(status='success',total=1,members=['XSHG:600519']));db.commit()
    job=queue_scan(db);job.status='completed';job.active_key=None
    item=db.scalar(select(ScanItem));item.status='eligible';item.result={'id':item.instrument_id,'opinion_score':90,'effective_posts':22};db.commit()
    first=save_report(db,job)
    item.result={**item.result,'opinion_score':60};db.commit()
    assert report(scan_id=job.id,db=db)['mean_opinion_score']==90
    second=save_report(db,job)
    assert report(scan_id=job.id,db=db)['mean_opinion_score']==60
    assert first.id!=second.id and db.get(MarketReport,first.id).result['mean_opinion_score']==90


def test_daily_plan_resumes_budget_pause_but_never_access_denial(db,ai_config,monkeypatch):
    import backend.scanner as scanner
    import backend.sentiment_ai as ai
    from backend.models import ScanSchedule
    db.add(UniverseSync(status='success',total=1,members=['XSHG:600519']));db.add(ScanSchedule(id=1,enabled=1,hour=0,minute=0));db.commit()
    job=scanner.queue_scan(db,method=scanner.method_config('sina','ai'))
    job.status='paused';job.error='已达到今日模型调用上限';db.commit()
    monkeypatch.setattr(ai,'configuration',lambda db:{'configured':True,'cooldown':False,'calls_today':0,'daily_call_limit':50})
    worker=scanner.ScanWorker();worker.scheduled(db)
    assert job.status=='queued'
    job.status='paused';job.error='源站要求验证';db.commit()
    scanner.ScanWorker().scheduled(db)
    assert job.status=='paused'
