"""Bounded public-page adapter. No cookies, JavaScript execution or access bypass.

This first collector samples ordinary discussion TITLES from one list page.
It does not claim to have captured full posts, all authors, or the whole bar.
"""
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser

import httpx

IID = 'XSHE:300059'
SOURCE = 'eastmoney-title-v1'
BODY_SOURCE = 'eastmoney-body-v1'
STOCKS = {
    'XSHE:300059': ('300059', '东方财富', 0),
    'XSHE:000001': ('000001', '平安银行', 0),
    'XSHE:300750': ('300750', '宁德时代', 0),
    'XSHG:600519': ('600519', '贵州茅台', 1),
    'XSHG:688981': ('688981', '中芯国际', 1),
}
LIST_URL = 'https://guba.eastmoney.com/list,300059.html'
KLINE_URL = ('https://push2his.eastmoney.com/api/qt/stock/kline/get'
    '?secid=0.300059&klt=101&fqt=0&lmt=30&end=20500101'
    '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61')
CST = timezone(timedelta(hours=8))

def stock(iid=IID):
    if iid not in STOCKS: raise ValueError('真实采样仅支持已开放的股票；请使用交易所加代码，不支持指数')
    return STOCKS[iid]

def list_url(iid=IID): return f'https://guba.eastmoney.com/list,{stock(iid)[0]}.html'

def kline_url(iid=IID):
    code, _, market = stock(iid)
    return KLINE_URL.replace('0.300059', f'{market}.{code}')

def detail_url(iid, pid):
    if not re.fullmatch(r'\d{1,30}', str(pid)): raise SourceError('帖子ID格式异常')
    return f'https://guba.eastmoney.com/news,{stock(iid)[0]},{pid}.html'

class SourceError(ValueError):
    pass

class SourceExcluded(SourceError):
    """Valid detail response, excluded by the sample policy rather than failed."""
    pass

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('p', 'div', 'br') and not self.hidden: self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden-1)
        if tag in ('p', 'div') and not self.hidden: self.parts.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def plain(value):
    if not isinstance(value, str): raise SourceError('标题不是文本')
    p = PlainText(); p.feed(value)
    return unescape(''.join(p.parts)).strip()

def get_document(client, url):
    # URLs are generated from an explicit stock allowlist. Arbitrary hosts,
    # query parameters and redirected destinations are never requested.
    allowed = any(url in (list_url(iid), kline_url(iid)) or
        re.fullmatch(re.escape(f'https://guba.eastmoney.com/news,{code},') + r'\d{1,30}\.html', url)
        for iid, (code, _, _) in STOCKS.items())
    if not allowed: raise SourceError('不支持的数据地址')
    with client.stream('GET', url, follow_redirects=False, timeout=15) as response:
        if response.status_code != 200:
            raise SourceError(f'数据源返回 HTTP {response.status_code}；未重试或绕过访问限制')
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > 2_000_000: raise SourceError('源响应超出大小限制')
    try: return content.decode('utf-8')
    except UnicodeDecodeError as exc: raise SourceError('源响应编码不受支持') from exc

def nonnegative(value):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise SourceError('互动计数格式变化')
    return int(value)

