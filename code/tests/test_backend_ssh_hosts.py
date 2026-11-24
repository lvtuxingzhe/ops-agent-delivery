"""后端 SSH 主机选择：默认不猜、文本全量命中、显式范围才过滤。"""

from types import SimpleNamespace

from app.services import investigation_service as svc

HOSTS = [
    {"id": "vm-150", "name": "150 主机", "address": "10.0.0.150",
     "platform": "linux", "enabled": True},
    {"id": "vm-151", "name": "151 主机", "address": "10.0.0.151",
     "platform": "linux", "enabled": True},
    {"id": "vm-152", "name": "152 主机", "address": "10.0.0.152",
     "platform": "linux", "enabled": True},
    {"id": "vm-153", "name": "153 主机", "address": "10.0.0.153",
     "platform": "linux", "enabled": True},
    {"id": "vm-154", "name": "154 主机", "address": "10.0.0.154",
     "platform": "linux", "enabled": True},
]


def _fake_list(_settings):
    return [{**item} for item in HOSTS]


def _fake_get(_settings, host_id):
    for item in HOSTS:
        if item["id"] == host_id:
            return {**item, "local": False}
    return None


def _fake_target(host, settings=None):
    return {
        "address": host["address"],
        "port": 22,
        "user": "root",
        "password_enc": "gAAAAA-test",
    }


def _patch_hosts(monkeypatch, hosts=None):
    monkeypatch.setattr(
        svc.exec_hosts, "list_hosts",
        lambda _settings: [{**item} for item in (hosts or HOSTS)],
    )
    monkeypatch.setattr(svc.exec_hosts, "get_host", _fake_get)
    monkeypatch.setattr(svc.exec_hosts, "target_of", _fake_target)


def test_no_ssh_scope_never_falls_back_to_single_host(monkeypatch):
    """告警映射不到主机时显式 no_ssh，不能被“只有一台启用主机”兜底。"""
    _patch_hosts(monkeypatch, [HOSTS[0]])
    result = svc._ssh_hosts_for_task(
        SimpleNamespace(), {"no_ssh": True, "alert_host": "vm-999"}
    )
    assert result == []


def test_single_enabled_host_is_used_when_scope_empty(monkeypatch):
    _patch_hosts(monkeypatch, [HOSTS[0]])
    result = svc._ssh_hosts_for_task(SimpleNamespace(), {})
    assert [item["id"] for item in result] == ["vm-150"]


def test_multiple_hosts_without_scope_does_not_guess(monkeypatch):
    _patch_hosts(monkeypatch)
    assert svc._ssh_hosts_for_task(SimpleNamespace(), {}) == []


def test_text_hint_selects_single_host(monkeypatch):
    _patch_hosts(monkeypatch)
    result = svc._ssh_hosts_for_task(
        SimpleNamespace(), {}, hint="帮我看看150上面有哪些服务"
    )
    assert [item["id"] for item in result] == ["vm-150"]


def test_text_hint_accepts_full_address(monkeypatch):
    _patch_hosts(monkeypatch)
    result = svc._ssh_hosts_for_task(
        SimpleNamespace(), {}, hint="检查 10.0.0.151 的磁盘"
    )
    assert [item["id"] for item in result] == ["vm-151"]


def test_text_hint_returns_all_hosts_when_multiple(monkeypatch):
    _patch_hosts(monkeypatch)
    result = svc._ssh_hosts_for_task(
        SimpleNamespace(), {}, hint="对比 vm-150 和 vm-151 的服务"
    )
    assert [item["id"] for item in result] == ["vm-150", "vm-151"]


def test_bare_number_list_is_resolved(monkeypatch):
    _patch_hosts(monkeypatch)
    result = svc._ssh_hosts_for_task(
        SimpleNamespace(), {}, hint="我要查150和151主机有哪些服务"
    )
    assert [item["id"] for item in result] == ["vm-150", "vm-151"]


def test_list_and_range_forms_are_resolved(monkeypatch):
    _patch_hosts(monkeypatch)
    cases = {
        "150、151、152": ["vm-150", "vm-151", "vm-152"],
        "10.0.0.150 和 10.0.0.151": ["vm-150", "vm-151"],
        "150-154": ["vm-150", "vm-151", "vm-152", "vm-153", "vm-154"],
        "vm-150 到 vm-154": ["vm-150", "vm-151", "vm-152", "vm-153", "vm-154"],
        "10.0.0.150-10.0.0.153": ["vm-150", "vm-151", "vm-152", "vm-153"],
        "所有主机": ["vm-150", "vm-151", "vm-152", "vm-153", "vm-154"],
    }
    for hint, expected in cases.items():
        result = svc._ssh_hosts_for_task(SimpleNamespace(), {}, hint=hint)
        assert [item["id"] for item in result] == expected, hint


def test_unrelated_numbers_do_not_match(monkeypatch):
    _patch_hosts(monkeypatch)
    for hint in ("CPU 使用率 150%，帮我看一下", "端口150被占用", "1500台机器"):
        assert svc._ssh_hosts_for_task(
            SimpleNamespace(), {}, hint=hint
        ) == [], hint


def test_explicit_host_id_or_address_filters(monkeypatch):
    _patch_hosts(monkeypatch)
    by_id = svc._ssh_hosts_for_task(SimpleNamespace(), {"host": "vm-151"})
    by_addr = svc._ssh_hosts_for_task(SimpleNamespace(), {"hosts": ["10.0.0.150"]})
    assert [item["id"] for item in by_id] == ["vm-151"]
    assert [item["id"] for item in by_addr] == ["vm-150"]
