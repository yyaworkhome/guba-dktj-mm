"""Opt-in manual collection control for the local workstation only."""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from .auth import current_user
from .database import get_db, DATA_DIR
from .models import CollectionRun,MarketScan
from .eastmoney import IID, stock, default_client
from .collect import collect, collector_lock,collector_busy
from .live import run_dict, live_analysis
from .research import utc

router = APIRouter(prefix='/api/live')
LOCAL_NAMES = {'127.0.0.1', 'localhost', '::1'}


def enabled_for(request):
    if os.getenv('ALLOW_LOCAL_COLLECTION', '').lower() != 'true':
        return False
    # No forwarded headers are trusted. A production proxy must leave this off.
    if not request.client or request.client.host not in {'127.0.0.1', '::1'}:
        return False
    if request.url.hostname not in LOCAL_NAMES:
        return False
    origin = request.headers.get('origin')
    if origin:
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in {'http', 'https'} or parsed.hostname not in LOCAL_NAMES:
                return False
        except ValueError:
            return False
    return True


def validate(iid):
    try: stock(iid)
    except ValueError as exc: raise HTTPException(422, str(exc))


@router.get('/collection')
def status(request: Request, instrument_id: str = Query(IID, max_length=64), db=Depends(get_db)):
    validate(instrument_id)
    latest = db.scalar(select(CollectionRun).where(CollectionRun.instrument_id == instrument_id)
                       .order_by(CollectionRun.id.desc()))
    next_at = utc(latest.started_at) + timedelta(minutes=10) if latest else None
    now = datetime.now(timezone.utc)
    return {'enabled': enabled_for(request), 'locked': collector_busy(),
            'instrument_id': instrument_id, 'latest_run': run_dict(latest),
            'next_allowed_at': next_at.isoformat() if next_at and next_at > now else None,
            'minimum_interval_seconds': 600}


class Body(BaseModel):
    instrument_id: str = Field(max_length=64)
    content: Literal['titles', 'body'] = 'titles'
    limit: int = Field(default=3, ge=1, le=5)


@router.post('/collection')
def start(body: Body, request: Request, user=Depends(current_user), db=Depends(get_db)):
    if not enabled_for(request):
        raise HTTPException(403, '页面采集仅对显式启用的本机服务开放；远程部署请使用服务端采集任务。')
    validate(body.instrument_id)
    try:
        with collector_lock(), default_client() as client:
            return collect(db, client, body.instrument_id, body.content, body.limit)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except Exception:
        # Do not leak source responses, database paths or internal details.
        raise HTTPException(500, '采集内部处理失败；请刷新采集状态，不要连续重试。')


class AnalyzeBody(Body):
    days: int = Field(default=3, ge=1, le=30)


@router.post('/analyze')
def analyze(body: AnalyzeBody, request: Request, db=Depends(get_db)):
    """Local public-data analysis needs no account; private/account APIs retain auth.

    Source access still requires explicit opt-in, loopback host/client/origin,
    the shared collector lock, verified security identity and 10-minute cooldown.
    Other deployments can only analyze their previously saved observations.
    """
    validate(body.instrument_id)
    info = status(request, body.instrument_id, db)
    blocked=db.scalar(select(MarketScan).where(MarketScan.active_key=='active',MarketScan.status=='paused'))
    access_paused=bool(blocked and any(term in blocked.error for term in ('身份核实','验证码','验证或限制')))
    mode, run = 'saved', None
    if not info['enabled']:
        message = '本服务未启用页面采集，仅分析已保存样本。请用标准本机启动脚本开启采集。'
    elif access_paused:
        mode,message='source_paused','股吧源站验证尚未解除，未重新请求帖子；显示已保存样本。可点击“更新行情”独立分析价格趋势。'
    elif info['locked']:
        mode, message = 'busy', '另有采集任务正在运行，本次分析已保存样本；任务结束后可再分析。'
    elif info['next_allowed_at']:
        mode, message = 'cooldown', '十分钟采样间隔内，复用已保存的同股票、同口径样本，未重复请求源站。'
    else:
        try:
            with collector_lock(), default_client() as client:
                run = collect(db, client, body.instrument_id, body.content, body.limit)
            mode = 'collected'
            message = {'success': '采集完成，已分析本次保存的真实样本。',
                       'partial': '本次采集部分成功，请核对股吧与行情各自的状态和时间。',
                       'failed': '本次采集失败，下方仅分析此前已保存的样本。'}[run['status']]
        except ValueError as exc:
            # A concurrent request may have acquired the lock/cooldown meanwhile.
            mode, message = 'unavailable', str(exc)
        except Exception:
            raise HTTPException(500, '采集处理失败，请刷新采集状态后重试。')
    data = live_analysis(db, days=body.days, iid=body.instrument_id, content=body.content)
    return {'analysis': data, 'collection': {'mode': mode, 'message': message, 'run': run}}
