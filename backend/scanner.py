"""Durable, paced public-title scanning. The worker never invents missing results."""
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from .database import SessionLocal, DATA_DIR
from .models import UniverseSync, MarketScan, ScanItem, ScanSchedule, CollectionRun,ScanMethod,CommunitySchedule
from .collect import collect, collector_lock
from .eastmoney import default_client, CST, SOURCE
from .live import live_analysis
from .research import utc
from .universe import sync_universe


@contextmanager
def process_lock(name,wait_seconds=0):
    """OS releases this advisory lock on crash; the file itself can remain."""
    handle = (DATA_DIR / name).open('a+b')
    until=time.monotonic()+wait_seconds
    while True:
        try:
            handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic()<until:time.sleep(.1);continue
            handle.close()
            raise ValueError('任务正在其他进程运行，请等待。')
    try:
        yield
    finally:
        handle.close()


def method_config(source='eastmoney',mode='rules'):
    if source not in ('eastmoney','sina','taoguba'):raise ValueError('当前可扫描来源为东方财富、新浪或淘股吧；同花顺仅验证入口')
    if mode not in ('rules','ai') or (source=='eastmoney' and mode!='rules'):raise ValueError('东方财富保留原规则口径；AI扫描请先选择已验证的新来源')
    from .sentiment_ai import settings,PROMPT_VERSION,RULE_VERSION
    model=settings()['model'] if mode=='ai' else 'rules-v1' if source=='eastmoney' else RULE_VERSION
    return {'source':source,'mode':mode,'model':model,'version':PROMPT_VERSION if mode=='ai' else model,
        'basis':'publication' if source=='eastmoney' else 'observation'}


def scan_method(db,job):
    row=db.get(ScanMethod,job.id)
    return row.config if row else method_config()


def next_pending_item(db,job,excluded=()):
    """Interleave exchanges using persisted progress, including pre-upgrade jobs."""
    done=dict(db.execute(select(func.substr(ScanItem.instrument_id,1,4),func.count()).where(
        ScanItem.scan_id==job.id,ScanItem.status!='pending').group_by(func.substr(ScanItem.instrument_id,1,4))).all())
    now=datetime.now(timezone.utc).isoformat()
    for exchange in sorted(('XSHG','XSHE','XBSE'),key=lambda name:done.get(name,0)):
        row=db.scalar(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='pending',
            ScanItem.instrument_id.startswith(exchange+':'),ScanItem.id.not_in(excluded),
            or_(ScanItem.result['next_retry_at'].as_string().is_(None),ScanItem.result['next_retry_at'].as_string()<=now)).order_by(ScanItem.id))
        if row:return row
    return None


def ai_available(db):
    from .sentiment_ai import configuration
    state=configuration(db)
    return bool(state.get('configured') and not state.get('cooldown') and
                state.get('calls_today',0)<state.get('daily_call_limit',0))


def queue_scan(db, scheduled_date=None,method=None):
    active = db.scalar(select(MarketScan).where(MarketScan.active_key == 'active'))
    if active:
        if method and method!=scan_method(db,active):raise ValueError('请先停止当前批次，再切换来源或模型；同一批次不能混用口径')
        return active
    universe = db.scalar(select(UniverseSync).where(UniverseSync.status == 'success').order_by(UniverseSync.id.desc()))
    if not universe: raise ValueError('请先同步完整A股目录。')
    if scheduled_date:
        existing = db.scalar(select(MarketScan).where(MarketScan.scheduled_date == scheduled_date))
        if existing: return existing
    job = MarketScan(universe_id=universe.id, active_key='active', scheduled_date=scheduled_date,
                     total=universe.total, days=3, status='queued')
    try:
        db.add(job); db.flush()
        if method:db.add(ScanMethod(scan_id=job.id,config=method))
        db.add_all(ScanItem(scan_id=job.id, instrument_id=iid) for iid in universe.members)
        db.commit()
    except IntegrityError:
        db.rollback()
        job = db.scalar(select(MarketScan).where(MarketScan.active_key == 'active'))
        if not job: raise ValueError('扫描已被其他请求创建，请刷新。')
    return job


def summarize(data):
    return {'id':data['instrument_id'], 'code':data['code'], 'name':data['name'],
            'counts':data['counts'], 'percentages':data['percentages'],
            'effective_posts':data['effective_posts'], 'authors':data['authors'],
            'opinion_score':data['opinion_score'], 'observed_at':data['last_post_observation'],
            'eligible':not data['interpretation']['quality_reasons'],
            'reasons':data['interpretation']['quality_reasons'], 'model_version':'rules-v1'}


