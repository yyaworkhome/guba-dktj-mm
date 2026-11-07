"""Public progress and immutable daily rankings; loopback-only job controls."""
import csv
import io
from typing import Literal
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from .database import get_db
from .models import UniverseSync, MarketScan, ScanItem, ScanSchedule,CommunitySchedule,MarketReport
from .local_collection import enabled_for
from .scanner import queue_scan, process_lock, worker,method_config,scan_method
from .universe import sync_universe
from .research import utc

router = APIRouter(prefix='/api/live/market')


def allowed(request):
    if not enabled_for(request): raise HTTPException(403,'扫描管理仅对已开启本机采集的服务开放。')


def coverage(db,job):
    totals={name:0 for name in ('total','attempted','collected','classified','eligible','awaiting_ai','source_failed','model_failed','insufficient')}
    exchanges={name:{**totals,'exchange':name} for name in ('XSHG','XSHE','XBSE')}
    rows=db.execute(select(ScanItem.instrument_id,ScanItem.status,
        ScanItem.result['probe_id'].as_integer(),ScanItem.result['collection_status'].as_string(),
        ScanItem.result['analysis_id'].as_integer(),ScanItem.result['effective_posts'].as_integer(),
        ScanItem.result['error_code'].as_string()).where(ScanItem.scan_id==job.id))
    for iid,status,probe_id,collected,analysis_id,effective,code in rows:
        group=exchanges.get(iid.split(':')[0])
        values={'total':1,'attempted':int(status!='pending' or probe_id is not None),
            'collected':int(collected=='samples' or effective is not None and effective>0 or (code or '').startswith('model_')),
            'classified':int(analysis_id is not None or effective is not None),
            'eligible':int(status=='eligible'),'awaiting_ai':int(status=='awaiting_ai'),
            'source_failed':int(status=='failed' and not (code or '').startswith('model_')),
            'model_failed':int(status=='failed' and (code or '').startswith('model_')),
            'insufficient':int(status=='insufficient')}
        for key,value in values.items():
            totals[key]+=value
            if group is not None:group[key]+=value
    for key in ('collected','classified','eligible'):
        totals[key+'_pct']=round(totals[key]/job.total*100,2) if job.total else 0
    return {**totals,'exchanges':list(exchanges.values())}


def job_dict(db, job):
    if not job: return None
    counts = dict(db.execute(select(ScanItem.status,func.count()).where(ScanItem.scan_id==job.id).group_by(ScanItem.status)).all())
    return {'id':job.id,'status':job.status,'total':job.total,'counts':counts,'days':job.days,
            'created_at':utc(job.created_at).isoformat(),'finished_at':utc(job.finished_at).isoformat() if job.finished_at else None,
            'error':job.error,'processed':job.total-counts.get('pending',0),'method':scan_method(db,job),'coverage':coverage(db,job),
            'retry_waiting':db.scalar(select(func.count()).select_from(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status.in_(['pending','awaiting_ai']),ScanItem.result['next_retry_at'].as_string().is_not(None)))}


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
    from .sentiment_ai import configuration
    return {'enabled':enabled_for(request),'worker_ready':worker.ready,
            'universe':{'total':universe.total,'observed_at':utc(universe.finished_at).isoformat()} if universe else None,
            'directory_error': latest.error if latest and latest.status=='failed' else '',
            'schedule':{'enabled':bool(config and config.enabled),'hour':config.hour if config else 16,'minute':config.minute if config else 10,
                'method':db.get(CommunitySchedule,1).config if db.get(CommunitySchedule,1) else method_config()},
            'job':job_dict(db,jobs[0]) if jobs else None,
            'history':[{'id':j.id,'created_at':utc(j.created_at).isoformat(),'status':j.status} for j in jobs],
            'ai':configuration(db) if enabled_for(request) else None}


