"""Complete, paged A-share directory; only publish a consistent full response."""
import json
import time
import httpx
from datetime import datetime, timezone
from sqlalchemy import select
from .eastmoney import SourceError, stock, default_client
from .models import UniverseSync
from .securities import register

DIRECTORY_URL = 'https://82.push2.eastmoney.com/api/qt/clist/get'
FILTER = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'
SINA_BASE = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'


def sina_directory(client, pause=time.sleep):
    """Independent public directory, complete count before and after pagination."""
    def count():
        response=client.get(SINA_BASE+'Market_Center.getHQNodeStockCount',params={'node':'hs_a'},timeout=15,follow_redirects=False)
        if response.status_code!=200: raise SourceError(f'备用目录HTTP {response.status_code}')
        try: total=int(response.text.strip('"'))
        except ValueError as exc: raise SourceError('备用目录总数异常') from exc
        if not 4000<=total<=15000: raise SourceError('备用目录股票数量异常')
        return total
    total=count();rows={}
    for page in range(1,(total+99)//100+1):
        pause(2)
        with client.stream('GET',SINA_BASE+'Market_Center.getHQNodeData',params={'page':page,'num':100,'sort':'symbol','asc':1,'node':'hs_a','symbol':'','_s_r_a':'page'},timeout=15,follow_redirects=False) as response:
            if response.status_code!=200: raise SourceError(f'备用目录HTTP {response.status_code}')
            body=bytearray()
            for part in response.iter_bytes():
                body.extend(part)
                if len(body)>500_000: raise SourceError('备用目录单页响应过大')
        try: batch=json.loads(body)
        except ValueError as exc: raise SourceError('备用目录格式异常') from exc
        if not isinstance(batch,list) or not 1<=len(batch)<=100: raise SourceError('备用目录分页不完整')
        for entry in batch:
            symbol,code,name=entry.get('symbol',''),entry.get('code'),entry.get('name')
            exchange={'sh':'XSHG','sz':'XSHE','bj':'XBSE'}.get(symbol[:2])
            if not exchange or not isinstance(code,str) or symbol[2:]!=code or not isinstance(name,str) or not 1<=len(name)<=80: raise SourceError('备用目录证券身份异常')
            iid=f'{exchange}:{code}'
            stock(iid)
            if iid in rows: raise SourceError('备用目录分页重复')
            rows[iid]={'id':iid,'code':code,'name':name,'exchange':exchange,'kind':'stock','market':1 if exchange=='XSHG' else 0,'pinyin':''}
    if len(rows)!=total or count()!=total: raise SourceError('备用目录前后总数不一致，未发布')
    return total,rows


def directory_page(client, page):
    with client.stream('GET', DIRECTORY_URL, params={'pn':page,'pz':100,'po':1,'np':1,
        'fltt':2,'invt':2,'fid':'f12','fs':FILTER,'fields':'f12,f13,f14'},
        follow_redirects=False, timeout=15) as response:
        if response.status_code != 200:
            raise SourceError(f'目录源HTTP {response.status_code}，未重试')
        content = bytearray()
        for part in response.iter_bytes():
            content.extend(part)
            if len(content) > 200_000: raise SourceError('目录单页响应过大')
    try:
        data = json.loads(content)['data']
        total, rows = data['total'], data['diff']
        if not isinstance(total, int) or not 1 <= total <= 15000 or not isinstance(rows,list) or not 1 <= len(rows) <= 100:
            raise SourceError(f'目录条目异常：总数{total}，单页数量{len(rows) if isinstance(rows,list) else "格式无效"}')
        out = []
        for row in rows:
            code, name, market = row['f12'], row['f14'], row['f13']
            if not isinstance(code,str) or not isinstance(name,str) or not 1 <= len(name) <= 80 or market not in (0,1): raise ValueError()
            exchange = 'XSHG' if market == 1 else 'XBSE' if code.startswith(('920','4','8')) else 'XSHE'
            iid = f'{exchange}:{code}'
            try: stock(iid)  # Reject non-A shares and inconsistent exchange prefixes.
            except ValueError as exc: raise SourceError(f'目录包含不支持的证券标识：{iid}') from exc
            out.append({'id':iid,'code':code,'name':name,'exchange':exchange,'kind':'stock','market':market,'pinyin':''})
        return total, out
    except SourceError:
        raise
    except (ValueError,KeyError,TypeError) as exc:
        raise SourceError('目录格式或证券身份校验失败') from exc


def sync_universe(db, client=None, pause=time.sleep, provider='sina'):
    recent = db.scalar(select(UniverseSync).where(UniverseSync.status=='success').order_by(UniverseSync.id.desc()))
    run = UniverseSync(status='running'); db.add(run); db.commit()
    own = client is None
    client = client or httpx.Client(headers={'User-Agent':'Mozilla/5.0'})
    def page_with_backoff(number):
        # Only transport failures are retried; source rejections and bad payloads stop.
        for attempt in range(3):
            try: return directory_page(client,number)
            except httpx.TransportError:
                if attempt == 2: raise
                pause(3 * (attempt+1))
    try:
        if provider=='sina':
            total,rows=sina_directory(client,pause)
        elif provider=='eastmoney':
            total, first = page_with_backoff(1)
            rows = {r['id']:r for r in first}
            if len(rows) != len(first): raise SourceError('目录首屏重复')
            for page in range(2, (total+99)//100+1):
                pause(2)
                current_total, batch = page_with_backoff(page)
                if current_total != total or any(r['id'] in rows for r in batch):
                    raise SourceError('目录分页变化或重复，未发布不完整目录')
                rows.update((r['id'],r) for r in batch)
        else: raise SourceError('目录提供方无效')
        if len(rows) != total: raise SourceError('目录数量不完整，未发布')
        if recent and total < recent.total*.9: raise SourceError('目录数量大幅缩减，保留上次完整目录')
        register(db, list(rows.values()))
        run.status, run.total, run.members = 'success', total, sorted(rows)
    except Exception as exc:
        db.rollback(); run = db.get(UniverseSync,run.id)
        run.status = 'failed'
        run.error = str(exc) if isinstance(exc,SourceError) else '目录源连接中断，未取得完整目录；保留上次完整目录，请稍后同步。'
    finally:
        if own: client.close()
    run.finished_at = datetime.now(timezone.utc); db.commit()
    return run
