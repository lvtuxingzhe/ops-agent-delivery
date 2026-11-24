"""主机域拆分后的兼容门面回归测试。"""

from __future__ import annotations

import inspect

from app.services import exec_hosts
from app.services.hosts import repository, service


def test_facade_reexports_public_symbols() -> None:
    assert exec_hosts.get_host is service.get_host
    assert exec_hosts.resolve_host is service.resolve_host
    assert exec_hosts.list_hosts is service.list_hosts
    assert exec_hosts.upsert_host is service.upsert_host
    assert exec_hosts.set_host_password is service.set_host_password
    assert exec_hosts.set_host_enabled is service.set_host_enabled
    assert exec_hosts.target_of is service.target_of


def test_facade_patch_only_affects_facade_callers(monkeypatch) -> None:
    sentinel = lambda *_args, **_kwargs: None  # noqa: E731
    monkeypatch.setattr(exec_hosts, "get_host", sentinel)

    assert exec_hosts.get_host is sentinel
    assert service.get_host is not sentinel


def test_repository_reads_accept_optional_connection() -> None:
    for name in ("load_host", "load_password_state", "load_value", "load_value_row"):
        signature = inspect.signature(getattr(repository, name))
        assert "conn" in signature.parameters
        assert signature.parameters["conn"].default is None
        assert signature.parameters["conn"].kind is inspect.Parameter.KEYWORD_ONLY
