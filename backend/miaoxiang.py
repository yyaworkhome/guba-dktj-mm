"""MX read-only API adapter. Never executes skills, generated code or mutation endpoints."""
import io
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import select

from .auth import current_user, utc
from .database import get_db
from .models import ProviderQuery

router = APIRouter(prefix='/api/mx', tags=['妙想只读数据'])
BASE = 'https://mkapi2.dfcfs.com/finskillshub/api/claw/'
ENDPOINTS = {'data': ('query', 'toolQuery'), 'screen': ('stock-screen', 'keyword')}
gate = threading.Lock()
attempts = deque()
MAX_BYTES = 2_000_000
MAX_TABLES, MAX_ROWS, MAX_COLUMNS = 8, 50, 24


class ProviderError(Exception):
    def __init__(self, code, message, status=502):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def api_key():
    key = os.getenv('MX_APIKEY', '').strip()
    # Existing desktop processes may predate a user's permanent Windows setting.
    if not key and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as env:
                key = str(winreg.QueryValueEx(env, 'MX_APIKEY')[0]).strip()
        except OSError:
            pass
    return key


def text(value, limit=1500):
    if value is None: return ''
    if isinstance(value, (dict, list)): value = json.dumps(value, ensure_ascii=False)
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:limit]


def obj(value): return value if isinstance(value, dict) else {}


def arr(value): return value if isinstance(value, list) else []


def normalize(payload, kind):
    """Keep dates, units, matched entities and provider conditions; HTML stays plain text."""
    if not isinstance(payload, dict):
        raise ProviderError('invalid_response', '妙想返回格式无法识别，请稍后重试。')
    outer = obj(payload.get('data'))
    codes = [payload.get('status'), payload.get('code'), outer.get('status'), outer.get('code')]
    if any(str(c) in ('113', '114', '401', '403', '429') for c in codes):
        quota = any(str(c) in ('113', '429') for c in codes)
        raise ProviderError('quota' if quota else 'credentials',
                            '妙想调用额度不足或触发限频，请稍后再试。' if quota else '妙想密钥无效或没有访问权限，请检查后端配置。', 503)
    if payload.get('status') != 0 or outer.get('status', 0) not in (0, None):
        raise ProviderError('provider_failure', '妙想未能完成查询，请调整问句后重试。')
    inner = obj(outer.get('data'))
    tables, notes, truncated = [], [], False
    if kind == 'data':
        result = obj(inner.get('searchDataResultDTO')) or inner
        dtos = arr(result.get('dataTableDTOList'))
        truncated = len(dtos) > MAX_TABLES
        for dto in dtos[:MAX_TABLES]:
            if not isinstance(dto, dict): continue
            table, names = obj(dto.get('table')), obj(dto.get('nameMap'))
            dates = arr(table.get('headName'))
            keys = list(dict.fromkeys([str(k) for k in arr(dto.get('indicatorOrder'))] + list(table)))
            keys = [k for k in keys if k != 'headName' and isinstance(table.get(k), list)]
            truncated |= len(keys) > MAX_COLUMNS - 1 or len(dates) > MAX_ROWS
            keys = keys[:MAX_COLUMNS - 1]
            if not dates or not keys: continue
            columns = [{'key': 'date', 'label': '日期 / 维度', 'date': ''}] + [
                {'key': k, 'label': text(names.get(k) or k, 120), 'date': ''} for k in keys]
            rows = [[text(d)] + [text(table[k][i]) if i < len(table[k]) else '' for k in keys]
                    for i, d in enumerate(dates[:MAX_ROWS])]
            tables.append({'title': text(dto.get('title') or dto.get('entityName') or '查询结果', 180),
                           'entity': text(dto.get('code'), 80), 'columns': columns, 'rows': rows,
                           'total_rows': len(dates), 'source_type': text(dto.get('dataType'), 80)})
            parameter = dto.get('fieldParameterStr')
            if parameter: notes.append(text(parameter, 500))
        entities = [{'name': text(obj(e).get('fullName'), 80), 'code': text(obj(e).get('secuCode'), 40),
                     'type': text(obj(e).get('entityTypeName'), 40)} for e in arr(result.get('entityTagDTOList'))[:20]]
        conditions = text(result.get('condition'), 2000)
        request_id = text(result.get('questionId'), 100)
    else:
        result = obj(obj(inner.get('allResults')).get('result')) or obj(inner.get('result'))
        columns_raw = [c for c in arr(result.get('columns')) if isinstance(c, dict) and c.get('key') and not c.get('hide')]
        data = arr(result.get('dataList'))
        truncated = len(columns_raw) > MAX_COLUMNS or len(data) > MAX_ROWS
        columns_raw = columns_raw[:MAX_COLUMNS]
        columns = [{'key': str(c['key']), 'label': text(c.get('title') or c['key'], 120),
                    'date': text(c.get('dateMsg'), 80)} for c in columns_raw]
        if columns:
            # Match by stable key before provider's date-specific uniqueKey.
            rows = [[text(obj(r).get(c['key'], obj(r).get(c.get('uniqueKey', '')))) for c in columns_raw]
                    for r in data[:MAX_ROWS]]
            total = result.get('total', result.get('totalRecordCount', len(data)))
            total = total if isinstance(total, int) and total >= 0 else len(data)
            truncated |= total > len(rows)
            tables.append({'title': text(inner.get('title') or '板块 / 条件筛选结果', 180), 'entity': '',
                           'columns': columns, 'rows': rows, 'total_rows': total, 'source_type': '妙想条件筛选'})
        entities, conditions = [], text(inner.get('totalCondition') or inner.get('responseConditionList'), 2000)
        request_id = text(obj(inner.get('allResults')).get('traceId') or inner.get('id'), 100)
    if not tables:
        raise ProviderError('no_table', '本次没有可展示的结构化表格，请缩小范围或更换问句。', 422)
    return {'tables': tables, 'entities': entities, 'conditions': conditions, 'notes': notes,
            'truncated': bool(truncated), 'request_id': request_id, 'adapter_version': 'mx-readonly-v1'}