@router.post('/directory')
def directory(request:Request, db=Depends(get_db)):
    allowed(request)
    try:
        with process_lock('directory-worker.lock'): run=sync_universe(db)
    except ValueError as exc: raise HTTPException(409,str(exc))
    if run.status!='success': raise HTTPException(502,run.error)
    return {'total':run.total,'observed_at':utc(run.finished_at).isoformat()}


class StartBody(BaseModel):
    source:str=Field(default='eastmoney',pattern='^(eastmoney|sina|taoguba)$')
    mode:str=Field(default='rules',pattern='^(rules|ai)$')


@router.post('/start')
def start(body:StartBody,request:Request, db=Depends(get_db)):
    allowed(request)
    if not worker.ready: raise HTTPException(503,'扫描工作进程尚未就绪，请重启本机服务。')
    try:
        method=method_config(body.source,body.mode)
        return job_dict(db,queue_scan(db,method=method))
    except ValueError as exc: raise HTTPException(409,str(exc))


class ScheduleBody(BaseModel):
    enabled: bool
    hour: int = Field(default=16, ge=0,le=23)
    minute: int = Field(default=10, ge=0,le=59)
    source:str=Field(default='eastmoney',pattern='^(eastmoney|sina|taoguba)$')
    mode:str=Field(default='rules',pattern='^(rules|ai)$')


@router.put('/schedule')
def schedule(body:ScheduleBody, request:Request, db=Depends(get_db)):
    allowed(request)
    try:method=method_config(body.source,body.mode)
    except ValueError as exc:raise HTTPException(422,str(exc))
    method_row=db.get(CommunitySchedule,1) or CommunitySchedule(id=1)
    method_row.config=method;db.add(method_row)
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
    if not job and body.action in ('cancel','pause','resume'):
        job=db.scalar(select(MarketScan).where(MarketScan.status=='waiting_ai').order_by(MarketScan.id.desc()))
    if body.action=='retry_failed':
        job=job or db.scalar(select(MarketScan).order_by(MarketScan.id.desc()))
        if not job: raise HTTPException(409,'暂无可重试批次。')
        rows=list(db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='failed')))
        if not rows: raise HTTPException(409,'本批次没有采集失败的股票。')
        for item in rows:
            if item.result.get('error_code') in ('access_required','model_access'):
                raise HTTPException(409,'本批存在访问或模型授权拒绝，请先解除限制；不能通过重试绕过。可选择其他来源新建批次。')
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
    job.active_key='active'
    db.commit()
    return job_dict(db,job)


@router.get('/rankings')
def rankings(scan_id:int|None=None,limit:int=Query(30,ge=1,le=100),db=Depends(get_db)):
    job=selected_job(db,scan_id)
    rows=list(db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='eligible'))) if job else []
    eligible=[r.result for r in rows]
    method=scan_method(db,job) if job else method_config()
    return {'job':job_dict(db,job),'bullish':sorted(eligible,key=lambda r:(-r['opinion_score'],r['id']))[:limit],
            'bearish':sorted(eligible,key=lambda r:(r['opinion_score'],r['id']))[:limit],
            'method':f"{method['source']} / {method['model']}；"+('近3自然日标题样本；' if method['basis']=='publication' else '公开列表观察快照，非近3日发帖总量；')+'至少20条有效样本、10位作者、10条方向样本且24小时内观察。',
            'scope':'采样、分类和入榜覆盖分别统计。AI待处理、失败与不足样本不参与排序；采样结束不代表全市场AI分类完成。'}


@router.get('/report')
def report(scan_id:int|None=None,db=Depends(get_db)):
    return build_report(scan_id,db)


