"""Datetime helpers.

``utcnow()`` is a drop-in replacement for the deprecated
``datetime.datetime.utcnow()`` (deprecated in Python 3.12+, projects on
3.13 emit a DeprecationWarning on every call -- 798 warnings in the test
suite before this helper).

Returns a **tz-naive** UTC datetime to match the project's ``DateTime``
columns (PG ``timestamp without time zone`` via asyncpg, which rejects
tz-aware values; SQLite via aiosqlite is lenient but we keep behavior
identical across both). Behaves exactly like ``datetime.utcnow()``
minus the warning -- do NOT switch callers to ``datetime.now(UTC)``,
that returns tz-aware and breaks asyncpg writes + naive/aware
comparisons against DB-read values.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC datetime, non-deprecated replacement for ``datetime.utcnow()``."""
    return datetime.now(UTC).replace(tzinfo=None)
