"""Separate, bounded public market data with provenance and independent cooldown."""
import json
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from math import isfinite
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,Field
from sqlalchemy import select
from .database import get_db
from .models import Instrument,QuoteFetch,QuoteObservation
from .eastmoney import stock,CST,SourceError,default_client,get_document,kline_url,parse_bars
from .research import utc

router=APIRouter(prefix='/api/live')
TENCENT_URL='https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'


def symbol(iid):
    code,_,_=stock(iid)
    return {'XSHG':'sh','XSHE':'sz','XBSE':'bj'}[iid.split(':')[0]]+code


def parse_tencent(document,iid,observed):
    try:
        payload=json.loads(document);data=payload['data'][symbol(iid)]
        if payload['code']!=0 or data['qt'][symbol(iid)][2]!=stock(iid)[0]: raise ValueError()
        rows=data['day']
        if not isinstance(rows,list) or not 1<=len(rows)<=60: raise ValueError()
        bars=[];dates=set()
        for row in rows:
            if not isinstance(row,list) or len(row) not in (6,11): raise ValueError()
            date=datetime.strptime(row[0],'%Y-%m-%d').date()
            if date>observed.astimezone(CST).date() or row[0] in dates: raise ValueError()
            opened,close,high,low,volume=map(float,row[1:6])
            # Tencent STAR volume is shares; normalize all stored observations to lots.
            if iid.startswith('XSHG:68'): volume/=100
            amount=float(row[8])*10000 if len(row)==11 else None
            if not all(isfinite(v) for v in (opened,close,high,low,volume)): raise ValueError()
            if not 0<low<=min(opened,close)<=max(opened,close)<=high or volume<0: raise ValueError()
            if amount is not None and (not isfinite(amount) or amount<0): raise ValueError()
            if amount and (not volume or not low*.99<=amount/(volume*100)<=high*1.01): raise ValueError()
            dates.add(row[0]);bars.append({'date':row[0],'open':opened,'close':close,'high':high,'low':low,
                'volume_lots':volume,'amount_cny':amount,'adjustment':'none','is_current_session':date==observed.astimezone(CST).date()})
        return sorted(bars,key=lambda b:b['date'])
    except (KeyError,IndexError,ValueError,TypeError) as exc:
        raise SourceError('腾讯日线证券、日期或价格格式校验失败；未生成估算成交额') from exc


def fetch_tencent(client,iid):
    with client.stream('GET',TENCENT_URL,params={'param':f'{symbol(iid)},day,,,40,'},timeout=15,follow_redirects=False) as response:
        if response.status_code!=200: raise SourceError(f'腾讯行情HTTP {response.status_code}')
        body=bytearray()
        for part in response.iter_bytes():
            body.extend(part)
            if len(body)>200_000: raise SourceError('腾讯行情响应过大')
    observed=datetime.now(timezone.utc)
    return parse_tencent(body,iid,observed),observed,sha256(body).hexdigest()


def saved_quote(db,iid,cutoff):
    old=db.scalar(select(QuoteObservation).where(QuoteObservation.instrument_id==iid,QuoteObservation.observed_at<=cutoff).order_by(QuoteObservation.observed_at.desc(),QuoteObservation.id.desc()))
    new=db.scalar(select(QuoteFetch).where(QuoteFetch.instrument_id==iid,QuoteFetch.status=='success',QuoteFetch.finished_at<=cutoff).order_by(QuoteFetch.finished_at.desc(),QuoteFetch.id.desc()))
    if new and (not old or utc(new.finished_at)>utc(old.observed_at)):
        return {'observed_at':utc(new.finished_at).isoformat(),'bars':new.bars,'source':new.source}
    return {'observed_at':utc(old.observed_at).isoformat(),'bars':old.bars,'source':'东方财富未复权日线'} if old else None


def refresh_quote(db,client,iid):
    stock(iid)
    item=db.get(Instrument,iid)
    if not item or not (item.scope or {}).get('live_directory'):
        from .eastmoney import STOCKS
        if not item or iid not in STOCKS: raise ValueError('请先搜索并选择已核验的A股。')
    now=datetime.now(timezone.utc)
    previous=db.scalar(select(QuoteFetch).where(QuoteFetch.instrument_id==iid).order_by(QuoteFetch.id.desc()))
    if previous and now-utc(previous.started_at)<timedelta(minutes=10): return previous,True
    run=QuoteFetch(instrument_id=iid,started_at=now);db.add(run);db.commit()
    attempts=[]
    try:
        run.bars,observed,digest=fetch_tencent(client,iid);run.source='腾讯财经未复权日线'
        attempts.append({'source':run.source,'status':'ok','response_sha256':digest,
                         'missing':['历史成交额'] if any(b['amount_cny'] is None for b in run.bars) else []})
    except (SourceError,httpx.HTTPError) as exc:
        attempts.append({'source':'腾讯财经未复权日线','status':'failed','error':str(exc) if isinstance(exc,SourceError) else '行情网络连接失败'})
        try:
            document=get_document(client,kline_url(iid));observed=datetime.now(timezone.utc)
            run.bars=parse_bars(document,observed,iid);run.source='东方财富未复权日线'
            attempts.append({'source':run.source,'status':'ok','response_sha256':sha256(document.encode()).hexdigest()})
        except (SourceError,httpx.HTTPError) as fallback:
            attempts.append({'source':'东方财富未复权日线','status':'failed','error':str(fallback) if isinstance(fallback,SourceError) else '备用行情网络连接失败'})
    run.status='success' if run.bars else 'failed';run.finished_at=datetime.now(timezone.utc);run.details=attempts
    db.commit();return run,False


def quote_analysis(db,iid):
    from .decision import technical_levels
    now=datetime.now(timezone.utc);quote=saved_quote(db,iid,now)
    technical,reasons=technical_levels(quote,now)
    latest=db.scalar(select(QuoteFetch).where(QuoteFetch.instrument_id==iid).order_by(QuoteFetch.id.desc()))
    return {'instrument_id':iid,'technical':technical,'reasons':reasons,
        'quote':quote,'refresh':{'status':latest.status,'attempts':latest.details,'started_at':utc(latest.started_at).isoformat()} if latest else None}


class Body(BaseModel):
    model_config={'extra':'forbid'}
    instrument_id:str=Field(max_length=64)


@router.get('/quotes')
def read(instrument_id:str=Query(max_length=64),db=Depends(get_db)):
    try: stock(instrument_id)
    except ValueError as exc: raise HTTPException(422,str(exc))
    return quote_analysis(db,instrument_id)


@router.post('/quotes')
def refresh(body:Body,request:Request,db=Depends(get_db)):
    from .local_collection import enabled_for
    from .collect import collector_lock
    if not enabled_for(request): raise HTTPException(403,'行情更新仅对本机服务开放。')
    try:
        with collector_lock(),default_client() as client: run,reused=refresh_quote(db,client,body.instrument_id)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {**quote_analysis(db,body.instrument_id),'reused':reused,
            'message':('十分钟内复用行情观察，未重复访问数据源。' if reused else '行情已更新，未请求股吧。' if run.status=='success' else '行情源暂不可用，保留此前数据；请查看来源状态。')}
