from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from backend.database import Base
from backend.models import Instrument, PostRevision, Classification
from backend.ingestion import record_post, classify_revision

def test_revisions_classifications_and_first_seen_are_append_only():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Instrument(id='CN:ALL',code='ALL',name='全市场',kind='market',exchange='CN')); db.flush()
        published=datetime.now(timezone.utc)-timedelta(days=3)
        args=dict(source='test-fixture',source_id='1',instrument_id='CN:ALL',author_id='author',published_at=published)
        p,r,created=record_post(db,**args,text='看好',engagement={'comments':1})
        first_seen=p.first_seen_at
        assert first_seen>published and created
        c1=classify_revision(db,r)
        _,same,created=record_post(db,**args,text='看好',engagement={'comments':1})
        assert not created and same.id==r.id
        _,updated,created=record_post(db,**args,text='看空',engagement={'comments':2})
        c2=classify_revision(db,updated)
        assert created and updated.id!=r.id
        assert p.first_seen_at==first_seen
        assert c1.result['label']=='bullish' and c2.result['label']=='bearish'
        assert db.scalar(select(func.count()).select_from(PostRevision))==2
        assert db.scalar(select(func.count()).select_from(Classification))==2
        with pytest.raises(ValueError): record_post(db,**{**args,'published_at':datetime.now(timezone.utc)+timedelta(days=1)},text='未来')
    engine.dispose()
