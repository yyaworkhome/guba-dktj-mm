"""Run explicitly: python -m backend.collect. Two source requests, no pagination."""
import argparse
import json
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .database import Base, engine, SessionLocal, DATA_DIR
from .models import Instrument, CollectionRun, QuoteObservation, Snapshot
from .ingestion import record_post, classify_revision
from .eastmoney import (IID, SOURCE, BODY_SOURCE, CST, STOCKS, SourceError, SourceExcluded, stock,
    list_url, kline_url, detail_url, default_client, get_document, parse_titles, parse_body, parse_bars, document_digest)
from .live import live_analysis, run_dict
from .research import utc
from .securities import ensure_stock
import httpx


@contextmanager
def collector_lock():
    """CLI and local web requests share the same exclusive local lock."""
    lock = DATA_DIR / 'collector.lock'
    try:
        handle = lock.open('x')
    except FileExistsError as exc:
        raise ValueError('已有采集正在运行或存在遗留锁，请先检查采集状态。') from exc
    try:
        with handle:
            yield
    finally:
        lock.unlink(missing_ok=True)

def collect_bodies(client, candidates, iid, limit):
    chosen = sorted(candidates, key=lambda p: (p['published_at'], int(p['source_id'])), reverse=True)[:limit]
    posts, documents, errors, excluded = [], [], [], []
    for n, candidate in enumerate(chosen):
        if n: time.sleep(1)
        url = detail_url(iid, candidate['source_id'])
        try:
            document = get_document(client, url)
            observed = datetime.now(timezone.utc)
            documents.append({'url': url, 'response_sha256': document_digest(document), 'observed_at': observed.isoformat()})
            posts.append(parse_body(document, observed, candidate, iid))
        except SourceExcluded as exc:
            excluded.append({'url': url, 'reason': str(exc)})
        except (httpx.HTTPError, SourceError) as exc:
            errors.append({'url': url, 'error': str(exc) if isinstance(exc, SourceError) else f'网络错误：{type(exc).__name__}'})
            # Stop the detail batch at the first rejection/structure error.
            break
    return posts, {'requested': len(chosen), 'documents': documents, 'errors': errors, 'detail_excluded': excluded,
        'not_attempted': len(chosen)-len(posts)-len(errors)-len(excluded)}

def collect(db, client, iid=IID, content='titles', limit=3):
    code, name, _ = stock(iid)
    if content not in ('titles', 'body') or not 1 <= limit <= 5: raise ValueError('采集类型或正文数量无效（1～5条）')
    source = SOURCE if content == 'titles' else BODY_SOURCE
    latest = db.scalar(select(CollectionRun).where(CollectionRun.instrument_id == iid).order_by(CollectionRun.id.desc()))
    now = datetime.now(timezone.utc)
    if latest and now - utc(latest.started_at) < timedelta(minutes=10):
        raise ValueError('采样间隔至少10分钟，请稍后运行；没有发起源站请求。')
    ensure_stock(db, iid, client)
    run = CollectionRun(instrument_id=iid, started_at=now, status='running')
    db.add(run); db.commit()
    details = {'_meta': {'source': source, 'content': content}}
    for name, url, parser in [('posts', list_url(iid), parse_titles), ('quotes', kline_url(iid), parse_bars)]:
        try:
            document = get_document(client, url)
            observed = datetime.now(timezone.utc)
            parsed = parser(document, observed, iid)
            body_stats = {}
            if name == 'posts' and content == 'body':
                candidates, stats = parsed
                posts, body_stats = collect_bodies(client, candidates, iid, limit)
                observed = datetime.now(timezone.utc)
                parsed = posts, {**stats, 'eligible_candidates': len(candidates), 'accepted': len(posts), **body_stats}
            # Source transaction isolation: either this whole response persists,
            # or it rolls back while the independent other source can succeed.
            with db.begin_nested():
                if name == 'posts':
                    posts, stats = parsed
                    revisions = 0
                    for p in posts:
                        _, revision, created = record_post(db, source=source, instrument_id=iid, **p)
                        if created: classify_revision(db, revision); revisions += 1
                    meta = {**stats, 'new_revisions': revisions, 'unchanged': len(posts)-revisions}
                else:
                    db.add(QuoteObservation(run_id=run.id, instrument_id=iid, observed_at=observed, bars=parsed))
                    meta = {'bars': len(parsed), 'adjustment': 'none'}
                db.flush()
            details[name] = {'status': 'ok', 'url': url, 'response_sha256': document_digest(document),
                'observed_at': observed.isoformat(), **meta}
            if name == 'posts' and body_stats.get('errors'):
                details[name]['status'] = 'partial' if posts else 'failed'
                details[name]['error'] = body_stats['errors'][0]['error']
        except (httpx.HTTPError, SourceError) as exc:
            # Store only a controlled diagnostic, never response bodies/tokens.
            error = str(exc) if isinstance(exc, SourceError) else f'网络错误：{type(exc).__name__}'
            details[name] = {'status': 'failed', 'url': url, 'error': error}
        except Exception:
            # Do not commit a partial database write or leave the run "running".
            db.rollback()
            run = db.get(CollectionRun, run.id)
            run.status = 'failed'; run.finished_at = datetime.now(timezone.utc)
            run.details = {'_meta': {'source': source, 'content': content},
                'posts': {'status': 'failed', 'error': '数据库或内部处理失败；本次未发布成功快照'}}
            db.commit()
            raise
    states = [details[key]['status'] for key in ('posts', 'quotes')]
    run.status = 'success' if all(s == 'ok' for s in states) else 'partial' if any(s in ('ok', 'partial') for s in states) else 'failed'
    run.details = details
    run.finished_at = datetime.now(timezone.utc)
    if details['posts']['status'] in ('ok', 'partial'):
        # This is a collection-time snapshot, never a backdated market signal.
        db.flush()
        metrics = live_analysis(db, days=1, as_of=run.finished_at, iid=iid, content=content)
        db.add(Snapshot(instrument_id=iid, session_date=run.finished_at.astimezone(CST).date().isoformat(),
            available_at=run.finished_at, version=f'live-{content}-run-{run.id}', data_mode='live', metrics=metrics))
    db.commit()
    return run_dict(run)

def main():
    parser = argparse.ArgumentParser(description='公开股吧限量采样；默认标题，正文每批最多5条')
    parser.add_argument('--instrument', default=IID, help='明确沪深北A股ID，例如XSHE:002594；新证券先核验目录')
    parser.add_argument('--content', default='titles', choices=['titles', 'body'])
    parser.add_argument('--limit', default=3, type=int, choices=range(1,6))
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    try:
        with collector_lock(), SessionLocal() as db, default_client() as client:
            result = collect(db, client, args.instrument, args.content, args.limit)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'success' else 1
    except ValueError as exc:
        print(str(exc)); return 1

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
