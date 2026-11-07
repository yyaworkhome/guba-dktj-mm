"""Authenticated real-sample rules and watchlist comparison. Database reads only."""
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from .database import get_db
from .auth import current_user
from .models import LiveAlertRule, Instrument, WatchItem
from .eastmoney import STOCKS, stock
from .live import live_analysis, RANK_MINIMUMS

router = APIRouter(prefix='/api/live')


def supported(db,iid):
    try: stock(iid)
    except ValueError: return False
    item=db.get(Instrument,iid)
    return iid in STOCKS or bool(item and (item.scope or {}).get('live_directory'))


def require_supported(db,iid):
    if not supported(db,iid): raise HTTPException(422,'请先按股票名称或代码搜索并选择已核验的A股')


def sample_quality(a):
    reasons = []
    directional = a['counts']['bullish'] + a['counts']['bearish']
    for key, label, value in [('effective_posts', '有效样本', a['effective_posts']),
                             ('authors', '有效作者', a['authors']), ('directional_posts', '方向样本', directional)]:
        if value < RANK_MINIMUMS[key]: reasons.append(f'{label}{value}，少于{RANK_MINIMUMS[key]}')
    if a['stale']: reasons.append('尚未采集或观察超过24小时')
    return reasons


class LiveAlertBody(BaseModel):
    instrument_id: str = Field(max_length=64)
    content: Literal['titles', 'body'] = 'titles'
    days: int = Field(default=3, ge=1, le=30)
    metric: Literal['bullish_pct', 'bearish_pct', 'opinion_score'] = 'bullish_pct'
    threshold: float = Field(ge=0, le=100, allow_inf_nan=False)


@router.post('/alerts')
def create_rule(body: LiveAlertBody, user=Depends(current_user), db=Depends(get_db)):
    require_supported(db,body.instrument_id)
    try: code, name, _ = stock(body.instrument_id)
    except ValueError as exc: raise HTTPException(422, str(exc))
    values = body.model_dump()
    existing = db.scalar(select(LiveAlertRule).where(LiveAlertRule.user_id == user.id,
        *(getattr(LiveAlertRule, key) == value for key, value in values.items())))
    if existing: return {'id': existing.id}
    if db.scalar(select(func.count()).select_from(LiveAlertRule).where(LiveAlertRule.user_id == user.id)) >= 50:
        raise HTTPException(409, '最多保存50条真实预警，请先移除不再使用的条件')
    if not db.get(Instrument, body.instrument_id):
        db.add(Instrument(id=body.instrument_id, code=code, name=name, kind='stock',
                          exchange=body.instrument_id.split(':')[0], aliases=[], scope={'boards': [name]}))
        db.flush()
    row = LiveAlertRule(user_id=user.id, **values)
    db.add(row); db.commit()
    return {'id': row.id}


@router.get('/alerts')
def rules(user=Depends(current_user), db=Depends(get_db)):
    cutoff = datetime.now(timezone.utc)
    out, cache = [], {}
    for rule in db.scalars(select(LiveAlertRule).where(LiveAlertRule.user_id == user.id).order_by(LiveAlertRule.id.desc())):
        key = (rule.instrument_id, rule.days, rule.content)
        if key not in cache:
            cache[key] = live_analysis(db, iid=rule.instrument_id, days=rule.days, content=rule.content, as_of=cutoff)
        a = cache[key]
        reasons = sample_quality(a)
        raw = a['opinion_score'] if rule.metric == 'opinion_score' else a['percentages'][rule.metric.removesuffix('_pct')]
        ready = not reasons and raw is not None
        out.append({'id': rule.id, 'instrument_id': rule.instrument_id, 'name': a['name'],
            'content': rule.content, 'days': rule.days, 'metric': rule.metric, 'threshold': rule.threshold,
            'value': raw if ready else None, 'raw_value': raw, 'ready': ready,
            'triggered': ready and raw >= rule.threshold, 'reasons': reasons,
            'effective_posts': a['effective_posts'], 'authors': a['authors'],
            'last_observation': a['last_post_observation'], 'as_of': cutoff.isoformat(), 'data_mode': 'live'})
    return out


@router.delete('/alerts/{rule_id}')
def delete_rule(rule_id: int, user=Depends(current_user), db=Depends(get_db)):
    db.execute(delete(LiveAlertRule).where(LiveAlertRule.id == rule_id, LiveAlertRule.user_id == user.id))
    db.commit()
    return {'ok': True}


@router.get('/watchlist')
def watchlist(days: int = Query(3, ge=1, le=30), content: Literal['titles','body'] = 'titles',
              user=Depends(current_user), db=Depends(get_db)):
    cutoff = datetime.now(timezone.utc)
    out = []
    rows = db.execute(select(WatchItem, Instrument).join(Instrument, Instrument.id == WatchItem.instrument_id)
                      .where(WatchItem.user_id == user.id).order_by(WatchItem.id)).all()
    for _, i in rows:
        item = {'id': i.id, 'code': i.code, 'name': i.name, 'kind': i.kind, 'exchange': i.exchange,
                'supported': supported(db,i.id), 'analysis': None}
        if item['supported']:
            a = live_analysis(db, iid=i.id, days=days, content=content, as_of=cutoff)
            item['analysis'] = {key: a[key] for key in ('opinion_score','effective_posts','authors',
                'counts','percentages','last_post_observation','stale')}
            item['analysis']['reasons'] = sample_quality(a)
        out.append(item)
    return {'items': out, 'days': days, 'content': content, 'as_of': cutoff.isoformat(), 'data_mode': 'live'}


class LiveWatchBody(BaseModel):
    instrument_id: str = Field(max_length=64)


@router.put('/watchlist')
def add_watch(body: LiveWatchBody, user=Depends(current_user), db=Depends(get_db)):
    require_supported(db,body.instrument_id)
    try: code, name, _ = stock(body.instrument_id)
    except ValueError as exc: raise HTTPException(422, str(exc))
    if not db.get(Instrument, body.instrument_id):
        db.add(Instrument(id=body.instrument_id, code=code, name=name, kind='stock',
                          exchange=body.instrument_id.split(':')[0], aliases=[], scope={'boards':[name]}))
        db.flush()
    if not db.scalar(select(WatchItem).where(WatchItem.user_id == user.id, WatchItem.instrument_id == body.instrument_id)):
        db.add(WatchItem(user_id=user.id, instrument_id=body.instrument_id))
        try: db.commit()
        except IntegrityError: db.rollback()
    return {'ok': True}
