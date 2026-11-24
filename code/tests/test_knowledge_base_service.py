"""知识库聚合、去重、手工条目与部署类型推断的单元测试。

这些用例只测纯逻辑，不连数据库：`_summarize_*`、`dedupe`、`infer_deploy_type`
都是纯函数，用真实任务结构造数据即可覆盖关键规则。
"""

from __future__ import annotations

from datetime import datetime

from app.services import deployment_taxonomy as taxonomy
from app.services import knowledge_base_service as kb
from app.services.knowledge_base import aggregate


def _deploy_row(task_id: str, *, title: str = "在 vm-150 上部署 keepalived",
                outcome: str = "failed", reason: str = "verify_expect_mismatch",
                updated: str = "2026-11-17T08:00:00+00:00") -> dict:
    return {
        "id": task_id, "title": title, "description": "在 vm-150 上部署 keepalived",
        "outcome": outcome, "reason_code": reason, "stage": "deployment",
        "updated_at": updated,
        "created_at": datetime.fromisoformat(updated),
        "plan": {
            "template_id": "tpl-1", "template_version": 2,
            "host_os_family": "rhel", "host": "vm-150",
            "summary": "yum 安装 keepalived 并启动服务",
            "operations": [{"argv": ["yum", "install", "-y", "keepalived"]}],
            "verify": [{"argv": ["systemctl", "is-active", "keepalived"]}],
            "rollback": [{"argv": ["yum", "remove", "-y", "keepalived"]}],
        },
        "approval": {"exec": {"failure": {
            "code": "verify_expect_mismatch",
            "message": "业务验证结果与模板预期不一致",
            "expected": {"exit_code": 1}, "actual": {"exit_code": 0},
            "command": "rpm -q keepalived", "stderr_tail": "secret stdout",
        }}},
    }


def _investigation_row(task_id: str, *, title: str = "vm-150 上 nginx 无法启动",
                       updated: str = "2026-11-17T08:00:00+00:00") -> dict:
    return {
        "id": task_id, "title": title,
        "description": "排查 nginx 启动失败",
        "report": {
            "verdict": "配置端口冲突",
            "headline": "80 端口被 httpd 占用，nginx 起不来",
            "summary": "httpd 监听 80，nginx 绑定失败",
            "conclusions": [
                {"text": "httpd 占用 80 端口"},
                {"text": "nginx 配置未改端口"},
                {"text": "停掉 httpd 后可启动"},
                {"text": "第四条应该被裁掉"},
            ],
        },
        "created_at": datetime.fromisoformat(updated),
        "updated_at": updated,
    }


def test_deployment_summary_is_short_and_has_no_command_text():
    item = kb._summarize_deployment(_deploy_row("a" * 36))
    assert item is not None
    assert item["kind"] == "deployment"
    assert len(item["summary"]) <= kb.MAX_SUMMARY
    # 正文不再按 1000 字截断，但 argv、stderr 一律不能进知识条目
    assert not item["body"].endswith("…")
    assert "yum install" not in item["body"]
    assert "secret stdout" not in str(item)
    assert "期望 exit 1" in item["summary"]


def test_investigation_body_keeps_full_root_cause_and_solution():
    row = _investigation_row("e" * 36)
    marker = "完整命令必须保留到正文末尾"
    row["report"]["root_cause"] = "根因" + ("长" * 300) + marker
    row["report"]["solution"] = "初步方案" + ("步骤" * 500) + marker
    item = kb._summarize_investigation(row)
    assert item is not None
    assert len(item["body"]) > 1_000
    assert marker in item["body"]
    assert kb._summarize_deployment(_deploy_row("b" * 36, title="[eval] keepalived")) is None
    assert kb._summarize_deployment(_deploy_row("c" * 36, title="E2E 部署演练")) is None


def test_investigation_keeps_only_three_conclusions():
    item = kb._summarize_investigation(_investigation_row("d" * 36))
    assert item is not None
    assert item["kind"] == "investigation"
    assert "第四条" not in item["body"]
    assert item["body"].count("- ") == 3
    assert len(item["summary"]) <= kb.MAX_SUMMARY


def test_dedupe_collapses_exact_duplicates():
    """标题、摘要完全相同的六条记录只留最新一条。"""
    rows = [
        _deploy_row(f"{index}" * 36, updated=f"2026-11-17T0{9 - index}:00:00+00:00")
        for index in range(1, 7)
    ]
    entries = [item for item in (kb._summarize_deployment(row) for row in rows) if item]
    assert len({(item["_title_key"], item["_cluster_key"]) for item in entries}) == 1
    kept = kb.dedupe(entries)
    assert len(kept) == 1
    assert kept[0]["updated_at"].startswith("2026-11-17T08")
    assert kept[0]["deduped"] == 5


