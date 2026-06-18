"""JWT 认证 + Internal Key"""
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRES_IN, INTERNAL_API_KEY
from database import get_db
from shared_db.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRES_IN)
    return jwt.encode({"sub": str(user_id), "username": username, "exp": expire},
                      JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
    request: Request = None,
) -> User:
    # Internal API key for email_worker etc.
    if request:
        internal_key = request.headers.get("x-internal-key", "")
        if internal_key and internal_key == INTERNAL_API_KEY:
            user = db.query(User).filter(User.username == "admin").first()
            if user:
                return user

    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user