def fetch_result(kind, query, key, client=None):
    endpoint, field = ENDPOINTS[kind]
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False)
    try:
        with client.stream('POST', BASE + endpoint, headers={'apikey': key}, json={field: query}) as response:
            if response.status_code in (401, 403):
                raise ProviderError('credentials', '妙想密钥无效或没有访问权限，请检查后端配置。', 503)
            if response.status_code == 429:
                raise ProviderError('quota', '妙想调用限频，请稍后再试。', 503)
            if response.status_code != 200:
                raise ProviderError('http_failure', '妙想服务暂不可用，请稍后再试。')
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ProviderError('too_large', '返回数据过多，请缩小查询范围。', 422)
            # Never return upstream error messages, headers or unfiltered raw JSON.
            payload = json.loads(content)
        normalized = normalize(payload, kind)
        # Defense against unexpected credential reflection by the upstream service.
        return json.loads(json.dumps(normalized, ensure_ascii=False).replace(key, '[redacted]'))
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ProviderError('unavailable', '妙想连接超时或返回格式变化，请稍后再试。', 502) from None
    finally:
        if own: client.close()


class QueryBody(BaseModel):
    model_config = {'str_strip_whitespace': True}
    kind: Literal['data', 'screen'] = 'data'
    query: str = Field(min_length=2, max_length=160)


def serialize(row, cached=False):
    return {'id': row.id, 'kind': row.kind, 'query': row.query, 'status': row.status,
            'observed_at': utc(row.observed_at).isoformat(), 'started_at': utc(row.started_at).isoformat(),
            'cached': cached, 'source': '东方财富妙想', 'data_mode': 'provider-observation', **row.result}


@router.get('/status')
def status():
    return {'configured': bool(api_key()), 'source': '东方财富妙想', 'read_only': True,
            'cache_seconds': 600, 'max_rows_per_table': MAX_ROWS}


