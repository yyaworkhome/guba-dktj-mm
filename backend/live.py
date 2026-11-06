"""Real observations have an isolated API and cannot feed the demo backtest."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .eastmoney import IID, SOURCE, BODY_SOURCE, CST, STOCKS, stock, list_url
from .models import Post, PostRevision, Classification, CollectionRun, QuoteObservation
from .research import utc, opinion_score, content_hash

def run_dict(run):
    if not run: return None
    return {'id': run.id, 'status': run.status, 'started_at': utc(run.started_at).isoformat(),
        'finished_at': utc(run.finished_at).isoformat() if run.finished_at else None, 'details': run.details}

def source_for(content):
    if content not in ('titles', 'body'): raise ValueError('不支持的内容口径')
    return BODY_SOURCE if content == 'body' else SOURCE

def run_source(run): return run.details.get('_meta', {}).get('source', SOURCE)

def live_catalog(q=''):
    q = q.strip().lower()
    return [{'id': iid, 'code': code, 'name': name, 'exchange': iid.split(':')[0], 'kind': 'stock'}
        for iid, (code, name, _) in STOCKS.items() if not q or q in f'{iid} {code} {name}'.lower()]

def source_status(db, iid=IID, content='titles'):
    stock(iid); source = source_for(content)
    latest = next((run for run in db.scalars(select(CollectionRun).where(CollectionRun.instrument_id == iid)
        .order_by(CollectionRun.id.desc())) if run_source(run) == source), None)
    return {'instrument_id': iid, 'source': source, 'latest_run': run_dict(latest),
        'collection': '手动限频采样；刷新页面只读取本地数据库，不发起源站请求。',
        'scope': '股吧首页普通讨论' + ('标题采样；不含正文。' if content == 'titles' else '正文限量采样；不含评论内容。')}

def live_analysis(db, days=1, as_of=None, iid=IID, content='titles'):
    code, name, _ = stock(iid); source = source_for(content)
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
    return {'data_mode': 'live', 'instrument_id': iid, 'code': code, 'name': name, 'source': source,
        'content': content, 'source_url': list_url(iid),
        'collector_command': f'.venv/Scripts/python.exe -m backend.collect --instrument {iid} --content {content}',
        'as_of': cutoff.isoformat(), 'last_post_observation': last_observation.isoformat() if last_observation else None,
        'stale': last_observation is None or cutoff - last_observation > timedelta(hours=24),
        'days': days, 'range': [start.astimezone(CST).date().isoformat(), local_day.isoformat()], 'time_basis': 'Asia/Shanghai自然日，非交易日',
        'sampled_posts': len(latest), 'effective_posts': n, 'removed': dict(removed),
        'authors': len({p.author_hash for p, r, c in effective}),
        'counts': {k: counts[k] for k in ('bullish', 'bearish', 'neutral')},
        'percentages': {k: round(counts[k]/n*100, 1) if n else None for k in ('bullish', 'bearish', 'neutral')},
        'opinion_score': opinion_score(counts['bullish'], counts['bearish']),
        'coverage': 35 if counts['bullish']+counts['bearish'] else 0,
        'comments': sum(r.engagement.get('comments', 0) for p, r, c in effective),
        'reads': sum(r.engagement.get('reads', 0) for p, r, c in effective),
        'keywords': [{'text': tag, 'count': count} for tag, count in tags.most_common(10)],
        'trend': [{'date': day, 'posts': sum(cs.values()), 'opinion_score': opinion_score(cs['bullish'], cs['bearish'])} for day, cs in sorted(daily.items())],
        'quote': {'observed_at': utc(quote.observed_at).isoformat(), 'bars': quote.bars} if quote else None,
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
