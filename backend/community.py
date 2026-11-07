"""Public community validation, bounded and separate from Eastmoney history.

Only fixed stock-page URLs are fetched; no cookies, redirects or script execution.
Visible excerpts and source display times are retained without inventing publication.
"""
import json
import re
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from html.parser import HTMLParser
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,Field
from sqlalchemy import select,func
import httpx
from .database import get_db
from .models import Instrument,CommunityProbe,CommunityAnalysis,UniverseSync
from .eastmoney import stock,plain,default_client,STOCKS
from .quotes import symbol
from .research import utc

router=APIRouter(prefix='/api/live/community')
SOURCES={'sina':'新浪股吧','ths':'同花顺圈子','taoguba':'淘股吧'}


def page_url(source,iid):
    code=stock(iid)[0]
    if source=='sina':return f'https://guba.sina.cn/list_{symbol(iid)}.html'
    if source=='ths':return f'https://t.10jqka.com.cn/guba/{code}/'
    if source=='taoguba':return f'https://www.tgb.cn/quotes/{symbol(iid)}'
    raise ValueError('未知社区来源')


class Nodes(HTMLParser):
    """Small DOM projection sufficient for source identity and visible post fields."""
    def __init__(self):
        super().__init__();self.root={'tag':'root','attrs':{},'children':[]};self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        node={'tag':tag,'attrs':dict(attrs),'children':[]};self.stack[-1]['children'].append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):self.stack.append(node)
    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,0,-1):
            if self.stack[index]['tag']==tag:self.stack=self.stack[:index];break
    def handle_data(self,data):self.stack[-1]['children'].append(data)


def nodes(node):
    yield node
    for child in node['children']:
        if isinstance(child,dict):yield from nodes(child)


def text(node):
    if node['tag'] in ('script','style'):return ''
    return ' '.join(child if isinstance(child,str) else text(child) for child in node['children']).strip()


def class_text(node,cls):
    return ' '.join(text(n) for n in nodes(node) if cls in n['attrs'].get('class','').split()).strip()


def js_value(document,name):
    match=re.search(r'\bvar\s+'+re.escape(name)+r'\s*=\s*',document)
    if not match:raise ValueError('未找到证券身份或公开列表字段')
    return json.JSONDecoder().raw_decode(document[match.end():])[0]


def parse_page(source,document,iid):
    if source not in SOURCES:raise ValueError('未知社区来源')
    parser=Nodes();parser.feed(document);root=parser.root
    samples=[];excluded={};code=stock(iid)[0]
    if source=='sina':
        cfg=js_value(document,'PAGE_CONFIG')
        if not isinstance(cfg,dict):raise ValueError('新浪证券身份字段格式变化')
        if cfg.get('stockCode')!=code or cfg.get('stockMarket')!=symbol(iid)[:2]:raise ValueError('新浪页面证券身份不匹配')
        for node in nodes(root):
            if node['tag']!='article' or 'j-post' not in node['attrs'].get('class','').split():continue
            attrs=node['attrs'];bid,tid,author=attrs.get('data-bid',''),attrs.get('data-tid',''),attrs.get('data-uid','')
            body='\n'.join(filter(None,[class_text(node,'title'),class_text(node,'content')]))
            displayed=class_text(node,'time')
            if not all(re.fullmatch(r'\d{1,24}',v) for v in (bid,tid,author)) or not body or not displayed:
                excluded['missing_fields']=excluded.get('missing_fields',0)+1;continue
            samples.append({'id':f'{bid}_{tid}','author_hash':sha256(f'sina:{author}'.encode()).hexdigest(),
                'text':body[:3000],'source_time':displayed,'published_at':None,
                'url':f'https://guba.sina.cn/view_{bid}_{tid}.html','content':'公开列表可见文本，可能截断'})
    elif source=='ths':
        identity=next((n['attrs'] for n in nodes(root) if n['attrs'].get('id')=='globalParam'),{})
        if identity.get('data-code')!=code:raise ValueError('同花顺页面证券身份不匹配')
        # An empty static stock page is not evidence of zero community discussion.
        return [],{'excluded':{},'note':'已核验个股社区入口；当前静态页面未提供可核验作者和时间的帖子，不以投顾首页代替散户样本。'}
    else:
        info=js_value(document,'stockInfo')
        if not isinstance(info,dict):raise ValueError('淘股吧证券身份字段格式变化')
        # Beijing pages return the exchange-prefixed code in both fields.
        if info.get('fullCode')!=symbol(iid) or info.get('code') not in (code,symbol(iid)):raise ValueError('淘股吧页面证券身份不匹配')
        rows=js_value(document,'coolAttr')
        if not isinstance(rows,list) or len(rows)>100:raise ValueError('淘股吧列表格式变化')
        for row in rows:
            if not isinstance(row,dict):raise ValueError('淘股吧帖子字段格式变化')
            # Replies, quotations, reposts and parent-topic counts are not standalone posts.
            if row.get('rType')!='T' or row.get('deleteFlag')!='N':
                excluded['reply_or_other']=excluded.get('reply_or_other',0)+1;continue
            tid=str(row.get('rID',''));author=str(row.get('userID',''));slug=row.get('newTopicID','')
            body='\n'.join(filter(None,[plain(row.get('subject') or ''),plain(row.get('body') or '')]))
            if not re.fullmatch(r'\d{1,24}',tid) or not re.fullmatch(r'\d{1,24}',author) or not re.fullmatch(r'[A-Za-z0-9]{5,40}',slug) or not body:
                excluded['missing_fields']=excluded.get('missing_fields',0)+1;continue
            samples.append({'id':tid,'author_hash':sha256(f'taoguba:{author}'.encode()).hexdigest(),
                'text':body[:3000],'source_time':row.get('actionDate') or '', 'published_at':None,
                'url':f'https://www.tgb.cn/a/{slug}','content':'公开标题和摘要；actionDate为活动时间，尚未当作发布时间'})
    # Repeated layout entries must not inflate post or author counts.
    unique={}
    for sample in samples:
        if sample['id'] not in unique:unique[sample['id']]=sample
    samples=list(unique.values())[:30]
    return samples,{'excluded':excluded,'note':'样本按本次观察时间留存；源页面未提供完整、已核验的发布时间，暂不并入历史情绪或回测。'}


