import io
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from openpyxl import Workbook
from .database import Base, engine, SessionLocal, get_db
from .models import Instrument, Snapshot, PriceBar, User, AuthSession, WatchItem, AlertRule
from .auth import passwords, DUMMY_HASH, current_user, issue_session, digest, bearer, utc
from .seed import seed
from .research import composite, opinion_score, state, signals, backtest, classify_rules, WEIGHTS
from .ingestion import record_post, classify_revision
from .live import source_status, live_analysis, live_catalog, snapshot_timeline, live_rankings
from .eastmoney import IID, stock
from .miaoxiang import router as mx_router
from .live_private import router as live_private_router
from .local_collection import router as local_collection_router
from .securities import router as securities_router
from .live_export import router as live_export_router

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO', 'true').lower() == 'true':
        with SessionLocal() as db: seed(db)
    yield

app = FastAPI(title='散户情绪量化 API', version='2.0.0', lifespan=lifespan)
app.include_router(mx_router)
app.include_router(live_private_router)
app.include_router(local_collection_router)
app.include_router(securities_router)
app.include_router(live_export_router)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173,https://localhost,http://tauri.localhost,tauri://localhost').split(','), allow_credentials=False, allow_methods=['GET','POST','PUT','DELETE'], allow_headers=['Authorization','Content-Type'])

@app.middleware('http')
async def private_api_no_cache(request:Request, call_next):
    response=await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
    return response

login_attempts = defaultdict(deque)

def instrument_dict(i): return {'id':i.id,'code':i.code,'name':i.name,'kind':i.kind,'exchange':i.exchange,'scope':i.scope}
def ensure_instrument(db, iid):
    i = db.get(Instrument, iid)
    if not i: raise HTTPException(404, '未找到分析对象；演示证券目录仅含部分样本')
    return i

def history(db, iid):
    # Immutable demo-v1 only. Live snapshots must be isolated, never mixed with fixtures.
    return list(db.scalars(select(Snapshot).where(Snapshot.instrument_id==iid, Snapshot.version=='demo-v1', Snapshot.data_mode=='demo').order_by(Snapshot.session_date)))

def demo_ids():
    return select(Snapshot.instrument_id).where(Snapshot.version=='demo-v1',Snapshot.data_mode=='demo').distinct()

def analyze(db, iid, period):
    i = ensure_instrument(db, iid)
    all_rows = history(db, iid)
    rows = all_rows[-period:]
    if not rows: raise HTTPException(404, '暂无可分析的快照')
    counts = {k: sum(s.metrics[k] for s in rows) for k in ('bullish','bearish','neutral','posts','comments','reads')}
    dims = {key: (sum(s.metrics['dimensions'][key] for s in rows if s.metrics['dimensions'][key] is not None)/sum(s.metrics['dimensions'][key] is not None for s in rows)) if any(s.metrics['dimensions'][key] is not None for s in rows) else None for key in WEIGHTS}
    dims['opinion'] = opinion_score(counts['bullish'], counts['bearish'])
    metrics = {**counts, **composite(dims)}
    for key in ('bullish','bearish','neutral'): metrics[key+'_pct'] = round(counts[key]/counts['posts']*100, 1) if counts['posts'] else None
    metrics['ratio'] = round(counts['bullish']/counts['bearish'], 2) if counts['bearish'] else None
    metrics['state'] = state(metrics['score'])
    metrics['consistency'] = round(abs(counts['bullish']-counts['bearish'])/(counts['bullish']+counts['bearish'])*100, 1) if counts['bullish']+counts['bearish'] else None
    metrics['disagreement'] = round(100-metrics['consistency'],1) if metrics['consistency'] is not None else None
    prices = list(db.scalars(select(PriceBar).where(PriceBar.instrument_id==iid, PriceBar.data_mode=='demo').order_by(PriceBar.session_date)))
    pmap = {p.session_date:p.close for p in prices}
    points = [{'date':s.session_date, 'score':s.metrics['score'], 'bullish_pct':s.metrics['bullish_pct'], 'close':pmap.get(s.session_date)} for s in rows]
    return {'instrument':instrument_dict(i), 'data_mode':'demo', 'source':'可重复模拟样本，非东方财富实时数据',
        'as_of':utc(rows[-1].available_at).isoformat(), 'period':len(rows), 'requested_period':period,
        'range':[rows[0].session_date, rows[-1].session_date], 'metrics':metrics,'latest':rows[-1].metrics,
        'trend':points,'signals':signals(all_rows[-1].metrics, all_rows[-2].metrics if len(all_rows)>1 else None, prices),
        'keywords':rows[-1].metrics['keywords'], 'score_version':'research-v1',
        'limitations':['资金活跃度未接入，缺失维度不以50分填充，按可用权重重算。','热度是独立观察指标；综合分中的热度方向校准仍属模拟。','股吧样本不代表全体散户；板块样本不是完整行业成分。']}

