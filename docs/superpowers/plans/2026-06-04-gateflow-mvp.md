# 闸机 GateFlow MVP 实现计划

> **✅ 已完成（2026-06）：** MVP 已全部落地（四条路径：OpenAI 网关 / Anthropic 网关 / Chat / 管理页面），随后完成 5 个 P0 安全修复与 P1/P2 质量治理（大部分完成，进度见 `2026-06-05-gateflow-p1-p2-backlog.md`）。
> **注意：** 本计划基于当时的 `backend/` 嵌套结构和 React 前端编写。当前项目结构已扁平化、前端已迁移到 Jinja2 + htmx + Tailwind（详见 `2026-06-06-frontend-refactor.md`），本文件仅作历史参考。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建企业 AI 网关 MVP，支持统一 API 转发、用户权限管理、审计日志、用量统计和问答对话。

**Architecture:** 模块化单体架构，FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL。后端提供 OpenAI 兼容 API，前端使用 React + Ant Design。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, PostgreSQL, React 18, TypeScript, Ant Design 5

---

## 文件结构总览

```
GateFlow/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI 应用入口
│   │   ├── config.py                  # 配置管理
│   │   ├── database.py                # 数据库连接
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                # Base 模型
│   │   │   ├── user.py                # User, Role, Department
│   │   │   ├── api_key.py             # 用户 API Key
│   │   │   ├── provider_key.py        # 上游 API Key
│   │   │   ├── gateway.py             # ModelConfig
│   │   │   ├── chat.py                # Conversation, Message
│   │   │   ├── audit.py               # AuditLog
│   │   │   └── audit.py               # 请求日志模型（统计也基于此）
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── user.py
│   │   │   ├── api_key.py
│   │   │   ├── provider_key.py
│   │   │   ├── gateway.py
│   │   │   ├── chat.py
│   │   │   ├── audit.py
│   │   │   └── usage.py
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── users.py
│   │   │   ├── api_keys.py
│   │   │   ├── provider_keys.py
│   │   │   ├── gateway.py
│   │   │   ├── chat.py
│   │   │   ├── audit.py
│   │   │   └── usage.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── auth_service.py
│   │   │   ├── gateway_service.py
│   │   │   ├── provider_key_service.py
│   │   │   ├── chat_service.py
│   │   │   ├── audit_service.py
│   │   │   └── usage_service.py
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth_middleware.py
│   │   │   └── logging_middleware.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── security.py            # JWT + bcrypt
│   │       └── http_client.py         # httpx 异步客户端
│   ├── alembic/
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   ├── package.json
│   └── tsconfig.json
└── README.md
```

---

## Task 1: 后端项目初始化

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/database.py`
- Create: `backend/app/main.py`

- [ ] **Step 1: 创建 requirements.txt**

```txt
# backend/requirements.txt
fastapi==0.136.3
uvicorn[standard]==0.48.0
sqlalchemy[asyncio]==2.0.50
asyncpg==0.31.0
alembic==1.18.4
pydantic==2.13.4
pydantic-settings==2.14.1
python-jose[cryptography]==3.5.0
passlib[bcrypt]==1.7.4
httpx==0.28.1
python-multipart==0.0.30
```

- [ ] **Step 2: 创建 .env.example**

```bash
# backend/.env.example
DATABASE_URL=postgresql+asyncpg://Think:pg123456@localhost:5432/gateflow_test
JWT_SECRET_KEY=your-secret-key-change-in-production
JWT_EXPIRE_DAYS=7
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
```

- [ ] **Step 3: 创建 config.py**

```python
# backend/app/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/gateflow"
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_EXPIRE_DAYS: int = 7
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: 创建 database.py**

```python
# backend/app/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
```

- [ ] **Step 5: 创建 main.py**

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="闸机 GateFlow", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

- [ ] **Step 6: 安装依赖并验证**

Run: `cd backend && pip install -r requirements.txt`
Run: `cd backend && python -c "from app.main import app; print('OK')"`
Expected: 输出 `OK`

- [ ] **Step 7: 提交**

```bash
git add backend/
git commit -m "feat: 初始化后端项目结构"
```

---

## Task 2: 数据库模型 - 基础模型和用户系统

**Files:**
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/base.py`
- Create: `backend/app/models/user.py`

- [ ] **Step 1: 创建 base.py**

```python
# backend/app/models/base.py
import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
```

- [ ] **Step 2: 创建 user.py 模型**

```python
# backend/app/models/user.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin


class Department(Base, TimestampMixin):
    __tablename__ = "departments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    
    users = relationship("User", back_populates="department")


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), nullable=False, unique=True)  # admin, user, viewer
    permissions = Column(JSON, default=list)
    
    users = relationship("User", back_populates="role")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(60), nullable=False)  # bcrypt 固定 60 字符
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    last_login = Column(DateTime, nullable=True)
    
    department = relationship("Department", back_populates="users")
    role = relationship("Role", back_populates="users")
    api_keys = relationship("APIKey", back_populates="user")
```

- [ ] **Step 3: 创建 __init__.py**

```python
# backend/app/models/__init__.py
from app.models.base import Base
from app.models.user import User, Role, Department

__all__ = ["Base", "User", "Role", "Department"]
```

- [ ] **Step 4: 验证模型导入**

Run: `cd backend && python -c "from app.models import Base, User, Role, Department; print('Models OK')"`
Expected: 输出 `Models OK`

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/
git commit -m "feat: 添加用户、角色、部门数据模型"
```

---

## Task 3: 数据库模型 - API Key 和网关配置

**Files:**
- Create: `backend/app/models/api_key.py`
- Create: `backend/app/models/provider_key.py`
- Create: `backend/app/models/gateway.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: 创建 api_key.py（用户 API Key）**

```python
# backend/app/models/api_key.py
import uuid
import secrets
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin


def generate_api_key() -> str:
    """生成 gf_ 前缀的 API Key"""
    return f"gf_{secrets.token_urlsafe(45)}"  # 约 60 字符


class APIKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    key = Column(String(64), nullable=False, unique=True, index=True)
    permissions = Column(JSON, default=list)  # 可限制可用模型
    rate_limit = Column(Integer, default=60)  # 每分钟请求数
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    last_used_at = Column(DateTime, nullable=True)
    
    user = relationship("User", back_populates="api_keys")
```

- [ ] **Step 2: 创建 provider_key.py（上游 API Key）**

```python
# backend/app/models/provider_key.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, BigInteger, Text
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, TimestampMixin


class ProviderAPIKey(Base, TimestampMixin):
    __tablename__ = "provider_api_keys"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String(50), nullable=False, index=True)  # deepseek, mimo
    key = Column(String(255), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    remark = Column(Text, nullable=True)
    
    # 状态管理
    is_active = Column(Boolean, default=True, index=True)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(String(255), nullable=True)
    
    # 速率限制
    rpm_limit = Column(Integer, default=60)
    tpm_limit = Column(Integer, default=100000)
    
    # 用量统计
    total_requests = Column(BigInteger, default=0)
    total_input_tokens = Column(BigInteger, default=0)
    total_output_tokens = Column(BigInteger, default=0)
    
    # 故障转移
    consecutive_errors = Column(Integer, default=0)
    cool_down_until = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True, index=True)