def parse_titles(document, observed_at, iid=IID):
    code, _, _ = stock(iid)
    match = re.search(r'\bvar\s+article_list\s*=\s*', document)
    if not match: raise SourceError('未找到股吧列表数据，可能为验证页面或源结构变化')
    try: payload, _ = json.JSONDecoder().raw_decode(document[match.end():])
    except (ValueError, TypeError) as exc: raise SourceError('列表JSON格式变化') from exc
    if not isinstance(payload, dict) or str(payload.get('bar_code')) != code or payload.get('rc') != 1:
        raise SourceError('股吧代码或返回状态不匹配')
    rows = payload.get('re')
    if not isinstance(rows, list) or not rows or len(rows) > 200:
        raise SourceError('列表为空或样本数量异常')
    out, excluded, ids = [], Counter(), set()
    for p in rows:
        if not isinstance(p, dict): excluded['invalid'] += 1; continue
        board = p.get('post_guba') or p
        if not isinstance(board, dict): excluded['invalid'] += 1; continue
        if str(board.get('stockbar_code')) != code:
            excluded['other_board'] += 1; continue
        # Conservative scope: ordinary discussion only. No media/announcements,
        # cross-bar recommendations, pinned/hot editorial articles or reposts.
        if p.get('post_type') != 0 or p.get('post_state', 0) != 0 or p.get('post_is_hot') or p.get('post_top_status', 0):
            excluded['nonordinary'] += 1; continue
        user = p.get('post_user') or p
        if not isinstance(user, dict): excluded['invalid'] += 1; continue
        extra = user.get('user_extendinfos') or {}
        if not isinstance(extra, dict): excluded['invalid'] += 1; continue
        if str(extra.get('is_enterprise', 'false')).lower() == 'true' or p.get('source_post_id', 0):
            excluded['nonordinary'] += 1; continue
        try:
            pid = str(p['post_id']); author = str(user['user_id'])
            if not re.fullmatch(r'\d{1,30}', pid) or not re.fullmatch(r'\d{1,40}', author): raise SourceError('身份字段异常')
            published = datetime.strptime(p['post_publish_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=CST)
            if published > observed_at: raise SourceError('未来发布时间')
            title = plain(p['post_title'])
            if not title or len(title) > 1000: raise SourceError('标题长度异常')
            engagement = {'reads': nonnegative(p['post_click_count']), 'comments': nonnegative(p['post_comment_count'])}
        except (KeyError, TypeError, ValueError):
            excluded['invalid'] += 1; continue
        if pid in ids: excluded['duplicate_id'] += 1; continue
        ids.add(pid)
        out.append({'source_id': pid, 'author_id': author, 'text': title,
            'published_at': published, 'engagement': {**engagement,
                'content_scope': 'title-only', 'url': detail_url(iid, pid)}})
    if not out: raise SourceError('没有通过代码、时间和普通讨论校验的标题样本')
    return out, {'received': len(rows), 'accepted': len(out), 'excluded': dict(excluded)}

def parse_bars(document, observed_at, iid=IID):
    code, _, market = stock(iid)
    try:
        payload = json.loads(document); data = payload['data']
        if payload['rc'] != 0 or data['code'] != code or data['market'] != market: raise ValueError()
        lines = data['klines']
        if not isinstance(lines, list) or not lines or len(lines) > 60: raise ValueError()
        bars, dates = [], set()
        for line in lines:
            parts = line.split(',')
            day = datetime.strptime(parts[0], '%Y-%m-%d').date()
            if day > observed_at.astimezone(CST).date() or parts[0] in dates: raise ValueError()
            opened, close, high, low, volume, amount = map(float, parts[1:7])
            import math
            if not all(math.isfinite(x) for x in (opened, close, high, low, volume, amount)): raise ValueError()
            if not (0 < low <= min(opened, close) <= max(opened, close) <= high and volume >= 0 and amount >= 0): raise ValueError()
            dates.add(parts[0])
            bars.append({'date': parts[0], 'open': opened, 'close': close, 'high': high, 'low': low,
                'volume_lots': volume, 'amount_cny': amount, 'adjustment': 'none',
                'is_current_session': day == observed_at.astimezone(CST).date()})
        return sorted(bars, key=lambda b: b['date'])
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        raise SourceError('日线代码、日期或价格数据校验失败') from exc

def document_digest(document): return sha256(document.encode()).hexdigest()

def parse_body(document, observed_at, candidate, iid=IID):
    """Validate detail identity against its list candidate before saving text."""
    match = re.search(r'\bvar\s+post_article\s*=\s*', document)
    if not match: raise SourceError('未找到帖子正文数据，可能为验证页面或源结构变化')
    try:
        p, _ = json.JSONDecoder().raw_decode(document[match.end():])
        if str(p['post_id']) != candidate['source_id']: raise ValueError()
        if p['post_guba']['stockbar_code'] != stock(iid)[0]: raise ValueError()
        if str(p['post_user']['user_id']) != candidate['author_id']: raise ValueError()
        if p['post_state'] != 0 or p.get('post_status', 0): raise ValueError()
        published = datetime.strptime(p['post_publish_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=CST)
        if published != candidate['published_at'] or published > observed_at: raise ValueError()
        if p['post_type'] != 0 or p.get('source_post_id', 0) or p.get('post_top_status', 0):
            raise SourceExcluded('正文标记为非普通讨论、转帖或置顶，按采样规则排除')
        if str((p['post_user'].get('user_extendinfos') or {}).get('is_enterprise', 'false')).lower() == 'true':
            raise SourceExcluded('企业标记账号，按采样规则排除')
        title = plain(p.get('post_title', ''))
        body = plain(p['post_content'])
        if not body or len(body) > 9000: raise ValueError()
        # Ordinary short discussions often have an empty detail title; the
        # list title is then an excerpt of the body and must not be duplicated.
        normalized_title = re.sub(r'\s+', '', title)
        normalized_body = re.sub(r'\s+', '', body)
        text = f'{title}\n{body}' if title and not normalized_body.startswith(normalized_title) else body
        if len(text) > 10000: raise ValueError()
        engagement = {'reads': nonnegative(p['post_click_count']), 'comments': nonnegative(p['post_comment_count']),
            'content_scope': 'body', 'url': detail_url(iid, p['post_id']), 'title': title or candidate['text']}
        return {**candidate, 'text': text, 'engagement': engagement}
    except SourceExcluded:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SourceError('正文身份、股吧、发布时间或内容校验失败；未使用标题代替') from exc

def default_client():
    return httpx.Client(headers={'User-Agent': 'RetailSentimentResearch/2.0 (single-page sample)'})