@app.get('/api/health')
def health(): return {'status':'ok','version':'2.0.0','analysis_workflow':'collect-analyze-v1','mode':'demo+live-sample','ai':'rules-only','live_source':True,'live_scope':'verified Shanghai/Shenzhen/Beijing A-shares; separate title/body samples; on-demand manual collection'}

def validate_live(iid):
    try: stock(iid)
    except ValueError as exc: raise HTTPException(422, str(exc))

@app.get('/api/live/instruments')
def live_instruments(q:str=Query('',max_length=40),db=Depends(get_db)):
    return live_catalog(q,db=db)

@app.get('/api/live/status')
def live_status(instrument_id:str=Query(IID,max_length=64),content:Literal['titles','body']='titles',db=Depends(get_db)):
    validate_live(instrument_id)
    return source_status(db, instrument_id, content)

@app.get('/api/live/analysis')
def real_analysis(days:int=Query(1,ge=1,le=30),instrument_id:str=Query(IID,max_length=64),content:Literal['titles','body']='titles',db=Depends(get_db)):
    validate_live(instrument_id)
    return live_analysis(db,days=days,iid=instrument_id,content=content)

@app.get('/api/live/timeline')
def real_timeline(instrument_id:str=Query(IID,max_length=64),content:Literal['titles','body']='titles',db=Depends(get_db)):
    validate_live(instrument_id)
    return snapshot_timeline(db,iid=instrument_id,content=content)

@app.get('/api/live/rankings')
def real_rankings(days:int=Query(3,ge=1,le=30),content:Literal['titles','body']='titles',
                  sort:Literal['opinion_score','bullish_pct','bearish_pct','effective_posts']='opinion_score',db=Depends(get_db)):
    return live_rankings(db,days=days,content=content,sort=sort)

@app.get('/api/instruments')
def instruments(q:str='', kind:Literal['all','market','index','stock','sector']='all', db=Depends(get_db)):
    all_items = db.scalars(select(Instrument).where(Instrument.id.in_(demo_ids())).order_by(Instrument.kind, Instrument.code)).all()
    q = q.strip().lower()
    return [instrument_dict(i) for i in all_items if (kind=='all' or i.kind==kind) and (not q or q in f'{i.id} {i.code} {i.name} {" ".join(i.aliases)}'.lower())]

@app.get('/api/analysis')
def analysis(instrument_id:str='CN:ALL', period:int=Query(10,ge=1,le=30), db=Depends(get_db)):
    return analyze(db,instrument_id,period)

@app.get('/api/rankings')
def rankings(sort:Literal['score','bullish_pct','bearish_pct','heat_change']='score', db=Depends(get_db)):
    items=[]
    for i in db.scalars(select(Instrument).where(Instrument.kind=='stock')):
        rows=history(db,i.id)
        if rows: items.append({**instrument_dict(i), **rows[-1].metrics, 'date':rows[-1].session_date, 'data_mode':'demo'})
    return sorted(items,key=lambda x:x[sort],reverse=True)

