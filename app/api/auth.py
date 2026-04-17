"""人类用户鉴权 API：注册、登录、JWT 验证依赖。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import secrets
import uuid

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.core.config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET, INTEREST_OPTIONS
from app.database import get_db
from app.models.comment import Comment
from app.models.user import User
from app.services.email_service import create_and_send_code, verify_code

router = APIRouter(prefix="/auth", tags=["auth"])

AVATAR_DIR = Path(__file__).parent.parent / "static" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    salt, hashed = stored.split("$", 1)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return secrets.compare_digest(h.hex(), hashed)


# ── 用户名校验 ────────────────────────────────────────────

_USERNAME_RE = re.compile(r'^[\w\u4e00-\u9fff]+$')  # 字母、数字、下划线、中文
_RESERVED_NAMES = {
    'admin', 'administrator', 'root', 'system', 'test', 'demo',
    'official', 'support', 'help', 'moderator', 'mod', 'bot',
    '管理员', '官方', '系统', '测试', '客服',
}


def _validate_username(v: str) -> str:
    v = v.strip()
    if ' ' in v or '\t' in v:
        raise ValueError('用户名不能包含空格')
    if not _USERNAME_RE.match(v):
        raise ValueError('用户名只能包含字母、数字、下划线和中文')
    if v.lower() in _RESERVED_NAMES:
        raise ValueError('该用户名为保留名称，请更换')
    return v


# ── Schemas ──────────────────────────────────────────────

class SendCodeRequest(BaseModel):
    email: EmailStr


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=4, max_length=32)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    code: str = Field(..., min_length=4, max_length=8)
    interests: list[str] = Field(default_factory=list)

    @field_validator('username')
    @classmethod
    def check_username(cls, v: str) -> str:
        return _validate_username(v)


class LoginRequest(BaseModel):
    username: str
    password: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str = Field(..., min_length=6, max_length=128)
    code: str = Field(..., min_length=4, max_length=8)


class AuthResponse(BaseModel):
    user_id: int
    username: str
    avatar: str | None = None
    bio: str | None = None
    interests: list[str] = Field(default_factory=list)
    token: str
    message: str


class UserInfo(BaseModel):
    id: int
    username: str
    email: str
    avatar: str | None = None
    bio: str | None = None
    interests: list[str] = Field(default_factory=list)
    created_at: datetime


class UpdateProfileRequest(BaseModel):
    username: str | None = Field(None, min_length=4, max_length=32)
    bio: str | None = Field(None, max_length=500)
    interests: list[str] | None = None

    @field_validator('username')
    @classmethod
    def check_username(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_username(v)
        return v


# ── JWT 工具 ─────────────────────────────────────────────

def _create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    authorization: str = Header(..., description="Bearer <jwt_token>"),
    db: Session = Depends(get_db),
) -> User:
    """JWT 鉴权依赖：从 Authorization header 解析出当前登录的人类用户。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authorization header 格式应为: Bearer <token>")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "token 不能为空")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "无效的 token")

    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


