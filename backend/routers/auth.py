"""POST /api/auth/login, POST /api/auth/register"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from shared_db.models import User
from auth import hash_password, verify_password, create_access_token, get_current_user, JWT_EXPIRES_IN

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class RegisterReq(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "token": create_access_token(user.id, user.username),
        "username": user.username,
        "expires_in": JWT_EXPIRES_IN,
    }


@router.post("/register")
def register(req: RegisterReq, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(username=req.username, password_hash=hash_password(req.password))
    db.add(user)
    db.commit()
    return {"message": "注册成功", "username": user.username}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "id": user.id}
