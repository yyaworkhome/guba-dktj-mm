"""Pure research functions. No future labels enter the signal calculation."""
from collections import Counter
from math import sqrt
import re
from hashlib import sha256
from datetime import timezone

def utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

WEIGHTS = {'opinion': .35, 'attention': .20, 'breadth': .20, 'activity': .15, 'velocity': .10}

def opinion_score(bull, bear):
    return round(100 * bull / (bull + bear), 2) if bull + bear else None

def composite(dimensions):
    present = {k: v for k, v in dimensions.items() if k in WEIGHTS and v is not None}
    if not present:
        return {'score': None, 'coverage': 0, 'dimensions': dimensions}
    coverage = sum(WEIGHTS[k] for k in present)
    return {'score': round(sum(max(0, min(100, v)) * WEIGHTS[k] for k, v in present.items()) / coverage, 1),
            'coverage': round(coverage * 100), 'dimensions': dimensions}

def state(score):
    if score is None: return '样本不足'
    if score < 20: return '极度恐慌'
    if score < 40: return '悲观'
    if score < 60: return '中性分歧'
    if score < 80: return '乐观'
    return '极度亢奋'

def classify_rules(text):
    """Explicit non-AI fallback, deliberately conservative on ambiguous language."""
    bulls = re.findall(r'看好|看多|上涨|涨停|低吸|抄底|越跌越买|起飞', text)
    bears = re.findall(r'看空|下跌|跌停|崩盘|跑路|割肉|清仓|没救了', text)
    negated = bool(re.search(r'不.{0,2}(看好|看多|上涨|涨停|看空|下跌|跌停)', text))
    label = 'neutral' if negated or bool(bulls) == bool(bears) else ('bullish' if bulls else 'bearish')
    tags = [tag for tag, pattern in [('追涨', '起飞|梭哈|冲啊'), ('恐慌', '崩盘|跑路|割肉'), ('抄底', '低吸|抄底|补仓'), ('踏空', '卖飞|没上车'), ('套牢', '套牢|回本|站岗')] if re.search(pattern, text)]
    return {'label': label, 'confidence': None, 'tags': tags, 'method': 'rules-v1',
            'reason': '规则辅助分类，含否定或多空冲突时归为中性；需要人工或AI复核。'}

def content_hash(text):
    return sha256(re.sub(r'\W+', '', text.lower()).encode()).hexdigest()

def aggregate_posts(rows, author_cap=3):
    """Each author capped; duplicate content has zero additional weight."""
    seen, authors, counts = set(), Counter(), Counter()
    for row in sorted(rows, key=lambda r: r['published_at']):
        digest = content_hash(row['text'])
        if digest in seen or authors[row['author_hash']] >= author_cap or row.get('excluded'):
            continue
        seen.add(digest)
        authors[row['author_hash']] += 1
        counts[row['label']] += 1
    return dict(counts)

def signals(current, previous, bars):
    bull, bear = current['bullish_pct'], current['bearish_pct']
    delta = bull - previous['bullish_pct'] if previous else 0
    ret = (bars[-1].close / bars[-4].close - 1) * 100 if len(bars) >= 4 else None
    out = []
    if bull >= 80: out.append({'name': '一致看多', 'tone': 'risk', 'reason': f'看多占比 {bull:.1f}%，需结合价格验证。'})
    if bear >= 80: out.append({'name': '极端悲观', 'tone': 'risk', 'reason': f'看空占比 {bear:.1f}%。'})
    if previous and previous['bearish_pct'] >= 60 and bear < 50:
        out.append({'name': '恐慌后修复', 'tone': 'watch', 'reason': '前期看空≥60%，当前降至50%以下。'})
    if previous and delta >= 5:
        out.append({'name': '情绪升温', 'tone': 'watch', 'reason': f'较前一交易日看多占比增加 {delta:.1f} 个百分点。'})
    if ret is not None and bull >= 80 and ret <= 1:
        out.append({'name': '亢奋但滞涨', 'tone': 'risk', 'reason': f'看多≥80%，近3个交易日价格变化 {ret:.2f}%。'})
    if ret is not None and ret < 0 and delta > 0:
        out.append({'name': '价格与情绪背离', 'tone': 'watch', 'reason': '价格回落而看多占比上升；仅为观察信号。'})
    if not out: out.append({'name': '未触发阈值', 'tone': 'neutral', 'reason': '当前未满足已定义的极端、反转或背离条件。'})
    return out

def backtest(snapshots, bars, threshold=60):
    """Signal at close; next session open entry, Nth session close exit.
    Price availability also must be at/before the last known snapshot cutoff.
    Overlapping events skipped separately for each holding horizon.
    """
    dates = sorted(bars)
    if not snapshots: return []
    cutoff = max(utc(s.available_at) for s in snapshots)
    result = []
    for horizon in (1, 3, 5, 10):
        returns, last_exit = [], -1
        excluded = 0
        for s in sorted(snapshots, key=lambda s: s.session_date):
            score = s.metrics.get('score')
            if score is None or score < threshold or s.session_date not in dates: continue
            i = dates.index(s.session_date)
            if i + 1 <= last_exit: continue
            end = i + horizon
            if end >= len(dates):
                excluded += 1
                continue
            entry, exit_bar = bars[dates[i+1]], bars[dates[end]]
            if utc(s.available_at) >= utc(entry.open_at) or utc(exit_bar.available_at) > cutoff:
                excluded += 1
                continue
            # All seeded bars are tradable. Live data must include suspension/limit fills.
            returns.append((exit_bar.close / entry.open - 1) * 100)
            last_exit = end
        n = len(returns)
        wins = sum(r > 0 for r in returns)
        p = wins / n if n else 0
        z = 1.96
        denom = 1 + z*z/n if n else 1
        center = (p+z*z/(2*n))/denom if n else 0
        radius = z*sqrt((p*(1-p)+z*z/(4*n))/n)/denom if n else 0
        result.append({'horizon': horizon, 'samples': n, 'excluded': excluded,
            'mean_return': round(sum(returns)/n, 3) if n else None,
            'up_pct': round(p*100, 1) if n else None,
            'ci_low': round((center-radius)*100, 1) if n else None,
            'ci_high': round((center+radius)*100, 1) if n else None})
    return result
