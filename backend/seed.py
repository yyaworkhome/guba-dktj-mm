"""Explicit, reproducible fixtures, never represented as live Eastmoney data."""
from datetime import date, datetime, time, timedelta, timezone
from math import sin, cos
from sqlalchemy import select
from .models import Instrument, Snapshot, PriceBar
from .research import opinion_score, composite

CATALOG = [
    ('CN:ALL', 'ALL', 'A股全市场', 'market', 'CN', ['全市场', 'a股', '全部'], {'boards': ['上证指数', '深证成指', '创业板指', '沪深300'], 'breadth': '全A模拟涨跌与涨跌停样本'}),
    ('XSHG:000001', '000001', '上证指数', 'index', 'XSHG', ['上证', 'sh000001'], {'boards': ['上证指数', '深证成指', '创业板指'], 'breadth': '全A模拟市场宽度（代理口径，非指数成分宽度）'}),
    ('XSHE:399001', '399001', '深证成指', 'index', 'XSHE', ['深证', 'sz399001'], {'boards': ['深证成指', '创业板指', '上证指数'], 'breadth': '全A模拟市场宽度'}),
    ('XSHE:399006', '399006', '创业板指', 'index', 'XSHE', ['创业板', 'sz399006'], {'boards': ['创业板指', '深证成指'], 'breadth': '全A模拟市场宽度'}),
    ('XSHG:000688', '000688', '科创50', 'index', 'XSHG', ['科创', '科创板'], {'boards': ['科创50', '上证指数'], 'breadth': '全A模拟市场宽度'}),
    ('XSHG:000300', '000300', '沪深300', 'index', 'XSHG', ['hs300', '沪深'], {'boards': ['沪深300', '上证指数', '深证成指'], 'breadth': '全A模拟市场宽度'}),
    ('XSHE:000001', '000001', '平安银行', 'stock', 'XSHE', ['平安', 'payh', 'sz000001'], {'boards': ['平安银行']}),
    ('XSHE:300059', '300059', '东方财富', 'stock', 'XSHE', ['东财', 'dfcf', 'sz300059'], {'boards': ['东方财富']}),
    ('XSHE:300750', '300750', '宁德时代', 'stock', 'XSHE', ['宁德', 'ndsd', 'sz300750'], {'boards': ['宁德时代']}),
    ('XSHG:688981', '688981', '中芯国际', 'stock', 'XSHG', ['中芯', 'zxgj', 'sh688981'], {'boards': ['中芯国际']}),
    ('XSHG:600519', '600519', '贵州茅台', 'stock', 'XSHG', ['茅台', 'gzmt', 'sh600519'], {'boards': ['贵州茅台']}),
    ('SECTOR:SEMI', 'SEMI', '半导体', 'sector', 'CUSTOM', ['芯片', '半导体板块'], {'members': ['XSHG:688981'], 'universe': '演示成分组，非完整行业成分'}),
    ('SECTOR:AI', 'AI', '人工智能', 'sector', 'CUSTOM', ['ai', '人工智能板块'], {'members': ['XSHG:688981', 'XSHE:300059'], 'universe': '演示成分组，非真实行业归类'}),
    ('SECTOR:BROKER', 'BROKER', '证券', 'sector', 'CUSTOM', ['券商', '证券板块'], {'members': ['XSHE:300059'], 'universe': '演示成分组，非完整行业成分'}),
    ('SECTOR:CONSUMER', 'CONSUMER', '消费', 'sector', 'CUSTOM', ['消费板块'], {'members': ['XSHG:600519'], 'universe': '演示成分组，非完整行业成分'})
]

def seed(db):
    if db.scalar(select(Snapshot.id).where(Snapshot.data_mode=='demo', Snapshot.version=='demo-v1').limit(1)): return
    sessions = [date(2026, 10, 12) + timedelta(days=i) for i in range(26) if (date(2026, 10, 12)+timedelta(days=i)).weekday()<5]
    for j, (iid, code, name, kind, exchange, aliases, scope) in enumerate(CATALOG):
        if not db.get(Instrument, iid):
            db.add(Instrument(id=iid, code=code, name=name, kind=kind, exchange=exchange, aliases=aliases, scope=scope))
        db.flush()
        price = 100 + j*13
        previous_total = None
        for t, session in enumerate(sessions):
            total = 800 + j*57 + t*31 + round(180*sin(t*.8+j))
            neutral = 9 + (j % 4)
            bullish = round(max(9, min(85, 48 + 18*sin(t*.28+j*.8)+t*.8)), 1)
            bearish = round(100-neutral-bullish, 1)
            bull_count, bear_count = round(total*bullish/100), round(total*bearish/100)
            dims = {'opinion': opinion_score(bull_count, bear_count), 'attention': round(50+22*sin(t*.3+j), 1),
                    'breadth': round(53+17*sin(t*.2+j), 1) if kind in ('market', 'index', 'sector') else None,
                    'activity': None, 'velocity': round(52+23*cos(t*.28+j*.8), 1)}
            score = composite(dims)
            available = datetime.combine(session, time(8, 0), timezone.utc) # 16:00 CST
            metric = {**score, 'bullish': bull_count, 'bearish': bear_count, 'neutral': total-bull_count-bear_count,
                'bullish_pct': round(bull_count/total*100, 1), 'bearish_pct': round(bear_count/total*100, 1),
                'posts': total, 'comments': round(total*2.1), 'reads': total*149,
                'heat_change': round((total/previous_total-1)*100, 1) if previous_total else 0,
                'keywords': [{'text': s, 'count': round(total*p)} for s,p in [('看好',.24),('低吸',.19),('观望',.16),('分歧',.12),('冲高回落',.08),('恐慌',.05),('踏空',.04)]],
                'advancers': round(2800+800*sin(t*.4)) if kind in ('market','index') else None,
                'decliners': round(2400-800*sin(t*.4)) if kind in ('market','index') else None,
                'source': 'deterministic-fixture-v1', 'authors': round(total*.72), 'duplicates_removed': round(total*.08)}
            db.add(Snapshot(instrument_id=iid, session_date=session.isoformat(), available_at=available, metrics=metric))
            previous_total = total
            opened = price*(1+.002*sin(t+j))
            price = opened*(1+.009*sin(t*.6+j)-.001)
            db.add(PriceBar(instrument_id=iid, session_date=session.isoformat(), open_at=datetime.combine(session, time(1, 30), timezone.utc), available_at=available-timedelta(hours=1), open=opened, close=price))
    db.commit()