```

- [ ] **Step 3: 创建 gateway.py（模型路由配置）**

```python
# backend/app/models/gateway.py
import uuid
from sqlalchemy import Column, String, Boolean, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, TimestampMixin


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_configs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_alias = Column(String(100), nullable=False, unique=True, index=True)
    provider = Column(String(50), nullable=False, index=True)
    target_model = Column(String(100), nullable=False)
    target_url = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    priority = Column(Integer, default=0)
    default_temperature = Column(Float, nullable=True)
    default_max_tokens = Column(Integer, nullable=True)
```

- [ ] **Step 4: 更新 __init__.py**

```python
# backend/app/models/__init__.py
from app.models.base import Base
from app.models.user import User, Role, Department
from app.models.api_key import APIKey
from app.models.provider_key import ProviderAPIKey
from app.models.gateway import ModelConfig

__all__ = ["Base", "User", "Role", "Department", "APIKey", "ProviderAPIKey", "ModelConfig"]
```

- [ ] **Step 5: 验证模型导入**

Run: `cd backend && python -c "from app.models import *; print('All models OK')"`
Expected: 输出 `All models OK`

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/
git commit -m "feat: 添加 API Key 和网关配置数据模型"
```

---

## Task 4: 数据库模型 - 审计日志和用量统计

**Files:**
- Create: `backend/app/models/audit.py`
- Create: `backend/app/models/usage.py`
- Create: `backend/app/models/chat.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: 创建 audit.py**

```python
# backend/app/models/audit.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending/completed/failed
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    username = Column(String(50), nullable=False)
    department = Column(String(100), nullable=True)
    api_key_id = Column(UUID(as_uuid=True), ForeignKey("api_keys.id"), nullable=True)
    api_key_name = Column(String(100), nullable=True)  # 快照：请求发生时 client key 的 name
    agent_type = Column(String(50), nullable=True)
    model = Column(String(100), nullable=False)
    provider = Column(String(50), nullable=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    request_body = Column(Text, nullable=True)
    request_tokens = Column(Integer, default=0)
    response_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=True)
    is_stream = Column(Boolean, default=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("idx_audit_logs_timestamp", "timestamp"),
        Index("idx_audit_logs_user_time", "user_id", "timestamp"),
        Index("idx_audit_logs_dept_time", "department", "timestamp"),
        Index("idx_audit_logs_model_time", "model", "timestamp"),
    )
```

- [ ] **Step 2: ~~创建 usage.py~~（已删除 — 用量统计改为从 AuditLog 实时聚合）**

- [ ] **Step 3: 创建 chat.py**

```python
# backend/app/models/chat.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    model = Column(String(100), nullable=False)
    title = Column(String(255), nullable=True)
    
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    tokens = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    conversation = relationship("Conversation", back_populates="messages")
```

- [ ] **Step 4: 更新 __init__.py**

```python
# backend/app/models/__init__.py
from app.models.base import Base
from app.models.user import User, Role, Department
from app.models.api_key import APIKey
from app.models.provider_key import ProviderAPIKey
from app.models.gateway import ModelConfig
from app.models.audit import AuditLog
from app.models.chat import Conversation, Message

__all__ = [
    "Base", "User", "Role", "Department", "APIKey", "ProviderAPIKey",
    "ModelConfig", "AuditLog", "Conversation", "Message"
]
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/
git commit -m "feat: 添加审计日志、用量统计、对话消息数据模型"
```

---

## Task 5: 工具函数 - 安全和 HTTP 客户端

**Files:**
- Create: `backend/app/utils/__init__.py`
- Create: `backend/app/utils/security.py`
- Create: `backend/app/utils/http_client.py`

- [ ] **Step 1: 创建 security.py**

```python
# backend/app/utils/security.py
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """创建 JWT Token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=settings.JWT_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> Optional[dict]:
    """解码 JWT Token"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        return None
```

- [ ] **Step 2: 创建 http_client.py**

```python
# backend/app/utils/http_client.py
import httpx
from typing import Optional

# 全局异步 HTTP 客户端实例
_client: Optional[httpx.AsyncClient] = None


