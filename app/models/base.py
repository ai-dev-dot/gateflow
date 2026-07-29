from sqlalchemy import Column, DateTime
from sqlalchemy.orm import DeclarativeBase

from app.utils.datetime_utils import utcnow


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
