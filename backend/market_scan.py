"""Public progress and immutable daily rankings; loopback-only job controls."""
import csv
import io
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from .database import get_db
from .models import UniverseSync, MarketScan, ScanItem, ScanSchedule
from .local_collection import enabled_for
from .scanner import queue_scan, process_lock, worker
from .universe import sync_universe
from .research import utc

router = APIRouter(prefix='/api/live/market')


def allowed(request):
    if not enabled_for(request): raise HTTPException(403,'扫描管理仅对已开启本机采集的服务开放。')


def job_dict(db, job):
    if not job: return None
    counts = dict(db.execute(select(ScanItem.status,func.count()).where(ScanItem.scan_id==job.id).group_by(ScanItem.status)).all())
    return {'id':job.id,'status':job.status,'total':job.total,'counts':counts,'days':job.days,
            'created_at':utc(job.created_at).isoformat(),'finished_at':utc(job.finished_at).isoformat() if job.finished_at else None,
            'error':job.error,'processed':job.total-counts.get('pending',0)}


def selected_job(db, scan_id):
    job = db.get(MarketScan,scan_id) if scan_id else db.scalar(select(MarketScan).order_by(MarketScan.id.desc()))
    if scan_id and not job: raise HTTPException(404,'扫描批次不存在')
    return job


@router.get('/status')
def status(request:Request, db=Depends(get_db)):
    universe = db.scalar(select(UniverseSync).where(UniverseSync.status=='success').order_by(UniverseSync.id.desc()))
    latest = db.scalar(select(UniverseSync).order_by(UniverseSync.id.desc()))
    config = db.get(ScanSchedule,1)
    jobs = list(db.scalars(select(MarketScan).order_by(MarketScan.id.desc()).limit(20)))
    return {'enabled':enabled_for(request),'worker_ready':worker.ready,
            'universe':{'total':universe.total,'observed_at':utc(universe.finished_at).isoformat()} if universe else None,
            'directory_error': latest.error if latest and latest.status=='failed' else '',
            'schedule':{'enabled':bool(config and config.enabled),'hour':config.hour if config else 16,'minute':config.minute if config else 10},
            'job':job_dict(db,jobs[0]) if jobs else None,
            'history':[{'id':j.id,'created_at':utc(j.created_at).isoformat(),'status':j.status} for j in jobs]}


@router.post('/directory')
def directory(request:Request, db=Depends(get_db)):
    allowed(request)
    try:
        with process_lock('directory-worker.lock'): run=sync_universe(db)
    except ValueError as exc: raise HTTPException(409,str(exc))
    if run.status!='success': raise HTTPException(502,run.error)
    return {'total':run.total,'observed_at':utc(run.finished_at).isoformat()}


@router.post('/start')
def start(request:Request, db=Depends(get_db)):
    allowed(request)
    if not worker.ready: raise HTTPException(503,'扫描工作进程尚未就绪，请重启本机服务。')
    try: return job_dict(db,queue_scan(db))
    except ValueError as exc: raise HTTPException(409,str(exc))


class ScheduleBody(BaseModel):
    enabled: bool
    hour: int = Field(default=16, ge=0,le=23)
    minute: int = Field(default=10, ge=0,le=59)


@router.put('/schedule')
def schedule(body:ScheduleBody, request:Request, db=Depends(get_db)):
    allowed(request)
    config=db.get(ScanSchedule,1) or ScanSchedule(id=1)
    config.enabled=int(body.enabled);config.hour=body.hour;config.minute=body.minute
    db.add(config);db.commit()
    return {'enabled':bool(config.enabled),'hour':config.hour,'minute':config.minute}


class ControlBody(BaseModel):
    action: str = Field(pattern='^(cancel|resume|pause|retry_failed)$')


@router.post('/control')
def control(body:ControlBody, request:Request, db=Depends(get_db)):
    allowed(request)
    job=db.scalar(select(MarketScan).where(MarketScan.active_key=='active'))
    if body.action=='retry_failed':
        job=job or db.scalar(select(MarketScan).order_by(MarketScan.id.desc()))
        if not job: raise HTTPException(409,'暂无可重试批次。')
        rows=list(db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='failed')))
        if not rows: raise HTTPException(409,'本批次没有采集失败的股票。')
        for item in rows:
            item.result={**item.result,'retry_history':[*item.result.get('retry_history',[]),{'error':item.result.get('error',''),'observed_at':utc(item.observed_at).isoformat() if item.observed_at else None}]}
            item.status='pending'
        job.active_key='active';job.status='queued';job.cancel_requested=0;job.finished_at=None;job.error=''
        try: db.commit()
        except IntegrityError: db.rollback();raise HTTPException(409,'另有扫描正在运行。')
        return job_dict(db,job)
    if not job: raise HTTPException(409,'暂无进行中的扫描。')
    if body.action=='cancel':
        job.cancel_requested=1
        # A paused job must be made runnable so the worker can finalize cancellation.
        job.status='queued'
    elif body.action=='pause': job.status='paused'
    else: job.status='queued';job.error=''
    db.commit()
    return job_dict(db,job)


@router.get('/rankings')
def rankings(scan_id:int|None=None,limit:int=Query(30,ge=1,le=100),db=Depends(get_db)):
    job=selected_job(db,scan_id)
    rows=list(db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='eligible'))) if job else []
    eligible=[r.result for r in rows]
    return {'job':job_dict(db,job),'bullish':sorted(eligible,key=lambda r:(-r['opinion_score'],r['id']))[:limit],
            'bearish':sorted(eligible,key=lambda r:(r['opinion_score'],r['id']))[:limit],
            'method':'近3自然日标题样本；按已识别方向舆论分排序。至少20条有效样本、10位作者、10条方向样本且24小时内观察；rules-v1。',
            'scope':'进行中的批次仅为部分排名；完成后覆盖本批完整目录，失败与不足样本不参与排序。'}


@router.get('/items')
def items(scan_id:int|None=None,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),db=Depends(get_db)):
    job=selected_job(db,scan_id)
    rows=list(db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status!='pending').order_by(ScanItem.id).offset(offset).limit(limit))) if job else []
    return {'items':[{'status':r.status,**r.result} for r in rows]}


@router.get('/export')
def export(scan_id:int|None=None,db=Depends(get_db)):
    job=selected_job(db,scan_id)
    if not job: raise HTTPException(404,'尚无扫描批次。')
    out=io.StringIO();out.write('\ufeff');writer=csv.writer(out)
    writer.writerow(['批次','状态','代码','名称','舆论分','有效样本','作者','看多','看空','未识别','观察时间','未入榜或失败原因'])
    for item in db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id).order_by(ScanItem.id)):
        r=item.result;c=r.get('counts',{})
        values=[job.id,item.status,item.instrument_id,r.get('name',''),r.get('opinion_score',''),r.get('effective_posts',''),r.get('authors',''),c.get('bullish',''),c.get('bearish',''),c.get('neutral',''),r.get('observed_at',''),r.get('error') or '；'.join(r.get('reasons',[]))]
        writer.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for v in values])
    return StreamingResponse(iter([out.getvalue().encode('utf-8')]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="market-scan-{job.id}.csv"'})