@app.get('/api/backtest')
def run_backtest(instrument_id:str='CN:ALL', threshold:float=Query(60,ge=0,le=100), db=Depends(get_db)):
    ensure_instrument(db,instrument_id)
    rows=history(db,instrument_id)
    bars={p.session_date:p for p in db.scalars(select(PriceBar).where(PriceBar.instrument_id==instrument_id, PriceBar.data_mode=='demo'))}
    return {'data_mode':'demo','threshold':threshold,'results':backtest(rows,bars,threshold),
      'method':'收盘后信号 → 下一交易日开盘买入 → 第N交易日收盘卖出；每个周期独立剔除重叠事件。',
      'limitations':['仅20个模拟交易日，样本少，无预测结论。','未计手续费、滑点及涨跌停无法成交，收益为毛收益。','未进行样本外验证；95%区间为上涨比例Wilson区间。']}

@app.get('/api/signals')
def signal_screen(db=Depends(get_db)):
    result=[]
    for i in db.scalars(select(Instrument).where(Instrument.kind=='stock',Instrument.id.in_(demo_ids()))):
        a=analyze(db,i.id,10)
        for s in a['signals']:
            if s['tone']!='neutral': result.append({'instrument':instrument_dict(i),**s,'data_mode':'demo'})
    return {'items':result,'method':'确定性条件筛选，尚未启用AI选股模型','data_mode':'demo'}

class Credentials(BaseModel):
    username:str=Field(min_length=3,max_length=40,pattern=r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')
    password:str=Field(min_length=10,max_length=128)

def throttle(request):
    host=request.client.host if request.client else 'unknown'
    attempts=login_attempts[host]
    now=time.monotonic()
    while attempts and now-attempts[0]>300: attempts.popleft()
    if len(attempts)>=15: raise HTTPException(429,'操作过于频繁，请5分钟后再试')
    attempts.append(now)

@app.post('/api/auth/register')
def register(body:Credentials,request:Request,db=Depends(get_db)):
    throttle(request)
    user=User(username=body.username.lower(),password_hash=passwords.hash(body.password))
    db.add(user)
    try: db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'该用户名已存在')
    return issue_session(db,user)

@app.post('/api/auth/login')
def login(body:Credentials,request:Request,db=Depends(get_db)):
    throttle(request)
    user=db.scalar(select(User).where(User.username==body.username.lower()))
    valid=passwords.verify(body.password,user.password_hash if user else DUMMY_HASH)
    if not user or not valid: raise HTTPException(401,'用户名或密码错误')
    return issue_session(db,user)

@app.get('/api/auth/me')
def me(user=Depends(current_user)): return {'username':user.username}

@app.post('/api/auth/logout')
def logout(user=Depends(current_user),creds=Depends(bearer),db=Depends(get_db)):
    db.execute(delete(AuthSession).where(AuthSession.token_hash==digest(creds.credentials)))
    db.commit()
    return {'ok':True}

@app.get('/api/watchlist')
def watches(user=Depends(current_user),db=Depends(get_db)):
    available=set(db.scalars(demo_ids()))
    return [{**instrument_dict(ensure_instrument(db,w.instrument_id)),'demo_available':w.instrument_id in available}
            for w in db.scalars(select(WatchItem).where(WatchItem.user_id==user.id))]

class WatchBody(BaseModel): instrument_id:str=Field(max_length=64)

@app.put('/api/watchlist')
def add_watch(body:WatchBody,user=Depends(current_user),db=Depends(get_db)):
    ensure_instrument(db,body.instrument_id)
    if not db.scalar(select(WatchItem).where(WatchItem.user_id==user.id,WatchItem.instrument_id==body.instrument_id)):
        db.add(WatchItem(user_id=user.id,instrument_id=body.instrument_id))
        try: db.commit()
        except IntegrityError: db.rollback() # concurrent idempotent add
    return {'ok':True}

@app.delete('/api/watchlist')
def remove_watch(instrument_id:str,user=Depends(current_user),db=Depends(get_db)):
    db.execute(delete(WatchItem).where(WatchItem.user_id==user.id,WatchItem.instrument_id==instrument_id)); db.commit()
    return {'ok':True}