async def get_http_client() -> httpx.AsyncClient:
    """获取异步 HTTP 客户端单例"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    return _client


async def close_http_client():
    """关闭 HTTP 客户端"""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
```

- [ ] **Step 3: 验证工具函数**

Run: `cd backend && python -c "from app.utils.security import get_password_hash, verify_password; h = get_password_hash('test'); print(verify_password('test', h))"`
Expected: 输出 `True`

- [ ] **Step 4: 提交**

```bash
git add backend/app/utils/
git commit -m "feat: 添加安全工具（JWT + bcrypt）和 HTTP 客户端"
```

---

## Task 6: 认证中间件

**Files:**
- Create: `backend/app/middleware/__init__.py`
- Create: `backend/app/middleware/auth_middleware.py`

- [ ] **Step 1: 创建 auth_middleware.py**

```python
# backend/app/middleware/auth_middleware.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, APIKey
from app.utils.security import decode_access_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """获取当前用户，支持 JWT Token 和 API Key 两种认证方式"""
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息"
        )
    
    token = credentials.credentials
    
    # 判断是 API Key 还是 JWT Token
    if token.startswith("gf_"):
        # API Key 认证
        result = await db.execute(
            select(APIKey).where(
                APIKey.key == token,
                APIKey.is_active == True
            )
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的 API Key"
            )
        
        # 检查是否过期
        if api_key.expires_at and api_key.expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key 已过期"
            )
        
        # 更新最后使用时间
        api_key.last_used_at = datetime.utcnow()
        await db.commit()
        
        # 返回关联的用户
        result = await db.execute(select(User).where(User.id == api_key.user_id))
        user = result.scalar_one_or_none()
    else:
        # JWT Token 认证
        payload = decode_access_token(token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的 Token"
            )
        
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的 Token"
            )
        
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用"
        )
    
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """要求管理员权限"""
    if user.role.name != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return user
```

- [ ] **Step 2: 修复导入（添加 datetime）**

```python
# backend/app/middleware/auth_middleware.py
from datetime import datetime
from fastapi import Depends, HTTPException, status
# ... 其余代码不变
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/middleware/
git commit -m "feat: 添加双模认证中间件（JWT + API Key）"
```

---

## Task 7: Schemas - Pydantic 请求/响应模型

**Files:**
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/schemas/user.py`
- Create: `backend/app/schemas/api_key.py`
- Create: `backend/app/schemas/provider_key.py`
- Create: `backend/app/schemas/gateway.py`

- [ ] **Step 1: 创建 auth.py**

```python
# backend/app/schemas/auth.py
from pydantic import BaseModel
from typing import Optional


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str
```

- [ ] **Step 2: 创建 user.py**

```python
# backend/app/schemas/user.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    department_id: Optional[UUID] = None
    role_id: UUID


class UserUpdate(BaseModel):
    email: Optional[str] = None
    department_id: Optional[UUID] = None
    role_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: UUID
    username: str
    email: str
    department_id: Optional[UUID]
    role_id: UUID
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]
    
    class Config:
        from_attributes = True


class RoleResponse(BaseModel):
    id: UUID
    name: str
    permissions: list
    
    class Config:
        from_attributes = True


class DepartmentResponse(BaseModel):
    id: UUID
    name: str
    parent_id: Optional[UUID]
    
    class Config:
        from_attributes = True


class DepartmentCreate(BaseModel):
    name: str
    parent_id: Optional[UUID] = None
```

- [ ] **Step 3: 创建 api_key.py**

```python
# backend/app/schemas/api_key.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import UUID


class APIKeyCreate(BaseModel):
    name: str
    permissions: List[str] = []
    rate_limit: int = 60
    expires_at: Optional[datetime] = None


class APIKeyUpdate(BaseModel):
    name: Optional[str] = None
    permissions: Optional[List[str]] = None
    rate_limit: Optional[int] = None
    is_active: Optional[bool] = None


class APIKeyResponse(BaseModel):
    id: UUID
    name: str
    key: str
    permissions: List[str]
    rate_limit: int
    expires_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    
    class Config:
        from_attributes = True
```

- [ ] **Step 4: 创建 provider_key.py**

```python
# backend/app/schemas/provider_key.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID


class ProviderKeyCreate(BaseModel):
    provider: str
    key: str
    name: str
    remark: Optional[str] = None
    rpm_limit: int = 60
    tpm_limit: int = 100000


class ProviderKeyUpdate(BaseModel):
    name: Optional[str] = None
    remark: Optional[str] = None
    is_active: Optional[bool] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None


class ProviderKeyResponse(BaseModel):
    id: UUID
    provider: str
    name: str
    remark: Optional[str]
    is_active: bool
    is_banned: bool
    ban_reason: Optional[str]
    rpm_limit: int
    tpm_limit: int
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    consecutive_errors: int
    cool_down_until: Optional[datetime]
    created_at: datetime
    last_used_at: Optional[datetime]
    
    class Config:
        from_attributes = True
```

- [ ] **Step 5: 创建 gateway.py**

```python
# backend/app/schemas/gateway.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID


class ModelConfigCreate(BaseModel):
    model_alias: str
    provider: str
    target_model: str
    target_url: str
    priority: int = 0
    default_temperature: Optional[float] = None
    default_max_tokens: Optional[int] = None


class ModelConfigUpdate(BaseModel):
    target_model: Optional[str] = None
    target_url: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    default_temperature: Optional[float] = None
    default_max_tokens: Optional[int] = None


class ModelConfigResponse(BaseModel):
    id: UUID
    model_alias: str
    provider: str
    target_model: str
    target_url: str
    is_active: bool
    priority: int
    default_temperature: Optional[float]
    default_max_tokens: Optional[int]
    created_at: datetime
    
    class Config:
        from_attributes = True
```

- [ ] **Step 6: 创建 __init__.py**

```python
# backend/app/schemas/__init__.py
from app.schemas.auth import LoginRequest, TokenResponse, PasswordChangeRequest
from app.schemas.user import UserCreate, UserUpdate, UserResponse, RoleResponse, DepartmentResponse, DepartmentCreate
from app.schemas.api_key import APIKeyCreate, APIKeyUpdate, APIKeyResponse
from app.schemas.provider_key import ProviderKeyCreate, ProviderKeyUpdate, ProviderKeyResponse
from app.schemas.gateway import ModelConfigCreate, ModelConfigUpdate, ModelConfigResponse
```

- [ ] **Step 7: 提交**

```bash
git add backend/app/schemas/
git commit -m "feat: 添加 Pydantic 请求/响应模型"
```

---

## Task 8: 认证服务和路由

**Files:**
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/auth_service.py`
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/auth.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 auth_service.py**

```python
# backend/app/services/auth_service.py
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import User, Role
from app.utils.security import get_password_hash, verify_password, create_access_token
from app.config import get_settings

settings = get_settings()


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def authenticate_user(self, username: str, password: str) -> User | None:
        """验证用户名密码"""
        result = await self.db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        
        if not user or not verify_password(password, user.hashed_password):
            return None
        
        # 更新最后登录时间
        user.last_login = datetime.utcnow()
        await self.db.commit()
        
        return user
    
    async def create_token(self, user: User) -> str:
        """为用户创建 JWT Token"""
        return create_access_token({
            "sub": str(user.id),
            "username": user.username,
            "role": user.role.name,
            "department_id": str(user.department_id) if user.department_id else None
        })
    
    async def change_password(self, user: User, old_password: str, new_password: str) -> bool:
        """修改密码"""
        if not verify_password(old_password, user.hashed_password):
            return False
        
        user.hashed_password = get_password_hash(new_password)
        await self.db.commit()
        return True
    
    async def init_admin(self):
        """初始化默认管理员账号"""
        result = await self.db.execute(
            select(User).where(User.username == settings.ADMIN_USERNAME)
        )
        if result.scalar_one_or_none():
            return  # 已存在
        
        # 获取 admin 角色
        result = await self.db.execute(
            select(Role).where(Role.name == "admin")
        )
        admin_role = result.scalar_one_or_none()
        
        if not admin_role:
            admin_role = Role(name="admin", permissions=["*"])
            self.db.add(admin_role)
            await self.db.flush()
        
        # 创建管理员
        admin = User(
            username=settings.ADMIN_USERNAME,
            email=f"{settings.ADMIN_USERNAME}@gateflow.local",
            hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
            role_id=admin_role.id,
            is_active=True
        )
        self.db.add(admin)
        await self.db.commit()
```

- [ ] **Step 2: 创建 auth 路由**

```python
# backend/app/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.auth import LoginRequest, TokenResponse, PasswordChangeRequest
from app.services.auth_service import AuthService
from app.middleware.auth_middleware import get_current_user
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录"""
    auth_service = AuthService(db)
    user = await auth_service.authenticate_user(request.username, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )
    
    token = await auth_service.create_token(user)
    return TokenResponse(access_token=token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """刷新 Token"""
    auth_service = AuthService(db)
    token = await auth_service.create_token(user)
    return TokenResponse(access_token=token)


@router.put("/password")
async def change_password(
    request: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """修改密码"""
    auth_service = AuthService(db)
    success = await auth_service.change_password(
        user, request.old_password, request.new_password
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码错误"
        )
    
    return {"message": "密码修改成功"}
```

- [ ] **Step 3: 注册路由到 main.py**

```python
# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth

app = FastAPI(title="闸机 GateFlow", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

- [ ] **Step 4: 提交**

```bash
git add backend/app/services/ backend/app/routers/ backend/app/main.py
git commit -m "feat: 添加认证服务和登录/刷新/改密接口"
```

---

## Task 9: 用户管理路由

**Files:**
- Create: `backend/app/routers/users.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 users.py**

```python
# backend/app/routers/users.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
from app.database import get_db
from app.models import User, Role, Department
from app.schemas.user import UserCreate, UserUpdate, UserResponse, RoleResponse, DepartmentCreate, DepartmentResponse
from app.middleware.auth_middleware import require_admin
from app.utils.security import get_password_hash

router = APIRouter(prefix="/api/users", tags=["用户管理"])


@router.get("", response_model=List[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """获取用户列表"""
    result = await db.execute(select(User))
    return result.scalars().all()


@router.post("", response_model=UserResponse)
async def create_user(
    request: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """创建用户"""
    # 检查用户名是否已存在
    result = await db.execute(select(User).where(User.username == request.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    user = User(
        username=request.username,
        email=request.email,
        hashed_password=get_password_hash(request.password),
        department_id=request.department_id,
        role_id=request.role_id
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    request: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """更新用户"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)
    
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """删除用户"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    await db.delete(user)
    await db.commit()
    return {"message": "用户已删除"}


@router.get("/roles", response_model=List[RoleResponse])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """获取角色列表"""
    result = await db.execute(select(Role))
    return result.scalars().all()


@router.get("/departments", response_model=List[DepartmentResponse])
async def list_departments(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """获取部门列表"""
    result = await db.execute(select(Department))
    return result.scalars().all()


@router.post("/departments", response_model=DepartmentResponse)
async def create_department(
    request: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """创建部门"""
    department = Department(name=request.name, parent_id=request.parent_id)
    db.add(department)
    await db.commit()
    await db.refresh(department)
    return department
```

- [ ] **Step 2: 注册路由**

```python
# backend/app/main.py
from app.routers import auth, users

app.include_router(auth.router)
app.include_router(users.router)
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/routers/users.py backend/app/main.py
git commit -m "feat: 添加用户管理路由（CRUD + 角色 + 部门）"
```

---

## Task 10: API Key 管理路由

**Files:**
- Create: `backend/app/routers/api_keys.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 api_keys.py**

```python
# backend/app/routers/api_keys.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
from app.database import get_db
from app.models import User, APIKey
from app.models.api_key import generate_api_key
from app.schemas.api_key import APIKeyCreate, APIKeyUpdate, APIKeyResponse
from app.middleware.auth_middleware import get_current_user

router = APIRouter(prefix="/api/api-keys", tags=["API Key 管理"])


@router.get("", response_model=List[APIKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取当前用户的 API Key 列表"""
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user.id)
    )
    return result.scalars().all()


@router.post("", response_model=APIKeyResponse)
async def create_api_key(
    request: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """创建新的 API Key"""
    api_key = APIKey(
        user_id=user.id,
        name=request.name,
        key=generate_api_key(),
        permissions=request.permissions,
        rate_limit=request.rate_limit,
        expires_at=request.expires_at
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key


@router.put("/{key_id}", response_model=APIKeyResponse)
async def update_api_key(
    key_id: UUID,
    request: APIKeyUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """更新 API Key"""
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(api_key, key, value)
    
    await db.commit()
    await db.refresh(api_key)
    return api_key


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """删除 API Key"""
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    
    await db.delete(api_key)
    await db.commit()
    return {"message": "API Key 已删除"}
```

- [ ] **Step 2: 注册路由**

```python
# backend/app/main.py
from app.routers import auth, users, api_keys

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
```

- [ ] **Step 3: 提交**

```bash
git add backend/app/routers/api_keys.py backend/app/main.py
git commit -m "feat: 添加用户 API Key 管理路由"
```

---

## Task 11: 网关管理路由（模型配置 + 上游 Key）

**Files:**
- Create: `backend/app/routers/provider_keys.py`
- Create: `backend/app/routers/gateway.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 provider_keys.py**

```python
# backend/app/routers/provider_keys.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from app.database import get_db
from app.models import User, ProviderAPIKey
from app.schemas.provider_key import ProviderKeyCreate, ProviderKeyUpdate, ProviderKeyResponse
from app.middleware.auth_middleware import require_admin

router = APIRouter(prefix="/api/gateway/provider-keys", tags=["上游 API Key 管理"])


@router.get("", response_model=List[ProviderKeyResponse])
async def list_provider_keys(
    provider: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """获取上游 API Key 列表"""
    query = select(ProviderAPIKey)
    if provider:
        query = query.where(ProviderAPIKey.provider == provider)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=ProviderKeyResponse)
async def create_provider_key(
    request: ProviderKeyCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """添加上游 API Key"""
    key = ProviderAPIKey(
        provider=request.provider,
        key=request.key,
        name=request.name,
        remark=request.remark,
        rpm_limit=request.rpm_limit,
        tpm_limit=request.tpm_limit
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key


@router.put("/{key_id}", response_model=ProviderKeyResponse)
async def update_provider_key(
    key_id: UUID,
    request: ProviderKeyUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """更新上游 API Key"""
    result = await db.execute(select(ProviderAPIKey).where(ProviderAPIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="Key 不存在")
    
    update_data = request.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(key, k, v)
    
    await db.commit()
    await db.refresh(key)
    return key


@router.delete("/{key_id}")
async def delete_provider_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """删除上游 API Key"""
    result = await db.execute(select(ProviderAPIKey).where(ProviderAPIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="Key 不存在")
    
    await db.delete(key)
    await db.commit()
    return {"message": "Key 已删除"}


@router.post("/{key_id}/reset")
async def reset_provider_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """重置 Key 状态（清除错误计数、解封）"""
    result = await db.execute(select(ProviderAPIKey).where(ProviderAPIKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        raise HTTPException(status_code=404, detail="Key 不存在")
    
    key.consecutive_errors = 0
    key.is_banned = False
    key.ban_reason = None
    key.cool_down_until = None
    
    await db.commit()
    return {"message": "Key 状态已重置"}
```

- [ ] **Step 2: 创建 gateway.py（模型配置路由）**

```python
# backend/app/routers/gateway.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
from app.database import get_db
from app.models import User, ModelConfig
from app.schemas.gateway import ModelConfigCreate, ModelConfigUpdate, ModelConfigResponse
from app.middleware.auth_middleware import require_admin

router = APIRouter(prefix="/api/gateway/models", tags=["模型路由管理"])


@router.get("", response_model=List[ModelConfigResponse])
async def list_models(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """获取模型路由列表"""
    result = await db.execute(select(ModelConfig))
    return result.scalars().all()


@router.post("", response_model=ModelConfigResponse)
async def create_model(
    request: ModelConfigCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """添加模型路由"""
    model = ModelConfig(
        model_alias=request.model_alias,
        provider=request.provider,
        target_model=request.target_model,
        target_url=request.target_url,
        priority=request.priority,
        default_temperature=request.default_temperature,
        default_max_tokens=request.default_max_tokens
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


@router.put("/{model_id}", response_model=ModelConfigResponse)
async def update_model(
    model_id: UUID,
    request: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """更新模型路由"""
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(model, key, value)
    
    await db.commit()
    await db.refresh(model)
    return model


@router.delete("/{model_id}")
async def delete_model(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """删除模型路由"""
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    
    await db.delete(model)
    await db.commit()
    return {"message": "模型路由已删除"}
```

- [ ] **Step 3: 注册路由**

```python
# backend/app/main.py
from app.routers import auth, users, api_keys, provider_keys, gateway

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
app.include_router(provider_keys.router)
app.include_router(gateway.router)
```

- [ ] **Step 4: 提交**

```bash
git add backend/app/routers/ backend/app/main.py
git commit -m "feat: 添加网关管理路由（模型配置 + 上游 Key 管理）"
```

---

## Task 12: 网关核心服务（API 转发）

**Files:**
- Create: `backend/app/services/gateway_service.py`
- Create: `backend/app/services/provider_key_service.py`
- Create: `backend/app/routers/gateway_forward.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 provider_key_service.py**

```python
# backend/app/services/provider_key_service.py
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models import ProviderAPIKey


class ProviderKeyService:
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_available_key(self, provider: str) -> ProviderAPIKey | None:
        """获取可用的 API Key，采用故障转移策略"""
        result = await self.db.execute(
            select(ProviderAPIKey)
            .where(
                ProviderAPIKey.provider == provider,
                ProviderAPIKey.is_active == True,
                ProviderAPIKey.is_banned == False,
                (ProviderAPIKey.cool_down_until == None) |
                (ProviderAPIKey.cool_down_until < datetime.utcnow())
            )
            .order_by(ProviderAPIKey.consecutive_errors, ProviderAPIKey.last_used_at)
        )
        keys = result.scalars().all()
        return keys[0] if keys else None
    
    async def update_key_success(self, key_id, input_tokens: int, output_tokens: int):
        """Key 调用成功后的原子更新"""
        await self.db.execute(
            update(ProviderAPIKey)
            .where(ProviderAPIKey.id == key_id)
            .values(
                consecutive_errors=0,
                total_requests=ProviderAPIKey.total_requests + 1,
                total_input_tokens=ProviderAPIKey.total_input_tokens + input_tokens,
                total_output_tokens=ProviderAPIKey.total_output_tokens + output_tokens,
                last_used_at=datetime.utcnow()
            )
        )
        await self.db.commit()
    
    async def update_key_error(self, key_id, status_code: int):
        """Key 调用失败后的处理"""
        result = await self.db.execute(
            select(ProviderAPIKey).where(ProviderAPIKey.id == key_id)
        )
        key = result.scalar_one_or_none()
        if not key:
            return
        
        key.consecutive_errors += 1
        
        if status_code == 429:  # 限流
            key.cool_down_until = datetime.utcnow() + timedelta(minutes=1)
        elif status_code == 401:  # 认证失败
            key.is_banned = True
            key.ban_reason = "上游返回 401 Unauthorized"
        elif key.consecutive_errors >= 10:
            key.cool_down_until = datetime.utcnow() + timedelta(minutes=10)
        
        await self.db.commit()
```

- [ ] **Step 2: 创建 gateway_service.py**

```python
# backend/app/services/gateway_service.py
import asyncio
import time
import json
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from app.models import ModelConfig, ProviderAPIKey, User
from app.services.provider_key_service import ProviderKeyService
from app.services.audit_service import AuditService
from app.services.usage_service import UsageService
from app.utils.http_client import get_http_client


class GatewayService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.key_service = ProviderKeyService(db)
        self.audit_service = AuditService(db)
        self.usage_service = UsageService(db)
    
    async def forward_request(
        self,
        user: User,
        model_config: ModelConfig,
        request_body: dict,
        is_stream: bool
    ):
        """转发请求到上游 API"""
        # 获取可用的 API Key
        key = await self.key_service.get_available_key(model_config.provider)
        if not key:
            raise HTTPException(503, f"提供商 {model_config.provider} 没有可用的 API Key")
        
        # 创建待处理日志
        log_id = await self.audit_service.create_pending_log(
            user=user,
            model=model_config.model_alias,
            provider=model_config.provider,
            request_body=request_body,
            is_stream=is_stream
        )
        
        start_time = time.time()
        
        # 构建请求
        headers = {
            "Authorization": f"Bearer {key.key}",
            "Content-Type": "application/json"
        }
        
        client = await get_http_client()
        
        if is_stream:
            return await self._handle_stream(
                client=client,
                url=f"{model_config.target_url}/chat/completions",
                headers=headers,
                request_body=request_body,
                key=key,
                user=user,
                model=model_config.model_alias,
                log_id=log_id,
                start_time=start_time
            )
        else:
            return await self._handle_non_stream(
                client=client,
                url=f"{model_config.target_url}/chat/completions",
                headers=headers,
                request_body=request_body,
                key=key,
                user=user,
                model=model_config.model_alias,
                log_id=log_id,
                start_time=start_time
            )
    
    async def _handle_stream(
        self, client, url, headers, request_body, key, user, model, log_id, start_time
    ) -> StreamingResponse:
        """处理流式响应"""
        response = await client.stream("POST", url, json=request_body, headers=headers)
        
        async def stream_generator() -> AsyncGenerator[bytes, None]:
            full_response = ""
            async for chunk in response.aiter_bytes():
                yield chunk
                full_response += chunk.decode("utf-8", errors="ignore")
            
            # 响应结束后异步更新日志和统计
            latency_ms = int((time.time() - start_time) * 1000)
            asyncio.create_task(
                self._update_after_response(
                    log_id=log_id,
                    key_id=key.id,
                    user=user,
                    model=model,
                    request_body=request_body,
                    full_response=full_response,
                    status_code=response.status_code,
                    latency_ms=latency_ms
                )
            )
        
        # 透传响应头
        response_headers = dict(response.headers)
        response_headers.pop("content-encoding", None)
        response_headers.pop("transfer-encoding", None)
        
        return StreamingResponse(
            stream_generator(),
            status_code=response.status_code,
            headers=response_headers,
            media_type="text/event-stream"
        )
    
    async def _handle_non_stream(
        self, client, url, headers, request_body, key, user, model, log_id, start_time
    ):
        """处理非流式响应"""
        response = await client.post(url, json=request_body, headers=headers)
        latency_ms = int((time.time() - start_time) * 1000)
        
        response_data = response.json()
        
        # 异步更新日志和统计
        asyncio.create_task(
            self._update_after_response(
                log_id=log_id,
                key_id=key.id,
                user=user,
                model=model,
                request_body=request_body,
                full_response=json.dumps(response_data),
                status_code=response.status_code,
                latency_ms=latency_ms
            )
        )
        
        return response_data
    
    async def _update_after_response(
        self, log_id, key_id, user, model, request_body, full_response, status_code, latency_ms
    ):
        """响应结束后更新日志和统计（后台任务）"""
        # 简单估算 token 数量
        request_tokens = len(json.dumps(request_body)) // 4
        response_tokens = len(full_response) // 4
        
        # 更新日志
        await self.audit_service.update_log(
            log_id=log_id,
            status_code=status_code,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            latency_ms=latency_ms
        )
        
        # 更新 Key 统计
        if 200 <= status_code < 300:
            await self.key_service.update_key_success(key_id, request_tokens, response_tokens)
        else:
            await self.key_service.update_key_error(key_id, status_code)
        
        # 注意：不再调用 usage_service.record_usage()
        # 用量统计从 AuditLog 实时 GROUP BY 聚合（见 Task 12）
```

- [ ] **Step 3: 创建网关转发路由**

```python
# backend/app/routers/gateway_forward.py
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import User, ModelConfig
from app.services.gateway_service import GatewayService
from app.middleware.auth_middleware import get_current_user

router = APIRouter(prefix="/v1", tags=["网关转发"])


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """OpenAI 兼容的对话补全接口"""
    body = await request.json()
    model_alias = body.get("model")
    is_stream = body.get("stream", False)
    
    # 查询模型配置
    result = await db.execute(
        select(ModelConfig).where(
            ModelConfig.model_alias == model_alias,
            ModelConfig.is_active == True
        )
    )
    model_config = result.scalar_one_or_none()
    
    if not model_config:
        return {"error": {"message": f"模型 {model_alias} 不存在或未启用", "type": "invalid_request_error"}}
    
    gateway_service = GatewayService(db)
    return await gateway_service.forward_request(
        user=user,
        model_config=model_config,
        request_body=body,
        is_stream=is_stream
    )


@router.get("/models")
async def list_models(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取可用模型列表"""
    result = await db.execute(select(ModelConfig).where(ModelConfig.is_active == True))
    models = result.scalars().all()
    
    return {
        "object": "list",
        "data": [
            {
                "id": m.model_alias,
                "object": "model",
                "owned_by": m.provider
            }
            for m in models
        ]
    }
```

- [ ] **Step 4: 注册路由**

```python
# backend/app/main.py
from app.routers import auth, users, api_keys, provider_keys, gateway, gateway_forward

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
app.include_router(provider_keys.router)
app.include_router(gateway.router)
app.include_router(gateway_forward.router)
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ backend/app/routers/ backend/app/main.py
git commit -m "feat: 实现网关核心转发功能（流式 + 非流式）"
```

---

## Task 13: 审计日志和用量统计服务

**Files:**
- Create: `backend/app/services/audit_service.py`
- Create: `backend/app/services/usage_service.py`
- Create: `backend/app/routers/audit.py`
- Create: `backend/app/routers/usage.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 audit_service.py**

```python
# backend/app/services/audit_service.py
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import AuditLog, User
from typing import Optional
from uuid import UUID

MAX_LOG_CONTENT_LENGTH = 100 * 1024  # 100KB


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_pending_log(
        self, user: User, model: str, provider: str, request_body: dict, is_stream: bool
    ) -> UUID:
        """创建待处理的日志记录"""
        request_body_str = str(request_body)
        if len(request_body_str) > MAX_LOG_CONTENT_LENGTH:
            request_body_str = request_body_str[:MAX_LOG_CONTENT_LENGTH] + " [内容过长已截断]"
        
        log = AuditLog(
            status="pending",
            user_id=user.id,
            username=user.username,
            department=user.department.name if user.department else None,
            model=model,
            provider=provider,
            method="POST",
            path="/v1/chat/completions",
            request_body=request_body_str,
            is_stream=is_stream
        )
        self.db.add(log)
        await self.db.commit()
        return log.id
    
    async def update_log(
        self, log_id: UUID, status_code: int, request_tokens: int, response_tokens: int, latency_ms: int
    ):
        """更新日志记录"""
        result = await self.db.execute(select(AuditLog).where(AuditLog.id == log_id))
        log = result.scalar_one_or_none()
        
        if not log:
            return
        
        log.status = "completed" if 200 <= status_code < 300 else "failed"
        log.status_code = status_code
        log.request_tokens = request_tokens
        log.response_tokens = response_tokens
        log.total_tokens = request_tokens + response_tokens
        log.latency_ms = latency_ms
        log.completed_at = datetime.utcnow()
        
        await self.db.commit()
    
    async def get_logs(
        self, user_id: Optional[UUID] = None, department: Optional[str] = None,
        model: Optional[str] = None, start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None, page: int = 1, page_size: int = 20
    ):
        """查询日志"""
        query = select(AuditLog)
        
        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if department:
            query = query.where(AuditLog.department == department)
        if model:
            query = query.where(AuditLog.model == model)
        if start_time:
            query = query.where(AuditLog.timestamp >= start_time)
        if end_time:
            query = query.where(AuditLog.timestamp <= end_time)
        
        query = query.order_by(AuditLog.timestamp.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        
        result = await self.db.execute(query)
        return result.scalars().all()
```

- [ ] **Step 2: 创建 usage_service.py（从 AuditLog 实时聚合）**

```python
# backend/app/services/usage_service.py
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, null
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class UsageService:
    """用量统计服务：从 AuditLog 实时 GROUP BY 聚合。

    严格遵循"日志即真相"原则：所有聚合维度（username / department /
    api_key_name）都用 audit_logs 表上的快照字段，不再 JOIN users /
    departments / api_keys。这样：
    - 用户改名后，历史统计仍按当时的 username
    - 员工换部门后，原部门的历史统计不变
    - API Key 重命名后，历史统计仍按当时的 key name
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_summary(
        self,
        dimension: str = "user",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        user_id: Optional[UUID] = None,
    ) -> list:
        """dimension: "user" | "department" | "model" | "api_key"
        所有维度返回 6 个字段（dimension / username / request_count /
        input_tokens / output_tokens / total_tokens），非 user 维度 username 为 null。
        """
        filters = [AuditLog.status_code.isnot(None)]  # 排除 pending
        if user_id:
            filters.append(AuditLog.user_id == user_id)
        if start_date:
            filters.append(AuditLog.timestamp >= datetime.combine(start_date, time.min))
        if end_date:
            filters.append(AuditLog.timestamp < datetime.combine(end_date, time.max))

        if dimension == "user":
            # 快照：直接用 audit_logs.username
            query = (
                select(
                    AuditLog.user_id.label("dimension"),
                    AuditLog.username.label("username"),
                    func.count().label("request_count"),
                    func.coalesce(func.sum(AuditLog.request_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AuditLog.response_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(AuditLog.total_tokens), 0).label("total_tokens"),
                )
                .where(*filters)
                .group_by(AuditLog.user_id, AuditLog.username)
            )
        elif dimension == "department":
            # 快照：直接用 audit_logs.department
            query = (
                select(
                    AuditLog.department.label("dimension"),
                    null().label("username"),
                    func.count().label("request_count"),
                    func.coalesce(func.sum(AuditLog.request_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AuditLog.response_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(AuditLog.total_tokens), 0).label("total_tokens"),
                )
                .where(*filters)
                .group_by(AuditLog.department)
            )
        elif dimension == "model":
            query = (
                select(
                    AuditLog.model.label("dimension"),
                    null().label("username"),
                    func.count().label("request_count"),
                    func.coalesce(func.sum(AuditLog.request_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AuditLog.response_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(AuditLog.total_tokens), 0).label("total_tokens"),
                )
                .where(*filters)
                .group_by(AuditLog.model)
            )
        elif dimension == "api_key":
            # 快照：直接用 audit_logs.api_key_name
            query = (
                select(
                    AuditLog.api_key_name.label("dimension"),
                    null().label("username"),
                    func.count().label("request_count"),
                    func.coalesce(func.sum(AuditLog.request_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AuditLog.response_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(AuditLog.total_tokens), 0).label("total_tokens"),
                )
                .where(*filters)
                .group_by(AuditLog.api_key_name)
            )
        else:
            raise ValueError(f"不支持的聚合维度: {dimension}")

        query = query.order_by(func.coalesce(func.sum(AuditLog.total_tokens), 0).desc())
        result = await self.db.execute(query)
        rows = result.all()

        return [
            {
                "dimension": str(row[0]) if row[0] is not None else "未知",
                "username": row[1],
                "request_count": row[2],
                "input_tokens": row[3],
                "output_tokens": row[4],
                "total_tokens": row[5],
            }
            for row in rows
        ]

    async def get_trend(
        self,
        user_id: UUID | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list:
        """按日聚合用量趋势"""
        query = select(
            func.date(AuditLog.timestamp).label("date"),
            func.count().label("request_count"),
            func.coalesce(func.sum(AuditLog.request_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(AuditLog.response_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(AuditLog.total_tokens), 0).label("total_tokens"),
        ).where(AuditLog.status_code.isnot(None))

        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if start_date:
            query = query.where(AuditLog.timestamp >= datetime.combine(start_date, time.min))
        if end_date:
            query = query.where(AuditLog.timestamp < datetime.combine(end_date, time.max))

        query = query.group_by(func.date(AuditLog.timestamp)).order_by(func.date(AuditLog.timestamp))
        result = await self.db.execute(query)
        return result.all()
```

- [ ] **Step 3: 创建审计日志路由**

```python
# backend/app/routers/audit.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime
from uuid import UUID
from app.database import get_db
from app.models import User
from app.services.audit_service import AuditService
from app.middleware.auth_middleware import get_current_user

router = APIRouter(prefix="/api/audit", tags=["审计日志"])


@router.get("/logs")
async def list_logs(
    user_id: Optional[UUID] = Query(None),
    department: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """查询审计日志"""
    # 非 admin 只能查看自己的日志
    if user.role.name != "admin":
        user_id = user.id
    
    audit_service = AuditService(db)
    logs = await audit_service.get_logs(
        user_id=user_id, department=department, model=model,
        start_time=start_time, end_time=end_time,
        page=page, page_size=page_size
    )
    
    return {"logs": logs, "page": page, "page_size": page_size}
```

- [ ] **Step 4: 创建用量统计路由**

```python
# backend/app/routers/usage.py
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth_middleware import get_current_user, require_admin
from app.models.user import User
from app.services.usage_service import UsageService

router = APIRouter(prefix="/api/usage", tags=["用量统计"])


# 管理员接口
@router.get("/summary")
async def get_usage_summary(
    dimension: str = Query("user", description="聚合维度: user/department/model/api_key"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    service = UsageService(db)
    items = await service.get_summary(
        dimension=dimension, start_date=start_date, end_date=end_date
    )
    return {"dimension": dimension, "items": items}


@router.get("/trend")
async def get_usage_trend(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    data = await UsageService(db).get_trend(start_date=start_date, end_date=end_date)
    return {"data": [...]}


# 普通用户接口（仅自身数据）
@router.get("/my-summary")
async def get_my_usage_summary(
    dimension: str = Query("model", description="聚合维度: model/api_key"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = UsageService(db)
    items = await service.get_summary(
        dimension=dimension, start_date=start_date, end_date=end_date, user_id=user.id
    )
    return {"dimension": dimension, "items": items}


@router.get("/my-trend")
async def get_my_usage_trend(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await UsageService(db).get_trend(user_id=user.id, start_date=start_date, end_date=end_date)
    return {"data": [...]}
```

- [ ] **Step 5: 注册路由**

```python
# backend/app/main.py
from app.routers import auth, users, api_keys, provider_keys, gateway, gateway_forward, audit, usage

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
app.include_router(provider_keys.router)
app.include_router(gateway.router)
app.include_router(gateway_forward.router)
app.include_router(audit.router)
app.include_router(usage.router)
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/ backend/app/routers/ backend/app/main.py
git commit -m "feat: 实现审计日志和用量统计服务及路由"
```

---

## Task 14: 问答对话服务和路由

**Files:**
- Create: `backend/app/services/chat_service.py`
- Create: `backend/app/routers/chat.py`
- Create: `backend/app/schemas/chat.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 创建 chat schemas**

```python
# backend/app/schemas/chat.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from uuid import UUID


class MessageCreate(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    tokens: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class ConversationCreate(BaseModel):
    model: str


class ConversationResponse(BaseModel):
    id: UUID
    model: str
    title: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True
```

- [ ] **Step 2: 创建 chat_service.py**

```python
# backend/app/services/chat_service.py
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Conversation, Message, User
from app.services.gateway_service import GatewayService
from uuid import UUID


class ChatService:
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_conversation(self, user: User, model: str) -> Conversation:
        """创建新对话"""
        conversation = Conversation(
            user_id=user.id,
            model=model,
            title="新对话"
        )
        self.db.add(conversation)
        await self.db.commit()
        await self.db.refresh(conversation)
        return conversation
    
    async def get_conversations(self, user: User) -> list:
        """获取用户的对话列表"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.created_at.desc())
        )
        return result.scalars().all()
    
    async def get_messages(self, conversation_id: UUID, user: User) -> list:
        """获取对话消息"""
        # 验证对话属于当前用户
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return []
        
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return result.scalars().all()
    
    async def send_message(self, conversation_id: UUID, user: User, content: str):
        """发送消息并获取 AI 回复"""
        # 获取对话
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return None
        
        # 保存用户消息
        user_message = Message(
            conversation_id=conversation_id,
            role="user",
            content=content,
            tokens=len(content) // 4
        )
        self.db.add(user_message)
        await self.db.commit()
        
        # 获取历史消息构建上下文
        messages = await self.get_messages(conversation_id, user)
        messages_for_api = [
            {"role": m.role, "content": m.content}
            for m in messages
        ]
        
        # 调用网关
        from app.models import ModelConfig
        result = await self.db.execute(
            select(ModelConfig).where(ModelConfig.model_alias == conversation.model)
        )
        model_config = result.scalar_one_or_none()
        
        if not model_config:
            return None
        
        gateway_service = GatewayService(self.db)
        request_body = {
            "model": model_config.target_model,
            "messages": messages_for_api,
            "stream": False
        }
        
        response = await gateway_service.forward_request(
            user=user,
            model_config=model_config,
            request_body=request_body,
            is_stream=False
        )
        
        # 保存 AI 回复
        ai_content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        ai_message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_content,
            tokens=len(ai_content) // 4
        )
        self.db.add(ai_message)
        await self.db.commit()
        
        return ai_message
    
    async def delete_conversation(self, conversation_id: UUID, user: User) -> bool:
        """删除对话"""
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user.id
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return False
        
        await self.db.delete(conversation)
        await self.db.commit()
        return True
```

- [ ] **Step 3: 创建 chat 路由**

```python
# backend/app/routers/chat.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from app.database import get_db
from app.models import User
from app.schemas.chat import ConversationCreate, ConversationResponse, MessageCreate, MessageResponse
from app.services.chat_service import ChatService
from app.middleware.auth_middleware import get_current_user
from typing import List

router = APIRouter(prefix="/api/chat", tags=["问答对话"])


@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话列表"""
    chat_service = ChatService(db)
    return await chat_service.get_conversations(user)


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    request: ConversationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建新对话"""
    chat_service = ChatService(db)
    return await chat_service.create_conversation(user, request.model)


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取对话消息"""
    chat_service = ChatService(db)
    return await chat_service.get_messages(conversation_id, user)


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: UUID,
    request: MessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """发送消息"""
    chat_service = ChatService(db)
    message = await chat_service.send_message(conversation_id, user, request.content)
    
    if not message:
        raise HTTPException(status_code=400, detail="发送失败")
    
    return message


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除对话"""
    chat_service = ChatService(db)
    success = await chat_service.delete_conversation(conversation_id, user)
    
    if not success:
        raise HTTPException(status_code=404, detail="对话不存在")
    
    return {"message": "对话已删除"}
```

- [ ] **Step 4: 注册路由**

```python
# backend/app/main.py
from app.routers import auth, users, api_keys, provider_keys, gateway, gateway_forward, audit, usage, chat

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
app.include_router(provider_keys.router)
app.include_router(gateway.router)
app.include_router(gateway_forward.router)
app.include_router(audit.router)
app.include_router(usage.router)
app.include_router(chat.router)
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ backend/app/routers/ backend/app/schemas/ backend/app/main.py
git commit -m "feat: 实现问答对话服务和路由"
```

---

## Task 15: 数据库初始化和迁移

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 初始化 Alembic**

Run: `cd backend && alembic init alembic`

- [ ] **Step 2: 配置 alembic.ini**

```ini
# backend/alembic.ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql+asyncpg://postgres:postgres@localhost:5432/gateflow
```

- [ ] **Step 3: 配置 env.py**

```python
# backend/alembic/env.py
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from app.models import Base
from app.config import get_settings

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: 添加初始化逻辑到 main.py**

```python
# backend/app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine
from app.models import Base
from app.services.auth_service import AuthService
from app.database import async_session
from app.routers import auth, users, api_keys, provider_keys, gateway, gateway_forward, audit, usage, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时创建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 初始化管理员
    async with async_session() as db:
        auth_service = AuthService(db)
        await auth_service.init_admin()
    
    yield
    
    # 关闭时清理
    from app.utils.http_client import close_http_client
    await close_http_client()


app = FastAPI(title="闸机 GateFlow", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(api_keys.router)
app.include_router(provider_keys.router)
app.include_router(gateway.router)
app.include_router(gateway_forward.router)
app.include_router(audit.router)
app.include_router(usage.router)
app.include_router(chat.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

- [ ] **Step 5: 生成迁移**

Run: `cd backend && alembic revision --autogenerate -m "initial"`

- [ ] **Step 6: 提交**

```bash
git add backend/alembic backend/app/main.py
git commit -m "feat: 添加数据库迁移和自动初始化"
```

---

## Task 16: 后端测试验证

- [ ] **Step 1: 启动 PostgreSQL**

确保 PostgreSQL 运行并创建数据库：
```bash
createdb gateflow
```

- [ ] **Step 2: 启动后端服务**

Run: `cd backend && uvicorn app.main:app --reload --port 8000`
Expected: 看到 `Uvicorn running on http://127.0.0.1:8000`

- [ ] **Step 3: 测试健康检查**

Run: `curl http://localhost:8000/health`
Expected: `{"status":"ok"}`

- [ ] **Step 4: 测试登录**

Run:
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```
Expected: 返回包含 `access_token` 的 JSON

- [ ] **Step 5: 测试创建用户**

使用上一步获取的 token：
```bash
curl -X POST http://localhost:8000/api/users \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"username":"test","email":"test@test.com","password":"test123","role_id":"<role_id>"}'
```

- [ ] **Step 6: 测试创建 API Key**

```bash
curl -X POST http://localhost:8000/api/api-keys \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"测试 Key"}'
```
Expected: 返回包含 `gf_` 开头的 key

- [ ] **Step 7: 提交最终状态**

```bash
git add -A
git commit -m "feat: 后端 MVP 完成，所有核心功能可用"
```

---

## 实现顺序总结

| Task | 内容 | 预计时间 |
|------|------|---------|
| 1 | 后端项目初始化 | 10 分钟 |
| 2 | 用户数据模型 | 15 分钟 |
| 3 | API Key 和网关模型 | 15 分钟 |
| 4 | 审计日志和用量模型 | 15 分钟 |
| 5 | 工具函数 | 10 分钟 |
| 6 | 认证中间件 | 15 分钟 |
| 7 | Pydantic Schemas | 20 分钟 |
| 8 | 认证服务和路由 | 20 分钟 |
| 9 | 用户管理路由 | 15 分钟 |
| 10 | API Key 管理路由 | 15 分钟 |
| 11 | 网关管理路由 | 20 分钟 |
| 12 | 网关核心转发 | 30 分钟 |
| 13 | 审计日志和用量统计 | 25 分钟 |
| 14 | 问答对话服务 | 25 分钟 |
| 15 | 数据库迁移 | 15 分钟 |
| 16 | 测试验证 | 15 分钟 |
| **总计** | | **约 4 小时** |

---

## 后续任务（前端）

前端实现计划将在后端完成后单独制定，包括：
- React + TypeScript + Ant Design 项目初始化
- 登录页面
- AI 问答页面（豆包风格）
- 管理后台页面
- API 调用封装
