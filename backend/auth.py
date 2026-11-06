from datetime import datetime, timezone, timedelta
from hashlib import sha256
import secrets
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from .database import get_db
from .models import AuthSession, User

passwords = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)
DUMMY_HASH = passwords.hash('not-a-real-user-password')

def digest(token): return sha256(token.encode()).hexdigest()
def utc(dt): return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

def issue_session(db, user):
    token = secrets.token_urlsafe(48)
    db.add(AuthSession(token_hash=digest(token), user_id=user.id, expires_at=datetime.now(timezone.utc)+timedelta(days=7)))
    db.commit()
    return {'token': token, 'username': user.username, 'expires_in': 604800}

def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    if not creds: raise HTTPException(401, '请先登录')
    session = db.get(AuthSession, digest(creds.credentials))
    if not session or utc(session.expires_at) <= datetime.now(timezone.utc): raise HTTPException(401, '登录已过期，请重新登录')
    user = db.scalar(select(User).where(User.id==session.user_id))
    if not user: raise HTTPException(401, '账号不存在')
    return user