def probe(db,client,source,iid):
    url=page_url(source,iid);item=db.get(Instrument,iid)
    if not item or not ((item.scope or {}).get('live_directory') or iid in STOCKS):raise ValueError('请先选择已核验的A股')
    now=datetime.now(timezone.utc)
    previous=db.scalar(select(CommunityProbe).where(CommunityProbe.source==source,CommunityProbe.instrument_id==iid).order_by(CommunityProbe.id.desc()))
    if previous and now-utc(previous.started_at)<timedelta(minutes=10):return previous,True
    barrier=db.scalar(select(CommunityProbe).where(CommunityProbe.source==source,CommunityProbe.status=='blocked',CommunityProbe.started_at>now-timedelta(minutes=30)).order_by(CommunityProbe.id.desc()))
    if barrier:raise ValueError(f'{SOURCES[source]}近期要求验证或限流，该来源暂停30分钟；其他来源可独立验证')
    row=CommunityProbe(source=source,instrument_id=iid,started_at=now,details={'url':url});db.add(row);db.commit()
    details={'url':url}
    try:
        with client.stream('GET',url,timeout=15,follow_redirects=False) as response:
            row.http_status=response.status_code
            if response.status_code in (401,403,423,429,451) or 300<=response.status_code<400:
                row.status='blocked';details['error']='源站拒绝、限流或要求跳转，未跟随或重试'
            elif response.status_code!=200:
                row.status='http_failed';details['error']=f'源站HTTP {response.status_code}'
            else:
                body=bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body)>2_000_000:raise ValueError('源页面超过2MB限制')
                encoding=response.encoding or 'utf-8'
                if encoding.lower().replace('_','-') not in ('utf-8','gbk','gb2312','gb18030'):raise ValueError('未知页面编码')
                document=body.decode(encoding);details['response_sha256']=sha256(body).hexdigest()
                details['encoding']=encoding
                if re.search(r'<title[^>]*>\s*(身份核实|安全验证|访问验证|人机验证|访问受限|Access Denied|Just a moment)',document,re.I):
                    row.status='blocked';details['error']='源站要求身份核实，未处理验证码'
                else:
                    row.samples,meta=parse_page(source,document,iid);details.update(meta)
                    row.status='samples' if row.samples else 'entry_only'
    except httpx.HTTPError:
        row.status='network_failed';details['error']='公开页面网络连接失败'
    except (ValueError,TypeError,KeyError,UnicodeError):
        row.status='format_failed';details['error']='证券身份、编码或帖子字段校验未通过；未发布可用样本'
    row.finished_at=datetime.now(timezone.utc);row.details=details;db.commit();return row,False


def observation(row):
    return {'id':row.id,'source':row.source,'instrument_id':row.instrument_id,'status':row.status,'http_status':row.http_status,
        'observed_at':utc(row.finished_at).isoformat() if row.finished_at else None,'details':row.details,
        'sample_count':len(row.samples),'author_count':len({r['author_hash'] for r in row.samples}),
        'verified_publication_count':sum(r.get('published_at') is not None for r in row.samples),
        'samples':[{k:v for k,v in r.items() if k!='author_hash'} for r in row.samples]}