def process_item(db, job, item, client):
    """Called under collector_lock; cooldown is shared with interactive sampling."""
    now = datetime.now(timezone.utc)
    retry_at=item.result.get('next_retry_at')
    if retry_at and datetime.fromisoformat(retry_at)>now:return False
    method=scan_method(db,job)
    if method['source']!='eastmoney':return process_community_item(db,job,item,client,method)
    latest = db.scalar(select(CollectionRun).where(CollectionRun.instrument_id == item.instrument_id)
                       .order_by(CollectionRun.id.desc()))
    if latest and now - utc(latest.started_at) < timedelta(minutes=10):
        posts = latest.details.get('posts', {})
        if latest.details.get('_meta', {}).get('source') != SOURCE or posts.get('status') != 'ok':
            # A body-only or failed interactive request cannot stand in for title collection.
            return False
        run = {'details': latest.details}
    else:
        run = collect(db, client, item.instrument_id, fetch_quotes=False)
    previous_attempts = item.result.get('retry_history',[])
    error = run['details'].get('posts', {}).get('error')
    if run['details'].get('posts', {}).get('status') != 'ok':
        item.status, item.result = 'failed', {'error':error or '股吧采样未成功', 'id':item.instrument_id,
                                            'error_code':run['details'].get('posts',{}).get('error_code','unknown')}
    else:
        data = live_analysis(db, days=job.days, iid=item.instrument_id)
        item.result = summarize(data)
        item.status = 'eligible' if item.result['eligible'] else 'insufficient'
    item.observed_at = now
    if previous_attempts: item.result={**item.result,'retry_history':previous_attempts}
    if item.status=='failed':schedule_retry(item,now)
    db.commit()
    return True


def schedule_retry(item,now):
    """At most two transient retries, persisted across restarts; never barriers."""
    history=item.result.get('retry_history',[])
    code=item.result.get('error_code')
    transient=code in ('network','http_transient','model_network','model_format')
    count=sum(r.get('automatic',False) for r in history)
    if transient and count<(1 if code=='model_format' else 2):
        item.result={**item.result,'retry_history':[*history,{'automatic':True,'error':item.result['error'],
            'observed_at':now.isoformat()}],'next_retry_at':(now+timedelta(minutes=11*(count+1))).isoformat()}
        item.status='awaiting_ai' if code.startswith('model_') and item.result.get('probe_id') else 'pending'


