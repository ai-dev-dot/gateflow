from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.user import Role, User
from app.utils.datetime_utils import utcnow
from app.utils.security import create_access_token, get_password_hash, verify_password

settings = get_settings()


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def authenticate_user(self, username: str, password: str) -> User | None:
        """验证用户凭据，成功则更新 last_login 并返回 User"""
        result = await self.db.execute(
            select(User).where(User.username == username).options(selectinload(User.role))
        )
        user = result.scalar_one_or_none()

        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None

        user.last_login = utcnow()
        await self.db.commit()
        await self.db.refresh(user)
        return user

    def create_token(self, user: User) -> str:
        """创建 JWT token，包含 sub, username, role, department_id"""
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role.name if user.role else "user",
            "department_id": str(user.department_id) if user.department_id else None,
        }
        return create_access_token(data=payload)

    async def change_password(self, user: User, old_password: str, new_password: str) -> bool:
        """修改密码，验证旧密码后更新"""
        if not verify_password(old_password, user.hashed_password):
            return False

        user.hashed_password = get_password_hash(new_password)
        await self.db.commit()
        return True

    async def init_admin(self) -> None:
        """如果不存在管理员用户，则创建默认管理员"""
        result = await self.db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
        admin = result.scalar_one_or_none()
        if admin:
            return

        # 确保 admin 角色存在
        result = await self.db.execute(select(Role).where(Role.name == "admin"))
        admin_role = result.scalar_one_or_none()
        if not admin_role:
            admin_role = Role(name="admin", permissions={"all": True})
            self.db.add(admin_role)
            await self.db.flush()

        admin_user = User(
            username=settings.ADMIN_USERNAME,
            email=f"{settings.ADMIN_USERNAME}@gateflow.local",
            hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
            role_id=admin_role.id,
            is_active=True,
        )
        self.db.add(admin_user)
        await self.db.commit()