def source_status(db,iid):
    now=datetime.now(timezone.utc)
    # Coverage refresh must not load every stock's entire text archive into RAM.
    rows=list(db.execute(select(CommunityProbe.source,CommunityProbe.instrument_id,CommunityProbe.status,
        CommunityProbe.http_status,CommunityProbe.details,func.json_array_length(CommunityProbe.samples).label('sample_count'))
        .where(CommunityProbe.started_at>=now-timedelta(days=7)).order_by(CommunityProbe.id.desc())))
    universe=db.scalar(select(UniverseSync).where(UniverseSync.status=='success').order_by(UniverseSync.id.desc()))
    result=[]
    for source,name in SOURCES.items():
        source_rows=[r for r in rows if r.source==source]
        attempts=[r for r in source_rows if not r.details.get('diagnostic_reparse')]
        latest=db.scalar(select(CommunityProbe).where(CommunityProbe.source==source,CommunityProbe.instrument_id==iid).order_by(CommunityProbe.id.desc()))
        successes=[r for r in attempts if r.status=='samples'];completed=[r for r in attempts if r.status!='running']
        analysis=db.scalar(select(CommunityAnalysis).where(CommunityAnalysis.probe_id==latest.id).order_by(CommunityAnalysis.id.desc())) if latest else None
        good=db.scalar(select(CommunityAnalysis).join(CommunityProbe,CommunityProbe.id==CommunityAnalysis.probe_id).where(
            CommunityProbe.source==source,CommunityProbe.instrument_id==iid,CommunityAnalysis.status=='success').order_by(CommunityAnalysis.id.desc()))
        from .sentiment_ai import analysis_dict
        result.append({'source':source,'name':name,'url':page_url(source,iid),'latest':observation(latest) if latest else None,
            'analysis':analysis_dict(analysis) if analysis else None,'last_success':analysis_dict(good) if good else None,
            'attempts':len(completed),'reachable_rate':round(sum(r.http_status==200 and r.status!='blocked' for r in completed)/len(completed)*100,1) if completed else None,
            'sample_success_rate':round(len(successes)/len(completed)*100,1) if completed else None,
            'attempted_stocks':len({r.instrument_id for r in completed}),'covered_stocks':len({r.instrument_id for r in successes}),
            'sample_observations':sum(r.sample_count for r in successes)})
    return {'instrument_id':iid,'sources':result,'universe_size':universe.total if universe else None,
        'method':'最近7自然日实际请求；十分钟内复用不增加分母。可读样本成功率与入口可达率分开；样本观察数包含重复采集，不代表独立帖子或全市场覆盖。'}


class ProbeBody(BaseModel):
    model_config={'extra':'forbid'}
    instrument_id:str=Field(max_length=64)
    source:str=Field(pattern='^(sina|ths|taoguba)$')


@router.get('/sources')
def sources(instrument_id:str=Query('XSHE:300059',max_length=64),db=Depends(get_db)):
    try:return source_status(db,instrument_id)
    except ValueError as exc:raise HTTPException(422,str(exc))


@router.get('/ai')
def ai_status(request:Request,db=Depends(get_db)):
    from .local_collection import enabled_for
    from .sentiment_ai import configuration
    if not enabled_for(request):return {'configured':False,'error':'模型配置仅在本机服务显示'}
    return configuration(db)


class AnalysisBody(BaseModel):
    model_config={'extra':'forbid'}
    probe_id:int=Field(gt=0)
    mode:str=Field(default='ai',pattern='^(ai|rules)$')


@router.post('/analyze')
def analyze(body:AnalysisBody,request:Request,db=Depends(get_db)):
    from .local_collection import enabled_for
    from .scanner import process_lock
    from .sentiment_ai import analyze_probe,analysis_dict
    if not enabled_for(request):raise HTTPException(403,'样本分类仅在本机服务开放')
    try:
        with process_lock('sentiment-ai.lock',wait_seconds=20),default_client() as client:
            row,reused=analyze_probe(db,client,db.get(CommunityProbe,body.probe_id),body.mode)
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'analysis':analysis_dict(row),'reused':reused}


@router.post('/probe')
def validate(body:ProbeBody,request:Request,db=Depends(get_db)):
    from .local_collection import enabled_for
    from .collect import collector_lock
    if not enabled_for(request):raise HTTPException(403,'来源验证仅在本机服务开放')
    try:
        with collector_lock(wait_seconds=15),default_client() as client:row,reused=probe(db,client,body.source,body.instrument_id)
    except ValueError as exc:raise HTTPException(409,str(exc))
    return {'observation':observation(row),'reused':reused}
