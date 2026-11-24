"""空库初始化的顺序回归：角色必须先于带 GRANT 的迁移创建。"""

from __future__ import annotations

from types import SimpleNamespace

from scripts import init_db


class _FakeResult:
    def fetchone(self):
        return None


class _FakeAdmin:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _FakeResult()


def _settings():
    return SimpleNamespace(
        db_admin_url="postgresql://admin@localhost/test",
        db_app_role="ops_app",
        db_app_password="app-secret",
        db_audit_reader_role="ops_audit_reader",
        db_audit_reader_password="reader-secret",
    )


def test_ensure_roles_creates_roles_without_table_grants(monkeypatch):
    admin = _FakeAdmin()
    monkeypatch.setattr(init_db, "connect_admin", lambda settings: admin)

    init_db.ensure_roles(_settings())

    sql_text = "\n".join(admin.statements)
    assert "CREATE ROLE" in sql_text
    assert "ops_app" in sql_text
    assert "ops_audit_reader" in sql_text
    assert "GRANT" not in sql_text


def test_init_schema_runs_roles_before_migrations(monkeypatch, tmp_path):
    events = []

    def fake_ensure_roles(_settings):
        events.append("roles")

    class _MigrationAdmin(_FakeAdmin):
        def execute(self, statement, params=None):
            events.append("migration")
            return super().execute(statement, params)

    monkeypatch.setattr(init_db, "ensure_roles", fake_ensure_roles)
    monkeypatch.setattr(init_db, "connect_admin", lambda settings: _MigrationAdmin())
    monkeypatch.setattr(init_db, "MIGRATIONS", tmp_path)
    (tmp_path / "031_task_comments.sql").write_text(
        "GRANT SELECT ON v3_task_comments TO ops_app;", encoding="utf-8"
    )

    init_db.init_schema(_settings())

    assert events == ["roles", "migration"]
