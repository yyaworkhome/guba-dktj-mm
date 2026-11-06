import io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from openpyxl import load_workbook
from backend.database import Base, get_db
from backend.main import app, login_attempts
from backend.seed import seed

@pytest.fixture
def client(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session=sessionmaker(engine,expire_on_commit=False)
    import backend.main as main
    monkeypatch.setattr(main,'engine',engine)
    monkeypatch.setattr(main,'SessionLocal',Session)
    with Session() as db: seed(db)
    def override():
        with Session() as db: yield db
    app.dependency_overrides[get_db]=override
    login_attempts.clear()
    with TestClient(app) as c: yield c
    app.dependency_overrides.clear()
    engine.dispose()

def account(client,name):
    response=client.post('/api/auth/register',json={'username':name,'password':'long-enough-password'})
    assert response.status_code==200,response.text
    return {'Authorization':f'Bearer {response.json()["token"]}'}

def test_ambiguous_code_returns_index_and_stock(client):
    rows=client.get('/api/instruments?q=000001').json()
    assert {r['id'] for r in rows}=={'XSHG:000001','XSHE:000001'}

def test_live_api_never_falls_back_to_demo(client):
    data=client.get('/api/live/analysis').json()
    assert data['data_mode']=='live' and data['effective_posts']==0
    assert data['opinion_score'] is None and data['quote'] is None
    assert client.get('/api/live/status').json()['latest_run'] is None
    assert client.get('/api/live/analysis?days=0').status_code==422
    assert client.get('/api/live/instruments?q=000001').json()[0]['id']=='XSHE:000001'
    assert len(client.get('/api/live/instruments').json())==5
    assert client.get('/api/live/analysis?instrument_id=000001').status_code==422
    assert client.get('/api/live/analysis?instrument_id=XSHG:000001').status_code==422
    body=client.get('/api/live/analysis?instrument_id=XSHE:000001&content=body').json()
    assert body['instrument_id']=='XSHE:000001' and body['content']=='body'
    assert body['effective_posts']==0
    assert client.get('/api/live/analysis?content=unknown').status_code==422

def test_analysis_uses_real_requested_period_and_consistent_counts(client):
    one=client.get('/api/analysis?period=1').json()
    many=client.get('/api/analysis?period=10').json()
    assert len(many['trend'])==10
    assert many['metrics']['posts']>one['metrics']['posts']
    assert sum(many['metrics'][k] for k in ('bullish','bearish','neutral'))==many['metrics']['posts']
    assert many['metrics']['dimensions']['activity'] is None
    assert many['metrics']['coverage']==85
    assert client.get('/api/analysis?period=1000').status_code==422
    assert client.get('/api/analysis?instrument_id=missing').status_code==404

def test_accounts_isolate_watchlists_and_alerts_and_logout_revokes(client):
    alice=account(client,'alice'); bob=account(client,'bob')
    assert client.get('/api/watchlist').status_code==401
    client.put('/api/watchlist',headers=alice,json={'instrument_id':'XSHE:000001'})
    client.put('/api/watchlist',headers=alice,json={'instrument_id':'XSHE:000001'})
    assert len(client.get('/api/watchlist',headers=alice).json())==1
    assert client.get('/api/watchlist',headers=bob).json()==[]
    rule=client.post('/api/alerts',headers=alice,json={'instrument_id':'CN:ALL','metric':'bullish_pct','threshold':50}).json()
    client.delete(f'/api/alerts/{rule["id"]}',headers=bob)
    assert len(client.get('/api/alerts',headers=alice).json())==1
    assert client.post('/api/auth/login',json={'username':'alice','password':'incorrect-password'}).status_code==401
    client.post('/api/auth/logout',headers=alice)
    assert client.get('/api/auth/me',headers=alice).status_code==401

def test_excel_is_valid_workbook_and_marked_demo(client):
    response=client.get('/api/export?period=5')
    assert response.status_code==200
    book=load_workbook(io.BytesIO(response.content))
    assert '模拟数据' in book.active['A1'].value
    assert book.active.max_row==8

def test_backtest_is_honest_and_classifier_requires_auth(client):
    data=client.get('/api/backtest?threshold=0').json()
    assert data['data_mode']=='demo'
    assert len(data['results'])==4
    assert data['results'][-1]['excluded']>0
    assert client.post('/api/classify',json={'text':'看好'}).status_code==401
    headers=account(client,'researcher')
    result=client.post('/api/classify',headers=headers,json={'text':'看好'}).json()
    assert result['method']=='rules-v1' and result['confidence'] is None


def test_each_demo_catalog_object_supports_analysis_backtest_and_export(client):
    catalog=client.get('/api/instruments').json()
    assert {'market','index','sector','stock'}=={i['kind'] for i in catalog}
    for item in catalog:
        iid=item['id']
        for period in (1,3,5,10,20):
            response=client.get('/api/analysis',params={'instrument_id':iid,'period':period})
            assert response.status_code==200,(iid,response.text)
            result=response.json()
            assert result['instrument']['id']==iid and result['data_mode']=='demo'
            assert len(result['trend'])==period
        result=client.get('/api/backtest',params={'instrument_id':iid,'threshold':0}).json()
        assert len(result['results'])==4 and result['data_mode']=='demo'
        assert client.get('/api/export',params={'instrument_id':iid,'period':3}).status_code==200
    for sort in ('score','bullish_pct','bearish_pct','heat_change'):
        result=client.get('/api/rankings',params={'sort':sort}).json()
        assert len(result)==5 and all(r['data_mode']=='demo' for r in result)
    assert client.get('/api/signals').status_code==200
    assert client.get('/api/report').json()['data_mode']=='demo'