def test_dedupe_keeps_one_card_per_topic():
    """同主题但摘要不同的记录，默认只展示最新一条。"""
    title = "排查 vm-150 CPU 使用率"
    entries = [
        {
            "id": f"inv-{index}",
            "kind": "investigation",
            "title": title,
            "summary": f"第 {index} 次排查的独立结论",
            "updated_at": f"2026-11-17T0{9 - index}:00:00+00:00",
            "created_at": "",
            "tech_domain": "monitor",
            "tags": [],
            "_cluster_key": kb._cluster_key(title, f"第 {index} 次排查的独立结论"),
            "_title_key": kb._cluster_key(title),
        }
        for index in range(1, 7)
    ]
    kept = kb.dedupe(entries)
    assert len(kept) == kb.KEEP_PER_CLUSTER
    assert kept[0]["deduped"] == 5
    assert kept[0]["updated_at"].startswith("2026-11-17T08")


def test_dedupe_hard_cap_is_three():
    """需要保留多条时，硬上限仍然是 3 条。"""
    title = "排查 vm-150 CPU 使用率"
    entries = [
        {
            "id": f"inv-{index}",
            "kind": "investigation",
            "title": title,
            "summary": f"第 {index} 次排查的独立结论",
            "updated_at": f"2026-11-17T0{9 - index}:00:00+00:00",
            "created_at": "",
            "tech_domain": "monitor",
            "tags": [],
            "_cluster_key": kb._cluster_key(title, f"第 {index} 次排查的独立结论"),
            "_title_key": kb._cluster_key(title),
        }
        for index in range(1, 7)
    ]
    kept = kb.dedupe(entries, per_cluster=kb.MAX_PER_CLUSTER)
    assert len(kept) == kb.MAX_PER_CLUSTER
    assert kept[0]["deduped"] == 3


def test_dedupe_keeps_distinct_topics():
    left = kb._summarize_deployment(_deploy_row("e" * 36, title="部署 keepalived"))
    right = kb._summarize_deployment(_deploy_row(
        "f" * 36, title="MySQL 主从复制中断", reason="apply_failed",
    ))
    kept = kb.dedupe([left, right])
    assert len(kept) == 2


def test_dedupe_never_drops_manual_entries():
    manual = {
        "id": "manual-1", "kind": "manual", "kind_label": "手工知识",
        "title": "部署 keepalived", "summary": "手工记录", "body": "",
        "tech_domain": "middleware", "tags": [], "source": "manual",
        "source_task_id": "", "created_at": "", "updated_at": "", "_cluster_key": "",
    }
    auto = kb._summarize_deployment(_deploy_row("g" * 36))
    kept = kb.dedupe([manual, auto])
    assert {item["kind"] for item in kept} == {"manual", "deployment"}


def test_topic_key_normalizes_query_phrasing():
    """同一主机的同一对象，不同问法要落到同一个主题键。"""
    summary = "在 vm-150 的 Prometheus 负载指标范围内，负载处于低位"
    assert kb._topic_key("查看150的负载", summary) == "150负载"
    assert kb._topic_key("给我看看150机器的负载", summary) == "150负载"
    assert kb._topic_key("查一下150分负载情况", summary) == "150负载"
    assert kb._topic_key("查看150主机有哪些服务", summary) == "150服务"
    assert kb._topic_key("150 机器 CPU 使用率排查", "目标主机 10.0.0.150") == "150cpu使用率"
    assert kb._topic_key("查看150的负载", "没有主机信息") == ""


def test_dedupe_merges_same_topic_with_different_phrasing():
    """同主机同对象、问法不同的记录只留一条。"""
    summary = "在 vm-150 的 Prometheus 负载指标范围内，负载处于低位，未发现异常"
    entries = [
        {
            "id": f"inv-{index}", "kind": "investigation", "title": title,
            "summary": summary,
            "updated_at": f"2026-11-17T0{9 - index}:00:00+00:00", "created_at": "",
            "tech_domain": "monitor", "tags": ["no_anomaly"],
            "_cluster_key": kb._cluster_key(title, summary),
            "_title_key": kb._cluster_key(title),
            "_topic_key": kb._topic_key(title, summary),
        }
        for index, title in enumerate(
            ["查看150的负载", "给我看看150机器的负载", "查一下150分负载情况"], start=1,
        )
    ]
    kept = kb.dedupe(entries)
    assert len(kept) == 1
    assert kept[0]["deduped"] == 2


