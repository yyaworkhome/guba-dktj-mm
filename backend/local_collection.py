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
from .models import CollectionRun
from .eastmoney import IID, stock, default_client
from .collect import collect, collector_lock
from .live import run_dict
from .research import utc

router = APIRouter(prefix='/api/live/collection')
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


@router.get('')
def status(request: Request, instrument_id: str = Query(IID, max_length=64), db=Depends(get_db)):
    validate(instrument_id)
    latest = db.scalar(select(CollectionRun).where(CollectionRun.instrument_id == instrument_id)
                       .order_by(CollectionRun.id.desc()))
    next_at = utc(latest.started_at) + timedelta(minutes=10) if latest else None
    now = datetime.now(timezone.utc)
    return {'enabled': enabled_for(request), 'locked': (DATA_DIR/'collector.lock').exists(),
            'instrument_id': instrument_id, 'latest_run': run_dict(latest),
            'next_allowed_at': next_at.isoformat() if next_at and next_at > now else None,
            'minimum_interval_seconds': 600}


class Body(BaseModel):
    instrument_id: str = Field(max_length=64)
    content: Literal['titles', 'body'] = 'titles'
    limit: int = Field(default=3, ge=1, le=5)


@router.post('')
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
