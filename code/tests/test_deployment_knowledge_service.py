"""部署失败知识：只读摘要、排序、同因去重和评估任务排除。"""

from __future__ import annotations

from datetime import datetime

from app.services import deployment_knowledge_service as service


def _row(task_id: str, *, title: str = "部署 keepalived", reason: str = "verify_expect_mismatch",
         template_id: str = "tpl-1", os_family: str = "rhel",
         updated: str = "2026-11-17T08:00:00+00:00") -> dict:
    return {
        "id": task_id, "title": title, "description": "在 host-150 上部署 keepalived",
        "outcome": "failed", "reason_code": reason, "stage": "deployment",
        "updated_at": datetime.fromisoformat(updated),
        "plan": {"template_id": template_id, "template_version": 2,
                 "host_os_family": os_family, "host": "host-150",
                 "summary": "yum 安装 keepalived 并启动服务"},
        "approval": {"exec": {"failure": {
            "code": "verify_expect_mismatch",
            "message": "业务验证结果与模板预期不一致",
            "expected": {"exit_code": 1}, "actual": {"exit_code": 0},
            "command": "rpm -q keepalived", "stderr_tail": "secret stdout",
        }}},
    }


def test_build_item_only_keeps_safe_summary():
    item = service.build_item(_row("a" * 36))
    assert item is not None
    assert item["template_id"] == "tpl-1"
    assert item["os_family"] == "rhel"
    assert "期望 exit 1" in item["failure_summary"]
    assert "rpm -q" not in item["failure_summary"]
    assert "secret stdout" not in str(item)


def test_build_item_falls_back_to_ai_host_and_target_scope():
    """AI 自由方案没有 plan.host 时，仍要拿到目标主机。"""
    row = _row("f" * 36)
    row["plan"] = {"ai_host": "host-154", "host_os_family": "linux", "ai_mode": "freeform"}
    row["target_scope"] = {"host": "host-154", "platform": "linux"}
    item = service.build_item(row)
    assert item is not None
    assert item["host"] == "host-154"

    row = _row("g" * 36)
    row["plan"] = {"host_os_family": "linux", "ai_mode": "freeform"}
    row["target_scope"] = {"host": "local", "platform": "linux"}
    item = service.build_item(row)
    assert item is not None
    assert item["host"] == "local"


def test_eval_titles_are_excluded():
    assert service.build_item(_row("b" * 36, title="[eval] verify 失败")) is None
    assert service.build_item(_row("c" * 36, title="E2E五阶段-verify失败")) is None
    assert service.build_item(_row("d" * 36, title="部署演练-自检")) is None


def test_rank_prefers_same_template_and_os_and_dedupes():
    rows = [
        _row("1" * 36, updated="2026-11-17T08:00:00+00:00"),
        _row("2" * 36, updated="2026-11-17T07:00:00+00:00"),
        _row("3" * 36, template_id="tpl-2", os_family="debian",
             reason="exit_code_nonzero", updated="2026-11-17T06:00:00+00:00"),
        _row("4" * 36, template_id="tpl-3", os_family="rhel",
             reason="apply_failed", updated="2026-11-17T05:00:00+00:00"),
    ]
    items = service.rank_deployments(
        "部署 keepalived", rows, template_id="tpl-1", os_family="rhel",
        reason_code="verify_expect_mismatch", stage="deployment", limit=3,
    )
    assert items[0]["task_id"] == "1" * 36
    # 同模板同原因只留最新一条；另外两条按原因/相似度补充。
    assert [item["task_id"] for item in items] == ["1" * 36, "3" * 36, "4" * 36]


def test_rank_returns_at_most_three():
    rows = [_row(str(index) * 36) for index in range(1, 7)]
    items = service.rank_deployments(
        "部署 keepalived", rows, template_id="tpl-1", os_family="rhel",
        reason_code="verify_expect_mismatch", limit=3,
    )
    assert len(items) == 1