@router.post('/query')
def query(body: QueryBody, user=Depends(current_user), db=Depends(get_db)):
    key = api_key()
    if not key: raise HTTPException(503, '后端尚未配置妙想密钥 MX_APIKEY。')
    # Catch accidental credential pastes before anything is stored or sent upstream.
    if key in body.query or 'mkt_' in body.query:
        raise HTTPException(422, '请输入行情或板块查询，不要输入密钥。')
    now = datetime.now(timezone.utc)
    cached = db.scalar(select(ProviderQuery).where(ProviderQuery.user_id == user.id,
        ProviderQuery.kind == body.kind, ProviderQuery.query == body.query, ProviderQuery.status == 'success',
        ProviderQuery.observed_at >= now - timedelta(minutes=10)).order_by(ProviderQuery.id.desc()))
    if cached: return serialize(cached, cached=True)
    if not gate.acquire(blocking=False): raise HTTPException(429, '已有妙想查询正在进行，请稍后再试。')
    try:
        clock = time.monotonic()
        while attempts and clock - attempts[0] > 3600: attempts.popleft()
        if len(attempts) >= 30 or sum(clock - t < 60 for t in attempts) >= 5:
            raise HTTPException(429, '妙想查询过于频繁，请稍后再试。')
        attempts.append(clock)
        try:
            result = fetch_result(body.kind, body.query, key)
            error = None
        except ProviderError as exc:
            result, error = {'error_code': exc.code, 'error': exc.message}, exc
        row = ProviderQuery(user_id=user.id, kind=body.kind, query=body.query, started_at=now,
                            observed_at=datetime.now(timezone.utc), status='failed' if error else 'success', result=result)
        db.add(row); db.commit()
        if error: raise HTTPException(error.status, error.message)
        return serialize(row)
    finally:
        gate.release()


@router.get('/history')
def history(user=Depends(current_user), db=Depends(get_db)):
    rows = db.scalars(select(ProviderQuery).where(ProviderQuery.user_id == user.id)
                      .order_by(ProviderQuery.id.desc()).limit(20))
    return [{'id': r.id, 'query': r.query, 'kind': r.kind, 'status': r.status,
             'observed_at': utc(r.observed_at).isoformat()} for r in rows]


def own_query(qid, user, db):
    row = db.scalar(select(ProviderQuery).where(ProviderQuery.id == qid, ProviderQuery.user_id == user.id))
    if not row: raise HTTPException(404, '未找到此查询记录。')
    return row


@router.get('/history/{qid}')
def detail(qid: int, user=Depends(current_user), db=Depends(get_db)):
    return serialize(own_query(qid, user, db))


@router.get('/history/{qid}/export')
def export(qid: int, user=Depends(current_user), db=Depends(get_db)):
    row = own_query(qid, user, db)
    if row.status != 'success': raise HTTPException(422, '失败记录没有可导出的数据。')
    book = Workbook(); metadata = book.active; metadata.title = '查询记录'
    metadata.append(['来源', '东方财富妙想', '观察时间', utc(row.observed_at).isoformat()])
    metadata.append(['原始问句', row.query]); metadata.append(['接口解析条件', row.result.get('conditions', '')])
    metadata.append(['范围说明', '仅导出保存的有限行结果；查询获取时间不等于市场数据发生时间。'])
    for i, table in enumerate(row.result['tables'], 1):
        sheet = book.create_sheet(f'数据{i}')
        sheet.append([table['title'], table['entity']])
        sheet.append([c['label'] + (f" ({c['date']})" if c['date'] else '') for c in table['columns']])
        for values in table['rows']: sheet.append(values)
    # Force untrusted provider/user strings to literal text, including =... cells.
    for sheet in book:
        for cells in sheet:
            for cell in cells:
                if isinstance(cell.value, str):
                    cell.value = text(cell.value, 30000)
                    cell.data_type = 's'
    stream = io.BytesIO(); book.save(stream); stream.seek(0)
    return StreamingResponse(stream, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="miaoxiang-{row.id}.xlsx"'})
