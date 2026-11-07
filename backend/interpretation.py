"""Explain observed samples without turning missing coverage into a market forecast."""
from .research import opinion_score


def interpret_sample(counts, effective_posts, authors, stale, quote):
    bull, bear = counts.get('bullish', 0), counts.get('bearish', 0)
    directional = bull + bear
    reasons = []
    if effective_posts < 20: reasons.append(f'有效样本{effective_posts}条，少于20条')
    if authors < 10: reasons.append(f'有效作者{authors}位，少于10位')
    if directional < 10: reasons.append(f'明确方向{directional}条，少于10条')
    if stale: reasons.append('未采集或最近观察超过24小时')
    score = opinion_score(bull, bear)
    if not effective_posts:
        headline, status = '暂无可分析样本', 'empty'
    elif not directional:
        headline, status = '已完成统计，规则未识别明确多空方向', 'insufficient'
    else:
        direction = '偏多' if score >= 60 else '偏空' if score <= 40 else '多空分歧'
        headline = f'已识别方向的样本{direction}'
        status = 'limited' if reasons else 'observed'
        if reasons: headline += '，样本覆盖不足'
    coverage = round(directional / effective_posts * 100, 1) if effective_posts else None
    bullets = [f'去重及作者封顶后，有效样本{effective_posts}条、作者{authors}位。',
               f'看多{bull}条、看空{bear}条、未识别方向{counts.get("neutral", 0)}条。']
    if coverage is not None:
        bullets.append(f'方向识别覆盖率{coverage}%；未识别不等于作者持中立观点。')
    if reasons:
        bullets.append('当前不足以判断整个股吧的一致预期；可补充正文采样并持续积累。')
    bars = (quote or {}).get('bars', [])
    price = None
    if len(bars) >= 2 and bars[-2]['close'] > 0:
        before, latest = bars[-2:]
        price = {'from_date': before['date'], 'to_date': latest['date'],
                 'close': latest['close'], 'change_pct': round((latest['close'] / before['close'] - 1) * 100, 2),
                 'observed_at': quote['observed_at']}
    return {'status': status, 'headline': headline, 'direction_coverage': coverage,
            'quality_reasons': reasons, 'bullets': bullets, 'price_observation': price,
            'method': 'rules-v1；对已保存样本作描述统计，不是AI预测或买卖建议。'}