def process_community_item(db,job,item,client,method):
    from .community import probe
    from .sentiment_ai import analyze_probe,prepare,configuration
    from .models import Instrument,CommunityProbe,CommunityAnalysis
    now=datetime.now(timezone.utc);history=item.result.get('retry_history',[])
    try:
        if method!=method_config(method['source'],method['mode']):raise ValueError('分类引擎版本已更新，请停止旧批次并创建新批次，避免混用模型或提示词')
        observation=db.get(CommunityProbe,item.result.get('probe_id')) if item.status=='awaiting_ai' else None
        if observation and (observation.source!=method['source'] or observation.instrument_id!=item.instrument_id):
            raise ValueError('待分类样本与批次证券或来源不一致，请检查数据记录')
        if observation and (not observation.finished_at or now-utc(observation.finished_at)>timedelta(hours=24)):
            # Old snapshots remain immutable; refresh before any new model spend.
            observation=None
        if observation is None:
            with collector_lock():observation,reused=probe(db,client,method['source'],item.instrument_id)
        else:reused=False
        if reused and observation.status not in ('samples','entry_only','blocked'):return False
        if observation.status=='samples':
            candidates,_,_=prepare(observation.samples,item.instrument_id)
            authors=len({row['author_hash'] for row in candidates})
            security=db.get(Instrument,item.instrument_id)
            collected={'id':item.instrument_id,'code':security.code,'name':security.name,'source':method['source'],
                'probe_id':observation.id,'collection_status':'samples','raw_posts':len(observation.samples),
                'candidate_posts':len(candidates),'candidate_authors':authors,'observed_at':utc(observation.finished_at).isoformat()}
            if method['mode']=='ai' and (len(candidates)<20 or authors<10):
                reasons=[]
                if len(candidates)<20:reasons.append('分类前可用样本不足20条，暂不消耗扫描AI额度')
                if authors<10:reasons.append('分类前作者不足10位，暂不消耗扫描AI额度')
                item.status='insufficient';item.observed_at=now
                item.result={**collected,'reasons':reasons,'classification_status':'not_requested',
                    'retry_history':history};db.commit();return True
            cached=db.scalar(select(CommunityAnalysis.id).where(CommunityAnalysis.probe_id==observation.id,
                CommunityAnalysis.model==method['model'],CommunityAnalysis.prompt_version==method['version'],
                CommunityAnalysis.status=='success').limit(1)) if method['mode']=='ai' else None
            if method['mode']=='ai' and not cached and not ai_available(db):
                state=configuration(db)
                item.status='awaiting_ai';item.observed_at=now
                item.result={**collected,'classification_status':'waiting',
                    'waiting_reason':state.get('error') or '等待模型额度恢复','retry_history':history}
                db.commit();return True
            try:
                with process_lock('sentiment-ai.lock'):
                    analysis,_=analyze_probe(db,client,observation,method['mode'],method['model'])
            except ValueError as exc:
                if '其他进程' in str(exc):return False
                if method['mode']=='ai' and not ai_available(db):
                    item.status='awaiting_ai';item.observed_at=now
                    item.result={**collected,'classification_status':'waiting','waiting_reason':str(exc),'retry_history':history}
                    db.commit();return True
                raise
            if analysis.status=='success':
                item.result={k:v for k,v in analysis.result.items() if k!='examples'}
                item.result.update({**collected,
                    'model_version':analysis.model,'prompt_version':analysis.prompt_version,'analysis_id':analysis.id,
                    'classification_status':'success'})
                item.status='eligible' if item.result['eligible'] else 'insufficient'
            else:
                code='model_network' if analysis.status=='network_failed' else 'model_access' if analysis.status in ('auth_failed','balance_failed','rate_limited') else 'model_format'
                item.status='failed';item.result={**collected,'classification_status':'failed','error':analysis.result.get('error','模型分类失败'),'error_code':code}
                if code=='model_access':
                    item.status='awaiting_ai';item.result={**item.result,'classification_status':'waiting','waiting_reason':'模型服务暂停；采集继续，等待配置或额度恢复'}
        elif observation.status=='entry_only':
            item.status='insufficient';item.result={'id':item.instrument_id,'probe_id':observation.id,'collection_status':'entry_only','reasons':['静态页面没有可核验帖子，不能认定该股无人讨论']}
        else:
            code='access_required' if observation.status=='blocked' else 'network' if observation.status=='network_failed' else 'http_transient' if observation.http_status and observation.http_status>=500 else 'source_unavailable' if observation.http_status in (404,410) else 'source_format'
            item.status='failed';item.result={'id':item.instrument_id,'probe_id':observation.id,'collection_status':observation.status,'error':observation.details.get('error','来源未提供样本'),'error_code':code}
    except ValueError as exc:
        # Configuration/budget/source cooldown needs attention, not repeated requests.
        if '数据采集正在运行' in str(exc) or '其他进程' in str(exc):return False
        job.status='paused';job.error=str(exc);db.commit()
        if '今日模型调用上限' in str(exc):
            from .market_scan import save_report
            save_report(db,job)
        return False
    item.observed_at=now
    if history:item.result={**item.result,'retry_history':history}
    if item.status=='failed':schedule_retry(item,now)
    db.commit();return True


def finish_collection(db,job):
    """Collection completion must not misrepresent unclassified samples as scored."""
    if db.scalar(select(ScanItem.id).where(ScanItem.scan_id==job.id,ScanItem.status=='pending').limit(1)):
        return False
    waiting=db.scalar(select(ScanItem.id).where(ScanItem.scan_id==job.id,ScanItem.status=='awaiting_ai').limit(1))
    if waiting:
        job.status='waiting_ai';job.error='本批采样已结束，仍有AI待处理；额度或配置恢复后继续，不混入规则结果。'
    else:
        job.status='completed';job.error='';job.finished_at=datetime.now(timezone.utc)
    job.active_key=None;db.commit()
    from .market_scan import save_report
    save_report(db,job)
    return True


