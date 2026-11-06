from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from backend.research import opinion_score, composite, aggregate_posts, classify_rules, backtest, state

def test_neutral_and_zero_direction_are_not_fabricated():
    assert opinion_score(70,20)==pytest.approx(77.78)
    assert opinion_score(0,0) is None
    assert state(20)=='悲观' and state(40)=='中性分歧' and state(80)=='极度亢奋'

def test_missing_dimensions_reweight_and_expose_coverage():
    result=composite({'opinion':80,'activity':None})
    assert result['score']==80
    assert result['coverage']==35
    assert composite({})['score'] is None

def test_duplicates_and_prolific_author_cannot_dominate():
    rows=[{'text':f'看好{i}', 'author_hash':'a','label':'bullish','published_at':i} for i in range(10)]
    rows += [{'text':'看好0', 'author_hash':'b','label':'bullish','published_at':20}, {'text':'看空了','author_hash':'c','label':'bearish','published_at':21}]
    assert aggregate_posts(rows)=={'bullish':3,'bearish':1}

def test_rules_label_negation_and_conflict_as_neutral():
    assert classify_rules('不看好')['label']=='neutral'
    assert classify_rules('有人看好有人看空')['label']=='neutral'
    assert classify_rules('我看好，准备低吸')['label']=='bullish'
    assert classify_rules('崩盘了，准备割肉')['tags']==['恐慌']

def fixtures():
    start=datetime(2026,10,12,8,tzinfo=timezone.utc)
    bars={}; snapshots=[]
    for i in range(12):
        date=(start+timedelta(days=i)).date().isoformat()
        bars[date]=SimpleNamespace(session_date=date,open_at=start+timedelta(days=i,hours=-6,minutes=-30),available_at=start+timedelta(days=i,hours=-1),open=100+i,close=101+i)
        snapshots.append(SimpleNamespace(session_date=date,available_at=start+timedelta(days=i),metrics={'score':70}))
    return snapshots,bars

def test_backtest_enters_next_open_and_excludes_future_labels():
    snapshots,bars=fixtures()
    result=backtest(snapshots,bars,60)
    assert result[0]['samples']==11
    assert result[0]['excluded']==1
    assert result[-1]['samples']==1
    expected=(111/101-1)*100 # day0 signal -> day1 open101 -> day10 close111
    assert result[-1]['mean_return']==pytest.approx(round(expected,3))
    # Future prices present in storage must not leak past the knowledge cutoff.
    trimmed=backtest(snapshots[:4],bars,60)
    assert trimmed[0]['samples']==3
    assert trimmed[-1]['samples']==0

def test_postdated_signal_cannot_trade_earlier_entry():
    snapshots,bars=fixtures()
    snapshots[0].available_at=snapshots[1].available_at
    result=backtest([snapshots[0]],bars,60)
    assert result[0]['samples']==0

def test_signal_after_next_open_but_before_next_close_is_too_late():
    snapshots,bars=fixtures()
    snapshots[0].available_at=snapshots[1].available_at-timedelta(hours=5)
    # A later non-triggering snapshot makes the exit price known, isolating
    # the entry-time condition rather than passing on the cutoff check alone.
    snapshots[-1].metrics={'score':0}
    assert backtest([snapshots[0],snapshots[-1]],bars,60)[0]['samples']==0