def build_report(scan_id,db,use_saved=True):
    job=selected_job(db,scan_id)
    if not job:raise HTTPException(404,'尚无扫描批次')
    saved=db.scalar(select(MarketReport).where(MarketReport.scan_id==job.id).order_by(MarketReport.id.desc()))
    if use_saved and saved and job.status=='completed':return {**saved.result,'report_id':saved.id,'saved_at':utc(saved.created_at).isoformat()}
    method=scan_method(db,job)
    results=[r.result for r in db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,ScanItem.status=='eligible'))]
    previous=None
    for candidate in db.scalars(select(MarketScan).where(MarketScan.id<job.id,MarketScan.status=='completed').order_by(MarketScan.id.desc()).limit(30)):
        if scan_method(db,candidate)==method:previous=candidate;break
    prior={r.instrument_id:r.result for r in db.scalars(select(ScanItem).where(ScanItem.scan_id==previous.id,ScanItem.status=='eligible'))} if previous else {}
    changes=[];extremes=[]
    for row in results:
        score=row['opinion_score'];base={k:row.get(k) for k in ('id','name','code','opinion_score','effective_posts','observed_at')}
        if score>=90 or score<=10:extremes.append({**base,'signal':'方向样本极端偏多' if score>=90 else '方向样本极端偏空'})
        old=prior.get(row['id'])
        if old and old.get('opinion_score') is not None:
            delta=round(score-old['opinion_score'],2)
            if abs(delta)>=20:changes.append({**base,'change':delta,'signal':'情绪升温' if delta>0 else '情绪降温'})
    return {'job':job_dict(db,job),'method':method,'eligible_stocks':len(results),
        'coverage_pct':round(len(results)/job.total*100,2),
        'mean_opinion_score':round(sum(r['opinion_score'] for r in results)/len(results),2) if results else None,
        'extremes':sorted(extremes,key=lambda r:-abs(r['opinion_score']-50))[:30],
        'changes':sorted(changes,key=lambda r:-abs(r['change']))[:30],
        'comparison_scan_id':previous.id if previous else None,
        'limitations':['均值仅代表本批达标股票的等权舆论分，不是完整A股情绪指数。','进行中、已暂停、未全覆盖的批次均为部分报告。','异动阈值是待校准研究参数；只比较同来源、同模型、同口径的已完成历史批次，非交易信号。']}


def save_report(db,job):
    data=build_report(job.id,db,use_saved=False)
    row=MarketReport(scan_id=job.id,result=data);db.add(row);db.commit();return row


@router.get('/items')
def items(scan_id:int|None=None,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),state:Literal['awaiting_ai','failed','insufficient','eligible']|None=None,db=Depends(get_db)):
    job=selected_job(db,scan_id)
    rows=list(db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id,
        ScanItem.status==state if state else ScanItem.status!='pending').order_by(ScanItem.id).offset(offset).limit(limit))) if job else []
    return {'items':[{'status':r.status,**r.result} for r in rows]}


@router.get('/export')
def export(scan_id:int|None=None,db=Depends(get_db)):
    job=selected_job(db,scan_id)
    if not job: raise HTTPException(404,'尚无扫描批次。')
    out=io.StringIO();out.write('\ufeff');writer=csv.writer(out)
    method=scan_method(db,job)
    writer.writerow(['批次','状态','来源','模型','提示词或规则版本','时间口径','证券ID','名称','舆论分','有效样本','作者','看多','看空','中性或未识别','观察时间','未入榜或失败原因','采样状态','分类状态','原始帖子','预筛可用帖子'])
    for item in db.scalars(select(ScanItem).where(ScanItem.scan_id==job.id).order_by(ScanItem.id)):
        r=item.result;c=r.get('counts',{})
        values=[job.id,item.status,method['source'],method['model'],method['version'],method['basis'],item.instrument_id,r.get('name',''),r.get('opinion_score',''),r.get('effective_posts',''),r.get('authors',''),c.get('bullish',''),c.get('bearish',''),c.get('neutral',''),r.get('observed_at',''),r.get('error') or r.get('waiting_reason') or '；'.join(r.get('reasons',[])),r.get('collection_status',''),r.get('classification_status',''),r.get('raw_posts',''),r.get('candidate_posts','')]
        writer.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v) for v in values])
    return StreamingResponse(iter([out.getvalue().encode('utf-8')]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="market-scan-{job.id}.csv"'})
