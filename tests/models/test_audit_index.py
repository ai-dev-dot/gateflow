"""P1-9 closure: the pending-status partial index must stay declared on the model.

`postgresql_where` partial indexes (PG-only) are silently ignored by SQLite,
so the SQLite test suite cannot exercise their runtime behavior — production
PG gets the real index via the Alembic baseline migration (already verified).
This test locks the model-side declaration: if a future refactor drops the
index config, the suite fails loudly instead of silently losing the
production index that the pending-cleanup query relies on (P2-6).
"""

from app.models.audit import AuditLog


def test_audit_pending_partial_index_declared():
    by_name = {ix.name: ix for ix in AuditLog.__table__.indexes}
    assert "ix_audit_logs_status_pending" in by_name, "pending partial index must stay declared"
    ix = by_name["ix_audit_logs_status_pending"]
    assert [c.name for c in ix.columns] == ["status"]
    opts = dict(ix.dialect_options)
    where = opts.get("postgresql", {}).get("where")
    assert where is not None, "postgresql_where predicate must be set"
    needle = str(where).replace(" ", "").lower()
    assert "status" in needle and "pending" in needle
