"""Real observations have an isolated API and cannot feed the demo backtest."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .eastmoney import IID, SOURCE, BODY_SOURCE, CST, STOCKS, stock, list_url
from .models import Post, PostRevision, Classification, CollectionRun, QuoteObservation, Snapshot, Instrument
from .research import utc, opinion_score, content_hash
from .interpretation import interpret_sample

def run_dict(run):
    if not run: return None
    return {'id': run.id, 'status': run.status, 'started_at': utc(run.started_at).isoformat(),
        'finished_at': utc(run.finished_at).isoformat() if run.finished_at else None, 'details': run.details}

def source_for(content):
    if content not in ('titles', 'body'): raise ValueError('不支持的内容口径')
    return BODY_SOURCE if content == 'body' else SOURCE

def run_source(run): return run.details.get('_meta', {}).get('source', SOURCE)

def live_catalog(q='',db=None):
    q = q.strip().lower()
    rows = {iid: {'id':iid,'code':code,'name':name,'exchange':iid.split(':')[0],'kind':'stock'}
        for iid,(code,name,_) in STOCKS.items()}
    if db is not None:
        for i in db.scalars(select(Instrument).where(Instrument.kind=='stock')):
            try: stock(i.id)
            except ValueError: continue
            meta=(i.scope or {}).get('live_directory')
            if meta:
                rows[i.id]={'id':i.id,'code':i.code,'name':meta['name'],'exchange':i.exchange,'kind':'stock'}
    return [r for r in rows.values() if not q or q in f'{r["id"]} {r["code"]} {r["name"]}'.lower()]

def source_status(db, iid=IID, content='titles'):
    stock(iid); source = source_for(content)
    latest = next((run for run in db.scalars(select(CollectionRun).where(CollectionRun.instrument_id == iid)
        .order_by(CollectionRun.id.desc())) if run_source(run) == source), None)
    return {'instrument_id': iid, 'source': source, 'latest_run': run_dict(latest),
        'collection': '手动限频采样；刷新页面只读取本地数据库，不发起源站请求。',
        'scope': '股吧首页普通讨论' + ('标题采样；不含正文。' if content == 'titles' else '正文限量采样；不含评论内容。')}


RANK_MINIMUMS = {'effective_posts': 20, 'authors': 10, 'directional_posts': 10}


def snapshot_timeline(db, iid=IID, content='titles', as_of=None, days=30):
    """Saved collection-time values only. Never recreate a historical point from current posts."""
    stock(iid); source = source_for(content)
    cutoff = as_of or datetime.now(timezone.utc)
    start = cutoff - timedelta(days=days)
    points = []
    rows = db.scalars(select(Snapshot).where(Snapshot.instrument_id == iid, Snapshot.data_mode == 'live',
        Snapshot.available_at >= start, Snapshot.available_at <= cutoff, Snapshot.created_at <= cutoff)
        .order_by(Snapshot.available_at, Snapshot.id))
    for row in rows:
        metrics = row.metrics
        if metrics.get('source') != source or metrics.get('instrument_id') != iid:
            continue
        if metrics.get('content', 'titles') != content:
            continue
        counts = metrics.get('counts', {})
        n = metrics.get('effective_posts', 0)
        directional = counts.get('bullish', 0) + counts.get('bearish', 0)
        points.append({'id': row.id, 'available_at': utc(row.available_at).isoformat(),
            'session_date': row.session_date, 'version': row.version,
            'opinion_score': metrics.get('opinion_score') if n and directional else None,
            'effective_posts': n, 'directional_posts': directional, 'authors': metrics.get('authors', 0),
            'window_days': metrics.get('days'), 'range': metrics.get('range'),
            'counts': {k: counts.get(k, 0) for k in ('bullish', 'bearish', 'neutral')}})
    # Bound the UI without averaging or changing historical values.
    return {'instrument_id': iid, 'content': content, 'as_of': cutoff.isoformat(),
        'points': points[-200:], 'total_points': len(points), 'truncated': len(points) > 200,
        'basis': '最近30天内保存的采集时点快照，每点保留当时统计窗口；无方向样本显示缺失，不与发帖日期分组混用。'}


def live_rankings(db, days=3, content='titles', sort='opinion_score', as_of=None):
    if sort not in ('opinion_score', 'bullish_pct', 'bearish_pct', 'effective_posts'):
        raise ValueError('不支持的排行榜排序')
    cutoff = as_of or datetime.now(timezone.utc)
    rows = []
    collected = set(db.scalars(select(CollectionRun.instrument_id).distinct())) | set(STOCKS)
    instruments = [i for i in live_catalog(db=db) if i['id'] in collected]
    for instrument in instruments:
        a = live_analysis(db, days=days, as_of=cutoff, iid=instrument['id'], content=content)
        directional = a['counts']['bullish'] + a['counts']['bearish']
        reasons = []
        for key, label, value in [('effective_posts', '有效样本', a['effective_posts']),
                                 ('authors', '有效作者', a['authors']), ('directional_posts', '方向样本', directional)]:
            if value < RANK_MINIMUMS[key]: reasons.append(f'{label}{value}，少于{RANK_MINIMUMS[key]}')
        if a['stale']: reasons.append('尚未采集或观察超过24小时')
        rows.append({**instrument, 'counts': a['counts'], 'effective_posts': a['effective_posts'],
            'authors': a['authors'], 'directional_posts': directional, 'opinion_score': a['opinion_score'],
            'bullish_pct': a['percentages']['bullish'], 'bearish_pct': a['percentages']['bearish'],
            'last_observation': a['last_post_observation'], 'stale': a['stale'], 'eligible': not reasons,
            'reasons': reasons})
    ranked = sorted((r for r in rows if r['eligible']),
                    key=lambda r: (-(r[sort] if r[sort] is not None else -1), r['id']))
    for i, r in enumerate(ranked, 1): r['rank'] = i
    return {'data_mode': 'live', 'as_of': cutoff.isoformat(), 'days': days, 'content': content,
        'sort': sort, 'minimums': RANK_MINIMUMS, 'ranked': ranked,
        'unranked': [r for r in rows if not r['eligible']], 'scope_size': len(instruments),
        'scope': '已采集股票和初始观察样本，同一自然日窗口与内容口径；不是全市场排名。',
        'limitations': ['最低样本门槛为开发阈值，未完成统计校准；达标不代表具有预测价值。',
            '不同股吧采集时间与选择偏差仍可能不同；排序只比较已保存的有限样本。',
            '有效样本量不是单位时间新增发帖量，不代表热度增长；不计算升温或收益预测。']}

def live_analysis(db, days=1, as_of=None, iid=IID, content='titles'):
    code, name, _ = stock(iid); source = source_for(content)
    instrument=db.get(Instrument,iid)
    if instrument: name=((instrument.scope or {}).get('live_directory') or {}).get('name',instrument.name)
    cutoff = as_of or datetime.now(timezone.utc)
    local_day = cutoff.astimezone(CST).date()
    start = datetime.combine(local_day - timedelta(days=days-1), datetime.min.time(), CST).astimezone(timezone.utc)
    pairs = db.execute(select(Post, PostRevision).join(PostRevision, PostRevision.post_id == Post.id).where(
        Post.source == source, Post.instrument_id == iid, Post.published_at >= start,
        Post.published_at <= cutoff, Post.first_seen_at <= cutoff, PostRevision.observed_at <= cutoff
    ).order_by(PostRevision.id)).all()
    # Select the latest revision visible at the cutoff, not today's revision of old text.
    latest = {p.id: (p, r) for p, r in pairs}
    revisions = [r.id for p, r in latest.values()]
    classified = {}
    if revisions:
        for c in db.scalars(select(Classification).where(Classification.revision_id.in_(revisions),
            Classification.classified_at <= cutoff, Classification.model_version == 'rules-v1').order_by(Classification.id)):
            classified[c.revision_id] = c
    seen, authors, effective, removed = set(), Counter(), [], Counter()
    for p, r in sorted(latest.values(), key=lambda pair: (utc(pair[0].published_at), pair[0].id)):
        c = classified.get(r.id)
        if not c: removed['unclassified'] += 1; continue
        digest = content_hash(r.text)
        if digest in seen: removed['duplicate_content'] += 1; continue
        # The cap is per local calendar day, not across the entire selected window.
        key = (p.author_hash, utc(p.published_at).astimezone(CST).date())
        if authors[key] >= 3: removed['author_cap'] += 1; continue
        seen.add(digest); authors[key] += 1
        effective.append((p, r, c))
    counts = Counter(c.result['label'] for p, r, c in effective)
    n = len(effective)
    quote = db.scalar(select(QuoteObservation).where(QuoteObservation.instrument_id == iid,
        QuoteObservation.observed_at <= cutoff).order_by(QuoteObservation.id.desc()))
    last_observation = max((utc(r.observed_at) for p, r in latest.values()), default=None)
    # An unchanged title still counts as a fresh source observation. Its content
    # revision remains untouched; the run records that it was checked again.
    for run in db.scalars(select(CollectionRun).where(CollectionRun.instrument_id == iid,
        CollectionRun.finished_at <= cutoff).order_by(CollectionRun.id.desc())):
        if run_source(run) != source: continue
        observation = run.details.get('posts', {})
        if observation.get('status') in ('ok', 'partial') and observation.get('observed_at'):
            checked = datetime.fromisoformat(observation['observed_at'])
            last_observation = max(filter(None, (last_observation, checked)))
            break
    tags = Counter(tag for p, r, c in effective for tag in c.result.get('tags', []))
    daily = {}
    for p, r, c in effective:
        day = utc(p.published_at).astimezone(CST).date().isoformat()
        daily.setdefault(day, Counter())[c.result['label']] += 1
    stale = last_observation is None or cutoff - last_observation > timedelta(hours=24)
    quote_data = {'observed_at': utc(quote.observed_at).isoformat(), 'bars': quote.bars} if quote else None
    author_count = len({p.author_hash for p, r, c in effective})
    return {'data_mode': 'live', 'instrument_id': iid, 'code': code, 'name': name, 'source': source,
        'content': content, 'source_url': list_url(iid),
        'collector_command': f'.venv/Scripts/python.exe -m backend.collect --instrument {iid} --content {content}',
        'as_of': cutoff.isoformat(), 'last_post_observation': last_observation.isoformat() if last_observation else None,
        'stale': stale,
        'days': days, 'range': [start.astimezone(CST).date().isoformat(), local_day.isoformat()], 'time_basis': 'Asia/Shanghai自然日，非交易日',
        'sampled_posts': len(latest), 'effective_posts': n, 'removed': dict(removed),
        'authors': author_count,
        'interpretation': interpret_sample(counts, n, author_count, stale, quote_data),
        'counts': {k: counts[k] for k in ('bullish', 'bearish', 'neutral')},
        'percentages': {k: round(counts[k]/n*100, 1) if n else None for k in ('bullish', 'bearish', 'neutral')},
        'opinion_score': opinion_score(counts['bullish'], counts['bearish']),
        'coverage': 35 if counts['bullish']+counts['bearish'] else 0,
        'comments': sum(r.engagement.get('comments', 0) for p, r, c in effective),
        'reads': sum(r.engagement.get('reads', 0) for p, r, c in effective),
        'keywords': [{'text': tag, 'count': count} for tag, count in tags.most_common(10)],
        'trend': [{'date': day, 'posts': sum(cs.values()), 'opinion_score': opinion_score(cs['bullish'], cs['bearish'])} for day, cs in sorted(daily.items())],
        'quote': quote_data,
        'examples': [{'source_id': p.source_id, 'excerpt': r.text[:180], 'url': r.engagement.get('url'),
            'label': c.result['label'], 'published_at': utc(p.published_at).isoformat(),
            'first_seen_at': utc(p.first_seen_at).isoformat(), 'observed_at': utc(r.observed_at).isoformat()}
            for p, r, c in effective[-5:][::-1]],
        'status': source_status(db, iid, content) if as_of is None else None,
        'limitations': [('仅首页普通讨论标题采样。' if content == 'titles' else '每批最多5条普通讨论正文，优先选择列表中发布时间最新的候选；正文失败不回退到标题。') + '列表按最后更新时间排序，存在活跃讨论选择偏差，不代表全体散户。',
            '规则分类尚未接入AI；中性包含未能识别方向，不能解释为真实中性观点。',
            '正文规则暂不区分同一帖子提及的不同证券、引用和反讽；长帖方向需人工或AI复核。',
            '标题与正文分开查询，不能相加；相同内容去重，每位作者每天最多计3条；阅读/评论为当前累计计数，不是所选期间新增量。',
            '仅舆论维度可用，占规划权重35%；舆论分不等于五维综合指数。',
            '自然日内无采样的日期不补零；行情为未复权日线，当前交易日可能尚未收盘。',
            '首次发现、修订及分类均按实际采集时间保存；没有可验证的历史信号时不计算真实胜率和背离。']}
