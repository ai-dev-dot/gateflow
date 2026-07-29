"""SystemConfig - singleton table for runtime-mutable admin settings.

This is a "one-row" table (id is always 1) for settings that admins
need to change at runtime - the alternative (Settings from pydantic-
settings) is @lru_cache'd at process start and would require a restart.

v1 fields: backup_dir, backup_include_audit_logs. Add new ones here
and in SystemConfigUpdate schema when needed.

Schema changes (e.g. adding new columns) are now handled by Alembic
migrations under ``alembic/versions/`` -- this module no longer
hand-rolls ``ALTER TABLE`` on startup.
"""

from sqlalchemy import Boolean, CheckConstraint, Column, Integer, String
from sqlalchemy.orm import validates

from app.models.base import Base, TimestampMixin


class SystemConfig(Base, TimestampMixin):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, default=1)
    # Both backup_dir and pg_dump_path are NULL by default. The service
    # refuses to run a backup if either is unset - admin must opt in.
    # Nullable here so we can store None instead of a sentinel string.
    backup_dir = Column(String(500), nullable=True, default=None)
    backup_include_audit_logs = Column(Boolean, nullable=False, default=False)
    # Absolute path to the pg_dump binary. The admin fills this in via the
    # /backup settings UI; the service does NOT search PATH or hardcoded
    # common install locations. If NULL, run_backup raises a clear error
    # telling the admin to set it. Default NULL (admin must opt in).
    pg_dump_path = Column(String(500), nullable=True, default=None)

    # Enforce the singleton invariant at the DB layer. If anything tries
    # to insert a second row, PG/SQLite both reject it.
    __table_args__ = (CheckConstraint("id = 1", name="ck_system_config_singleton"),)

    @validates("backup_dir")
    def _validate_backup_dir(self, key, value):
        # None = "not set", allowed. Non-None must be a non-empty string.
        # The service layer's update_config() raises a friendlier error
        # for empty input that the router maps to 422.
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("backup_dir must not be empty")
        return stripped