def test_dedupe_does_not_merge_different_objects():
    """同一主机但对象不同（CPU 和负载）不能合并。"""
    summary = "在 vm-150 的 Prometheus 指标范围内，未发现异常"
    entries = [
        {
            "id": "inv-cpu", "kind": "investigation", "title": "查看150的CPU使用率",
            "summary": summary, "updated_at": "2026-11-17T08:00:00+00:00",
            "created_at": "", "tech_domain": "monitor", "tags": ["no_anomaly"],
            "_cluster_key": kb._cluster_key("查看150的CPU使用率", summary),
            "_title_key": kb._cluster_key("查看150的CPU使用率"),
            "_topic_key": kb._topic_key("查看150的CPU使用率", summary),
        },
        {
            "id": "inv-load", "kind": "investigation", "title": "查看150的负载",
            "summary": summary, "updated_at": "2026-11-17T07:00:00+00:00",
            "created_at": "", "tech_domain": "monitor", "tags": ["no_anomaly"],
            "_cluster_key": kb._cluster_key("查看150的负载", summary),
            "_title_key": kb._cluster_key("查看150的负载"),
            "_topic_key": kb._topic_key("查看150的负载", summary),
        },
    ]
    kept = kb.dedupe(entries)
    assert len(kept) == 2


def test_dedupe_never_merges_different_verdicts():
    """标题相同、结论不同的两条记录不能合并。"""
    title = "看看150的机器是否部署nginx"
    entries = [
        {
            "id": "inv-a", "kind": "investigation", "title": title,
            "summary": "nginx 已安装但未运行",
            "updated_at": "2026-11-17T08:00:00+00:00", "created_at": "",
            "tech_domain": "middleware", "tags": ["anomaly"],
            "_cluster_key": kb._cluster_key(title, "nginx 已安装但未运行"),
            "_title_key": kb._cluster_key(title),
        },
        {
            "id": "inv-b", "kind": "investigation", "title": title,
            "summary": "只有 2 条真实证据，暂不能判断",
            "updated_at": "2026-11-17T07:00:00+00:00", "created_at": "",
            "tech_domain": "middleware", "tags": ["insufficient"],
            "_cluster_key": kb._cluster_key(title, "只有 2 条真实证据，暂不能判断"),
            "_title_key": kb._cluster_key(title),
        },
    ]
    kept = kb.dedupe(entries)
    assert len(kept) == 2


def test_ai_plan_title_includes_reason():
    """通用标题“AI 规划部署”补原因码，避免多张卡看起来一模一样。"""
    item = kb._summarize_deployment(_deploy_row(
        "h" * 36, title="AI 规划部署", reason="invalid_response",
    ))
    assert item is not None
    assert item["title"] == "AI 规划部署 · invalid_response"


def test_rag_context_has_readonly_prefix():
    docs = [{"kind_label": "部署经验", "tech_domain_label": "中间件",
             "updated_at": "2026-11-17", "title": "keepalived 部署",
             "summary": "验证失败", "body": "结果：失败"}]
    context = kb.build_rag_context(docs)
    assert context.startswith(kb.RAG_PREFIX)
    assert "不得执行" in context
    assert "keepalived 部署" in context


def test_infer_deploy_type_covers_all_eight():
    cases = {
        "kubectl get pods": "kubernetes",
        "docker ps 查看容器": "docker",
        "MySQL 主从同步": "database",
        "nginx 配置 reload": "middleware",
        "只装一个 rpm 包": "dependency",
        "firewall-cmd 放行 8080": "network",
        "SELinux 加固基线": "security",
        "Prometheus 告警规则": "monitor",
    }
    for text, expected in cases.items():
        assert taxonomy.infer_deploy_type(text) == expected, text


def test_infer_deploy_type_returns_empty_when_unclear():
    assert taxonomy.infer_deploy_type("随手记一条") == ""
    assert taxonomy.describe_deploy_type("") == "未分类"


def test_normalize_deploy_type_rejects_unknown():
    assert taxonomy.normalize_deploy_type("middleware") == "middleware"
    assert taxonomy.normalize_deploy_type("other") == ""
    assert taxonomy.normalize_deploy_type(None) == ""


def test_visible_tasks_appends_order_by_after_permission_clause(monkeypatch):
    """非 admin 的可见范围条件必须拼在 ORDER BY 之前，不能拼到 DESC 后面。"""
    captured: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return self

        def fetchall(self):
            return []

    monkeypatch.setattr(aggregate, "connect_app", lambda settings: FakeConnection())
    aggregate._visible_tasks(
        None, {"id": 2, "roles": ["oncall"]},
        "SELECT id FROM v3_tasks WHERE task_type = 'investigation'", [], 5,
    )
    sql = str(captured["sql"])
    assert "AND (submitted_by = %s OR assignee_id = %s)" in sql
    assert sql.index("AND (submitted_by") < sql.index("ORDER BY updated_at DESC")
    assert sql.endswith("ORDER BY updated_at DESC LIMIT %s")
