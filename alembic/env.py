"""Alembic env.py -- async 模式，复用 app 的 asyncpg engine。

连接串从 `app.config.get_settings().DATABASE_URL` 读取（即 .env），
不依赖 alembic.ini 里的 sqlalchemy.url，避免硬编码凭据。

target_metadata 指向 `Base.metadata`；`from app.models import Base` 的
副作用是触发 app/models/__init__.py 把所有 model 模块加载注册到 metadata，
autogenerate 才能看到全部表。
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from app.config import get_settings
from app.models import Base  # noqa: F401  -- import 副作用：注册所有 model

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 从 .env 注入连接串，覆盖 alembic.ini 的占位
DB_URL = get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连库。"""
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """在线模式：用 asyncpg async engine 跑迁移。"""
    connectable = create_async_engine(DB_URL, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