class ScanWorker:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = None
        self.last_schedule_attempt = None
        self.ready = False

    def start(self):
        if os.getenv('ENABLE_MARKET_SCANNER','').lower() != 'true': return
        if self.thread and self.thread.is_alive(): return
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, daemon=True, name='market-scan')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=20)

    def scheduled(self, db):
        config = db.get(ScanSchedule,1)
        local = datetime.now(CST)
        day = local.date().isoformat()
        if not config or not config.enabled or (local.hour,local.minute) < (config.hour,config.minute): return
        if self.last_schedule_attempt == day: return
        active=db.scalar(select(MarketScan).where(MarketScan.active_key=='active'))
        if active and active.status=='paused' and '今日模型调用上限' in active.error:
            from .sentiment_ai import configuration
            state=configuration(db)
            if state.get('configured') and not state.get('cooldown') and state['calls_today']<state['daily_call_limit']:
                active.status='queued';active.error='';db.commit();self.last_schedule_attempt=day
            return
        if db.scalar(select(MarketScan.id).where(MarketScan.scheduled_date == day)): return
        midnight=local.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
        if db.scalar(select(MarketScan.id).where(MarketScan.created_at>=midnight)):
            # A manually started scan already covers today's scheduled run.
            self.last_schedule_attempt=day
            return
        if db.scalar(select(MarketScan.id).where(MarketScan.active_key == 'active')): return
        self.last_schedule_attempt = day  # No repeated source requests on a failed day.
        with process_lock('directory-worker.lock'):
            directory = sync_universe(db, pause=lambda n:self.stop.wait(n))
        method=db.get(CommunitySchedule,1)
        if directory.status == 'success' and not self.stop.is_set(): queue_scan(db, day,method.config if method else None)

    def run(self):
        try:
            with process_lock('market-worker.lock'), default_client() as client:
                self.ready = True
                failures = 0
                cooldowns = {}
                while not self.stop.is_set():
                    try:
                        with SessionLocal() as db:
                            self.scheduled(db)
                            job = db.scalar(select(MarketScan).where(MarketScan.active_key == 'active'))
                            if not job:
                                waiting=db.scalar(select(MarketScan).where(MarketScan.status=='waiting_ai').order_by(MarketScan.id.desc()))
                                if waiting and ai_available(db):
                                    job=waiting;job.active_key='active';job.status='queued';job.error='';db.commit()
                            if not job or job.status == 'paused':
                                self.stop.wait(5); continue
                            if job.cancel_requested:
                                job.status,job.active_key,job.finished_at = 'cancelled',None,datetime.now(timezone.utc)
                                db.commit(); continue
                            job.status = 'running'; db.commit()
                            cooldowns = {key:until for key,until in cooldowns.items() if until > time.monotonic()}
                            item = next_pending_item(db,job,cooldowns)
                            if not item:
                                remaining = db.scalar(select(func.count()).select_from(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='pending'))
                                if remaining:
                                    self.stop.wait(5); continue
                                if ai_available(db):
                                    now=datetime.now(timezone.utc).isoformat()
                                    item=db.scalar(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='awaiting_ai',
                                        ScanItem.id.not_in(cooldowns),or_(ScanItem.result['next_retry_at'].as_string().is_(None),
                                            ScanItem.result['next_retry_at'].as_string()<=now)).order_by(ScanItem.id))
                                    if not item and db.scalar(select(ScanItem.id).where(ScanItem.scan_id==job.id,ScanItem.status=='awaiting_ai').limit(1)):
                                        self.stop.wait(5);continue
                                if not item:
                                    finish_collection(db,job)
                                    continue
                            try:
                                if scan_method(db,job)['source']=='eastmoney':
                                    with collector_lock():processed=process_item(db,job,item,client)
                                else:processed=process_item(db,job,item,client)
                            except ValueError:
                                self.stop.wait(2); continue
                            if not processed:
                                # Let other securities advance while this shared cooldown expires.
                                cooldowns[item.id] = time.monotonic()+30
                                self.stop.wait(2); continue
                            code=item.result.get('error_code','')
                            failures = failures+1 if item.status=='failed' and not code.startswith('model_') and code!='source_unavailable' else 0
                            pause_reason = source_pause_reason(item.result,failures) if item.status=='failed' else None
                            if pause_reason:
                                job.status='paused'; job.error=pause_reason
                                db.commit(); failures=0
                        self.stop.wait(5)
                    except Exception:
                        # Persist diagnostic without raw response text or credentials.
                        with SessionLocal() as db:
                            job = db.scalar(select(MarketScan).where(MarketScan.active_key=='active'))
                            if job:
                                job.status='paused'; job.error='扫描内部处理失败，进度已保留；请检查服务日志后继续。'; db.commit()
                        self.stop.wait(5)
        except ValueError:
            pass  # Another API process owns the one worker.
        finally:
            self.ready = False


def source_pause_reason(result, consecutive):
    code,error=result.get('error_code'),result.get('error','')
    if code=='source_unavailable':return None # Per-security 404/410 is not a site-wide failure.
    if code=='model_access':return '模型服务拒绝调用，已暂停；请检查密钥、余额和额度，已完成结果保留。'
    if code=='access_required' or any(s in error for s in ('HTTP 401','HTTP 403','HTTP 429','验证码','身份核实')):
        return '源站要求验证或限制访问，已暂停股吧采集。已保存结果可继续查看；免费量化后台不能代替股吧访问授权。请勿反复点击继续。'
    if code=='network' and consecutive>=3:
        return '连续采集失败且出现网络错误，已暂停以避免反复请求。请检查网络与失败明细后继续。'
    if consecutive>=10:
        return '连续10只股票未取得有效页面，可能发生源结构变化，已暂停。个别解析失败已保留并跳过，请检查数据适配。'
    return None


worker = ScanWorker()
