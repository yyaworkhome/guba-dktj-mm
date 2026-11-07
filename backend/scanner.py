"""Durable, paced public-title scanning. The worker never invents missing results."""
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from .database import SessionLocal, DATA_DIR
from .models import UniverseSync, MarketScan, ScanItem, ScanSchedule, CollectionRun
from .collect import collect, collector_lock
from .eastmoney import default_client, CST, SOURCE
from .live import live_analysis
from .research import utc
from .universe import sync_universe


@contextmanager
def process_lock(name):
    """OS releases this advisory lock on crash; the file itself can remain."""
    handle = (DATA_DIR / name).open('a+b')
    try:
        handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError('任务正在其他进程运行，请等待。')
    try:
        yield
    finally:
        handle.close()


def queue_scan(db, scheduled_date=None):
    active = db.scalar(select(MarketScan).where(MarketScan.active_key == 'active'))
    if active: return active
    universe = db.scalar(select(UniverseSync).where(UniverseSync.status == 'success').order_by(UniverseSync.id.desc()))
    if not universe: raise ValueError('请先同步完整A股目录。')
    if scheduled_date:
        existing = db.scalar(select(MarketScan).where(MarketScan.scheduled_date == scheduled_date))
        if existing: return existing
    job = MarketScan(universe_id=universe.id, active_key='active', scheduled_date=scheduled_date,
                     total=universe.total, days=3, status='queued')
    try:
        db.add(job); db.flush()
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
    db.commit()
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
        if directory.status == 'success' and not self.stop.is_set(): queue_scan(db, day)

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
                            if not job or job.status == 'paused':
                                self.stop.wait(5); continue
                            if job.cancel_requested:
                                job.status,job.active_key,job.finished_at = 'cancelled',None,datetime.now(timezone.utc)
                                db.commit(); continue
                            job.status = 'running'; db.commit()
                            cooldowns = {key:until for key,until in cooldowns.items() if until > time.monotonic()}
                            item = db.scalar(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='pending',
                                ScanItem.id.not_in(cooldowns)).order_by(ScanItem.id))
                            if not item:
                                remaining = db.scalar(select(func.count()).select_from(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='pending'))
                                if remaining:
                                    self.stop.wait(5); continue
                                job.status,job.active_key,job.finished_at = 'completed',None,datetime.now(timezone.utc)
                                db.commit(); continue
                            try:
                                with collector_lock():
                                    processed = process_item(db, job, item, client)
                            except ValueError:
                                self.stop.wait(2); continue
                            if not processed:
                                # Let other securities advance while this shared cooldown expires.
                                cooldowns[item.id] = time.monotonic()+30
                                self.stop.wait(2); continue
                            failures = failures+1 if item.status=='failed' else 0
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
    if code=='access_required' or any(s in error for s in ('HTTP 401','HTTP 403','HTTP 429','验证码','身份核实')):
        return '源站要求验证或限制访问，已暂停股吧采集。已保存结果可继续查看；免费量化后台不能代替股吧访问授权。请勿反复点击继续。'
    if code=='network' and consecutive>=3:
        return '连续采集失败且出现网络错误，已暂停以避免反复请求。请检查网络与失败明细后继续。'
    if consecutive>=10:
        return '连续10只股票未取得有效页面，可能发生源结构变化，已暂停。个别解析失败已保留并跳过，请检查数据适配。'
    return None


worker = ScanWorker()