class AlertBody(BaseModel):
    instrument_id:str=Field(max_length=64)
    metric:Literal['bullish_pct','bearish_pct','score']='bullish_pct'
    threshold:float=Field(ge=0,le=100)

@app.get('/api/alerts')
def alerts(user=Depends(current_user),db=Depends(get_db)):
    out=[]
    for r in db.scalars(select(AlertRule).where(AlertRule.user_id==user.id)):
        rows=history(db,r.instrument_id)
        value=rows[-1].metrics.get(r.metric) if rows else None
        out.append({'id':r.id,'instrument':instrument_dict(ensure_instrument(db,r.instrument_id)), 'metric':r.metric,'threshold':r.threshold,'value':value,'triggered':value is not None and value>=r.threshold,'data_mode':'demo'})
    return out

@app.post('/api/alerts')
def create_alert(body:AlertBody,user=Depends(current_user),db=Depends(get_db)):
    ensure_instrument(db,body.instrument_id)
    r=AlertRule(user_id=user.id,**body.model_dump()); db.add(r); db.commit()
    return {'id':r.id}

@app.delete('/api/alerts/{rule_id}')
def delete_alert(rule_id:int,user=Depends(current_user),db=Depends(get_db)):
    db.execute(delete(AlertRule).where(AlertRule.id==rule_id,AlertRule.user_id==user.id)); db.commit()
    return {'ok':True}

class ClassifyBody(BaseModel):
    model_config={'str_strip_whitespace':True}
    text:str=Field(min_length=1,max_length=10000)
    instrument_id:str=Field(default='CN:ALL',max_length=64)

@app.post('/api/classify')
def classify(body:ClassifyBody,user=Depends(current_user),db=Depends(get_db)):
    ensure_instrument(db,body.instrument_id)
    _,revision,_=record_post(db,source='manual-workbench',source_id=f'{user.id}:{uuid4()}',
      instrument_id=body.instrument_id,author_id=str(user.id),text=body.text,published_at=datetime.now(timezone.utc))
    classification=classify_revision(db,revision)
    db.commit()
    return {**classification.result,'classification_id':classification.id,'saved_at':classification.classified_at.isoformat()}

@app.get('/api/report')
def report(db=Depends(get_db)):
    a=analyze(db,'CN:ALL',1)
    return {'data_mode':'demo','as_of':a['as_of'], 'title':'A股情绪复盘 · 模拟样本',
      'paragraphs':[f'最新模拟快照为 {a["range"][-1]}，综合指数 {a["metrics"]["score"]}，状态：{a["metrics"]["state"]}。',f'看多 {a["metrics"]["bullish_pct"]}%，看空 {a["metrics"]["bearish_pct"]}%；有效帖子 {a["metrics"]["posts"]}。','当前为规则模板复盘，尚未连接实时数据、AI摘要或每日定时采集。']}

@app.get('/api/export')
def export(instrument_id:str='CN:ALL',period:int=Query(10,ge=1,le=30),db=Depends(get_db)):
    a=analyze(db,instrument_id,period)
    book=Workbook(); sheet=book.active; sheet.title='模拟情绪数据'
    sheet.append(['模拟数据 / 非实时行情',a['instrument']['name'],a['instrument']['id']])
    sheet.append(['日期','综合情绪指数','看多占比%','模拟收盘价'])
    for r in a['trend']: sheet.append([r['date'],r['score'],r['bullish_pct'],r['close']])
    sheet.append(['评分版本',a['score_version'],'来源',a['source']])
    stream=io.BytesIO(); book.save(stream); stream.seek(0)
    return StreamingResponse(stream,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="sentiment-demo.xlsx"','Cache-Control':'no-store'})

# The same image serves Web/PWA and the API; native clients use this API origin.
from pathlib import Path
from fastapi.staticfiles import StaticFiles
web_dir = Path(__file__).resolve().parent.parent / 'web'
if not web_dir.is_dir(): web_dir = Path(__file__).resolve().parent.parent / 'dist'
if web_dir.is_dir(): app.mount('/', StaticFiles(directory=web_dir, html=True), name='web')
