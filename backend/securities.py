"""A-share directory lookup from a fixed public Eastmoney suggestion endpoint."""
import json
import time
from collections import OrderedDict, defaultdict, deque
from threading import Lock
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from .database import get_db
from .models import Instrument
from .eastmoney import STOCKS, stock, default_client, SourceError

router = APIRouter(prefix='/api/live')
SEARCH_URL = 'https://searchapi.eastmoney.com/api/suggest/get'
cache = OrderedDict()
attempts = defaultdict(deque)
guard = Lock()


def parse_directory(document):
    try:
        table = json.loads(document)['QuotationCodeTable']
        rows = table['Data']
        if table['Status'] != 0 or not isinstance(rows, list) or len(rows) > 50:
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise SourceError('证券目录返回格式变化，未生成猜测代码') from exc
    out = {}
    for r in rows:
        if not isinstance(r, dict): continue
        code, name = r.get('Code'), r.get('Name')
        market = str(r.get('MktNum'))
        if not isinstance(code, str) or not isinstance(name, str) or not 0 < len(name) <= 80: continue
        kind, exchange = None, None
        if r.get('Classify') == 'AStock' and (market,str(r.get('SecurityType'))) in {('1','1'),('0','2')}:
            exchange = 'XSHG' if market == '1' else 'XSHE' if market == '0' else None
            kind = 'stock'
        elif (r.get('Classify') == 'NEEQ' and str(r.get('SecurityType')) == '27'
              and r.get('SecurityTypeName') in {'北A','京A'} and code.startswith('920') and market == '0'):
            exchange, kind = 'XBSE', 'stock'
        elif r.get('Classify') == 'Index' and market in ('0','1') and len(code)==6 and code.isascii() and code.isdigit():
            exchange, kind = ('XSHG' if market=='1' else 'XSHE'), 'index'
        if not exchange: continue
        iid = f'{exchange}:{code}'
        if r.get('QuoteID') != f'{market}.{code}': continue
        if kind == 'stock':
            try: stock(iid)
            except ValueError: continue
        pinyin = r.get('PinYin', '')
        out[iid] = {'id':iid, 'code':code, 'name':name, 'exchange':exchange, 'kind':kind,
                    'pinyin': pinyin if isinstance(pinyin,str) and len(pinyin)<=80 else '', 'market':int(market)}
    return list(out.values())


def lookup(q, client):
    normalized = q.strip()
    for prefix in ('XSHG:', 'XSHE:', 'XBSE:'):
        if normalized.upper().startswith(prefix): normalized = normalized[len(prefix):]
    with guard:
        found = cache.get(normalized)
        if found and time.monotonic()-found[0] < 1800:
            cache.move_to_end(normalized)
            return found[1]
    url = SEARCH_URL + '?' + urlencode({'input': normalized, 'type':'14','count':'20'})
    with client.stream('GET', url, follow_redirects=False, timeout=15) as response:
        if response.status_code != 200:
            raise SourceError(f'证券目录返回HTTP {response.status_code}，未重试或绕过')
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data)>200_000: raise SourceError('证券目录响应过大')
    try: rows = parse_directory(data.decode('utf-8'))
    except UnicodeDecodeError as exc: raise SourceError('证券目录编码异常') from exc
    with guard:
        cache[normalized] = (time.monotonic(), rows)
        cache.move_to_end(normalized)
        while len(cache)>256: cache.popitem(last=False)
    return rows


def register(db, rows):
    for r in rows:
        if r['kind'] != 'stock': continue
        item = db.get(Instrument, r['id'])
        if not item:
            item = Instrument(id=r['id'],code=r['code'],name=r['name'],kind='stock',exchange=r['exchange'],aliases=[],scope={})
            db.add(item)
        # Keep demo names and aliases stable; live display reads verified metadata.
        item.scope = {**(item.scope or {}), 'live_directory': {'name':r['name'], 'market':r['market'], 'pinyin':r['pinyin']}}
    db.commit()


def ensure_stock(db, iid, client):
    stock(iid)
    item = db.get(Instrument, iid)
    if item and (iid in STOCKS or (item.scope or {}).get('live_directory')): return item
    if iid in STOCKS:
        code,name,_=STOCKS[iid]
        if not item:
            item=Instrument(id=iid,code=code,name=name,kind='stock',exchange=iid.split(':')[0],aliases=[],scope={})
            db.add(item);db.commit()
        return item
    rows = [r for r in lookup(iid, client) if r['id']==iid and r['kind']=='stock']
    if not rows: raise ValueError('证券目录未确认这只A股，请按名称搜索并明确选择；未发起股吧采集。')
    register(db, rows)
    return db.get(Instrument, iid)


def local_matches(db, q):
    q=q.lower()
    result=[]
    for i in db.scalars(select(Instrument).where(Instrument.kind=='stock')):
        try: stock(i.id)
        except ValueError: continue
        meta=(i.scope or {}).get('live_directory') or {}
        if i.id not in STOCKS and not meta: continue
        name=meta.get('name',i.name)
        if q in f'{i.id} {i.code} {name} {meta.get("pinyin", "")} {" ".join(i.aliases)}'.lower():
            result.append({'id':i.id,'code':i.code,'name':name,'kind':'stock','exchange':i.exchange})
    return result[:20]


@router.get('/search')
def search(request:Request,q:str=Query(min_length=1,max_length=40),db=Depends(get_db)):
    q=q.strip()
    if not q: raise HTTPException(422,'请输入股票代码或名称')
    now=time.monotonic()
    address=request.client.host if request.client else 'unknown'
    with guard:
        # Bounded IP counters; search is manual, never a keystroke network crawler.
        if len(attempts)>1024: attempts.clear()
        times=attempts[address]
        while times and now-times[0]>60: times.popleft()
        if len(times)>=30: raise HTTPException(429,'搜索过于频繁，请一分钟后重试')
        times.append(now)
    try:
        with default_client() as client: rows=lookup(q,client)
        register(db,rows)
        return {'items':rows,'source':'东方财富证券目录','warning':None}
    except (httpx.HTTPError,SourceError):
        return {'items':local_matches(db,q),'source':'已保存的本机证券目录',
                'warning':'远程证券目录暂不可用，仅显示已保存候选；没有猜测证券或补入演示结果。'}
