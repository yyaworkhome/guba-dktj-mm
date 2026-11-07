from copy import deepcopy
from datetime import datetime,timedelta,timezone
from fastapi.testclient import TestClient
from backend.decision import decision_report,technical_levels
from backend.interpretation import interpret_sample
from backend.tests.test_api import client
from backend.main import app

NOW=datetime.now(timezone.utc)


def sample():
    bars=[]
    for n in range(30):
        close=10+n*.03
        bars.append({'date':(NOW-timedelta(days=30-n)).date().isoformat(),'open':close-.01,'close':close,'high':close+.05,'low':close-.05,'volume_lots':1000000,'amount_cny':100000000})
    quote={'observed_at':NOW.isoformat(),'bars':bars}
    counts={'bullish':15,'bearish':5,'neutral':5}
    return {'instrument_id':'XSHG:600900','code':'600900','name':'测试样本','as_of':NOW.isoformat(),'days':3,'quote':quote,'opinion_score':75,
            'effective_posts':25,'authors':20,'last_post_observation':NOW.isoformat(),'examples':[],
            'percentages':{'bullish':60,'bearish':20,'neutral':20},'counts':counts,'interpretation':interpret_sample(counts,25,20,False,quote)}


def test_missing_dimensions_are_not_renormalized_to_full_score():
    report=decision_report(sample(),now=NOW)
    assert report['score'] is None and report['coverage']<100
    assert report['status']=='等待信号' and report['plan']
    assert report['components'][4]['score'] is None
    assert any('不启用买入' in v for v in report['vetoes'])
    levels=report['plan']
    assert 0<levels['stop']<levels['entry']<levels['target_2r']<levels['target_3r']
    assert abs((levels['target_2r']-levels['entry'])/(levels['entry']-levels['stop'])-2)<.03


def test_stale_bad_low_volume_and_corporate_action_prices_withhold_trade_levels():
    for mutation in ['stale','future_observation','future_bar','gap','missing','duplicate','few']:
        data=sample();quote=data['quote']
        if mutation=='stale':quote['observed_at']=(NOW-timedelta(hours=25)).isoformat()
        elif mutation=='future_observation':quote['observed_at']=(NOW+timedelta(minutes=10)).isoformat()
        elif mutation=='future_bar':quote['bars'][-1]['date']=(NOW+timedelta(days=2)).date().isoformat()
        elif mutation=='gap':
            b=quote['bars'][-1];b.update(open=20,close=20,low=19,high=21)
        elif mutation=='missing':del quote['bars'][-1]['high']
        elif mutation=='duplicate':quote['bars'][-1]['date']=quote['bars'][-2]['date']
        else:quote['bars']=quote['bars'][-10:]
        report=decision_report(data,now=NOW)
        assert report['plan'] is None and report['price_reasons'],mutation
        assert not report['technical'] or not report['technical'].get('conditional_levels'),mutation


def test_trade_risk_rejections_do_not_hide_valid_price_analysis():
    for mutation in ['illiquid','missing_amount','wide_stop']:
        data=sample()
        for bar in data['quote']['bars']:
            if mutation=='illiquid':bar['amount_cny']=100
            elif mutation=='missing_amount':bar['amount_cny']=None
            else:bar['low']=bar['close']*.7
        result=decision_report(data,now=NOW)
        assert not result['price_reasons'] and result['technical']['trade_reasons'],mutation
        assert result['data_status']['price']=='技术数据可用'
        assert result['technical']['regime']=='趋势偏多' and result['plan'] is None
        assert not result['technical'].get('conditional_levels')


def test_weak_sentiment_withholds_entry_even_when_price_levels_are_valid():
    data=sample();data['interpretation']['quality_reasons']=['方向样本仅2条']
    report=decision_report(data,now=NOW)
    assert report['technical']['support10']>0 and report['plan'] is None
    assert report['components'][0]['score'] is None


def test_plan_record_is_local_and_does_not_accept_account_balances(client,monkeypatch):
    monkeypatch.setenv('ALLOW_LOCAL_COLLECTION','true')
    assert client.get('/api/live/decision').json()['plan'] is None
    assert client.post('/api/live/decision',json={'instrument_id':'XSHE:300059'}).status_code==403
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as local:
        one=local.post('/api/live/decision',json={'instrument_id':'XSHE:300059'}).json()
        two=local.post('/api/live/decision',json={'instrument_id':'XSHE:300059'}).json()
        assert one['record_id']==two['record_id'] and two['reused']
        assert local.get('/api/live/decision/history').json()[0]['id']==one['record_id']
        assert local.get('/api/live/decision?instrument_id=XSHG:000001').status_code==422
