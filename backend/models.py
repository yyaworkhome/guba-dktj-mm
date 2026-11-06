from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

def now():
    return datetime.now(timezone.utc)

class Instrument(Base):
    __tablename__ = 'instruments'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), index=True)
    exchange: Mapped[str] = mapped_column(String(20))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)

class Snapshot(Base):
    __tablename__ = 'sentiment_snapshots'
    __table_args__ = (UniqueConstraint('instrument_id', 'session_date', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[str] = mapped_column(String(40), default='demo-v1')
    data_mode: Mapped[str] = mapped_column(String(16), default='demo')
    metrics: Mapped[dict] = mapped_column(JSON)

class PriceBar(Base):
    __tablename__ = 'price_bars'
    __table_args__ = (UniqueConstraint('instrument_id', 'session_date', 'data_mode'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    open_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    data_mode: Mapped[str] = mapped_column(String(16), default='demo')

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class WatchItem(Base):
    __tablename__ = 'watch_items'
    __table_args__ = (UniqueConstraint('user_id', 'instrument_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'))

class AlertRule(Base):
    __tablename__ = 'alert_rules'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'))
    metric: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Post(Base):
    __tablename__ = 'posts'
    __table_args__ = (UniqueConstraint('source', 'source_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(100))
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    author_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class PostRevision(Base):
    __tablename__ = 'post_revisions'
    __table_args__ = (UniqueConstraint('post_id', 'content_hash', 'observed_at'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey('posts.id'), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(String(10000))
    engagement: Mapped[dict] = mapped_column(JSON, default=dict)

class Classification(Base):
    __tablename__ = 'classifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey('post_revisions.id'), index=True)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    model_version: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSON)

class CollectionRun(Base):
    __tablename__ = 'collection_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='running')
    details: Mapped[dict] = mapped_column(JSON, default=dict)

class QuoteObservation(Base):
    """A fetched unadjusted daily-bar batch; subsequent fetches never overwrite it."""
    __tablename__ = 'quote_observations'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('collection_runs.id'), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    bars: Mapped[list] = mapped_column(JSON)

class ProviderQuery(Base):
    """Read-only provider observations; separate from sentiment and backtests."""
    __tablename__ = 'provider_queries'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    query: Mapped[str] = mapped_column(String(160))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
