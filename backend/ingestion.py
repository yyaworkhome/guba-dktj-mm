"""Append-only persistence boundary for future collectors and the manual workbench.

Observation and classification time are assigned by the server, never backdated
to a post's claimed publication time.
"""
from datetime import datetime, timezone
from hashlib import sha256
from sqlalchemy import select
from .models import Post, PostRevision, Classification, Instrument
from .research import content_hash, classify_rules

def record_post(db, *, source, source_id, instrument_id, author_id, text, published_at, engagement=None):
    if published_at.tzinfo is None: raise ValueError('published_at must contain timezone')
    # SQLite drops offsets; persist UTC consistently on both supported databases.
    published_at=published_at.astimezone(timezone.utc)
    observed=datetime.now(timezone.utc)
    if published_at>observed: raise ValueError('publication cannot be in the future')
    if not db.get(Instrument,instrument_id): raise ValueError('unknown instrument')
    if not text.strip(): raise ValueError('empty post')
    post=db.scalar(select(Post).where(Post.source==source,Post.source_id==source_id))
    if not post:
        post=Post(source=source,source_id=source_id,instrument_id=instrument_id,
          author_hash=sha256(f'{source}:{author_id}'.encode()).hexdigest(),published_at=published_at,first_seen_at=observed)
        db.add(post); db.flush()
    elif post.instrument_id!=instrument_id:
        raise ValueError('post instrument identity cannot change')
    previous=db.scalar(select(PostRevision).where(PostRevision.post_id==post.id).order_by(PostRevision.id.desc()))
    engagement=engagement or {}
    # Exact equality for revision identity; normalized digest only for semantic deduplication.
    if previous and previous.text==text and previous.engagement==engagement: return post,previous,False
    revision=PostRevision(post_id=post.id,observed_at=observed,content_hash=content_hash(text),text=text,engagement=engagement)
    db.add(revision); db.flush()
    return post,revision,True

def classify_revision(db, revision):
    result=classify_rules(revision.text)
    classification=Classification(revision_id=revision.id,model_version='rules-v1',prompt_version='no-ai-rules-v1',result=result)
    db.add(classification); db.flush()
    return classification