def get_optional_user(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> User | None:
    """可选鉴权：有 token 则返回用户，没有或无效则返回 None。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
    return db.get(User, int(payload["sub"]))


# ── 发送验证码 ─────────────────────────────────────────────

@router.post("/send-code")
def send_code(body: SendCodeRequest):
    try:
        create_and_send_code(body.email)
    except ValueError as e:
        raise HTTPException(429, str(e))
    except Exception as e:
        raise HTTPException(500, f"邮件发送失败: {e}")
    return {"message": "验证码已发送，请查收邮箱"}


# ── 注册 ─────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if not verify_code(body.email, body.code):
        raise HTTPException(400, "验证码错误或已过期")

    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "用户名已被占用")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(409, "邮箱已被注册")

    valid_codes = {code for code, _ in INTEREST_OPTIONS}
    interests = [i for i in body.interests if i in valid_codes] or None

    user = User(
        username=body.username,
        email=body.email,
        password_hash=_hash_password(body.password),
        interests=interests,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return AuthResponse(
        user_id=user.id,
        username=user.username,
        avatar=user.avatar,
        bio=user.bio,
        interests=user.interests or [],
        token=_create_token(user.id, user.username),
        message="注册成功",
    )


# ── 登录 ─────────────────────────────────────────────────

@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")

    return AuthResponse(
        user_id=user.id,
        username=user.username,
        avatar=user.avatar,
        bio=user.bio,
        interests=user.interests or [],
        token=_create_token(user.id, user.username),
        message="登录成功",
    )


# ── 重置密码 ─────────────────────────────────────────────

@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    if not verify_code(body.email, body.code):
        raise HTTPException(400, "验证码错误或已过期")

    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user:
        raise HTTPException(404, "该邮箱未注册")

    user.password_hash = _hash_password(body.new_password)
    db.commit()
    return {"message": "密码重置成功，请使用新密码登录"}


# ── 获取当前用户信息 ─────────────────────────────────────

@router.get("/me", response_model=UserInfo)
def get_me(user: User = Depends(get_current_user)):
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        avatar=user.avatar,
        bio=user.bio,
        interests=user.interests or [],
        created_at=user.created_at,
    )


class UserPublicProfile(BaseModel):
    id: int
    username: str
    avatar: str | None = None
    bio: str | None = None
    created_at: datetime
    total_likes: int = 0
    total_dislikes: int = 0
    comment_count: int = 0


@router.get("/user/{user_id}", response_model=UserPublicProfile)
def get_user_profile(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")

    stats = (
        db.query(
            sa_func.coalesce(sa_func.sum(Comment.likes), 0).label("total_likes"),
            sa_func.coalesce(sa_func.sum(Comment.dislikes), 0).label("total_dislikes"),
            sa_func.count(Comment.id).label("comment_count"),
        )
        .filter(Comment.user_id == user_id)
        .first()
    )

    return UserPublicProfile(
        id=user.id,
        username=user.username,
        avatar=user.avatar,
        bio=user.bio,
        created_at=user.created_at,
        total_likes=int(stats.total_likes),
        total_dislikes=int(stats.total_dislikes),
        comment_count=int(stats.comment_count),
    )


@router.put("/me", response_model=UserInfo)
def update_profile(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.username is not None and body.username != user.username:
        existing = db.query(User).filter(User.username == body.username).first()
        if existing:
            raise HTTPException(409, "用户名已被占用")
        user.username = body.username
    if body.bio is not None:
        user.bio = body.bio
    if body.interests is not None:
        valid_codes = {code for code, _ in INTEREST_OPTIONS}
        user.interests = [i for i in body.interests if i in valid_codes] or None
    db.commit()
    db.refresh(user)
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        avatar=user.avatar,
        bio=user.bio,
        interests=user.interests or [],
        created_at=user.created_at,
    )


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"不支持的图片格式，仅允许 JPEG/PNG/GIF/WebP")
    data = await file.read()
    if len(data) > MAX_AVATAR_SIZE:
        raise HTTPException(400, "头像图片不能超过 2MB")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "png"
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        ext = "png"
    filename = f"{user.id}_{uuid.uuid4().hex[:8]}.{ext}"

    # delete old avatar file if it exists
    if user.avatar and user.avatar.startswith("/static/avatars/"):
        old_path = AVATAR_DIR / user.avatar.split("/")[-1]
        if old_path.exists():
            old_path.unlink(missing_ok=True)

    save_path = AVATAR_DIR / filename
    save_path.write_bytes(data)

    user.avatar = f"/static/avatars/{filename}"
    db.commit()
    db.refresh(user)
    return {"avatar": user.avatar, "message": "头像上传成功"}


@router.get("/interest-options")
def get_interest_options():
    return [{"code": code, "label": label} for code, label in INTEREST_OPTIONS]
