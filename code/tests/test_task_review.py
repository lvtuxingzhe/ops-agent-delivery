"""任务审阅的状态前置校验：不连库，用假连接覆盖驳回/取消两条路径。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services import task_review
from app.services.task_review import listing


class _FakeCtx:
    """最小上下文管理器：execute 返回一行状态，不碰真实数据库。"""

    def __init__(self, row):
        """row 是 fetchone 的返回值；None 表示任务不存在。"""
        self._row = row

    def __enter__(self):
        """进入 with 时返回自身，支持 conn.execute(...).fetchone()。"""
        return self

    def __exit__(self, *exc):
        """退出不吞异常，交给调用方。"""
        return False

    def execute(self, sql, params=None):
        """只记录调用，返回自身以支持链式 fetchone。"""
        return self

    def fetchone(self):
        """返回构造时给的状态行。"""
        return self._row


def test_require_state_accepts_awaiting_for_reject(monkeypatch):
    """驳回前置校验：待审批状态应放行，不能因结束时的 outcome 校验报错。"""
    monkeypatch.setattr(
        task_review, "connect_app", lambda settings: _FakeCtx(("awaiting_approval",))
    )
    task_review._require_state(None, "task-1", "awaiting_approval", "reject")


def test_require_state_rejects_other_state(monkeypatch):
    """已结束或执行中的任务不能再驳回。"""
    monkeypatch.setattr(
        task_review, "connect_app", lambda settings: _FakeCtx(("executing",))
    )
    with pytest.raises(task_review.ReviewError, match="cannot_reject:executing"):
        task_review._require_state(None, "task-1", "awaiting_approval", "reject")


def test_require_state_accepts_report_ready_for_execution(monkeypatch):
    """报告完成后可以发起处置，不是批准报告。"""
    monkeypatch.setattr(
        task_review, "connect_app", lambda settings: _FakeCtx(("report_ready",))
    )
    task_review._require_state(None, "task-1", "report_ready", "request_execution")


def test_require_state_rejects_execution_before_report(monkeypatch):
    """报告没完成不能发起处置。"""
    monkeypatch.setattr(
        task_review, "connect_app", lambda settings: _FakeCtx(("running",))
    )
    with pytest.raises(task_review.ReviewError, match="cannot_request_execution:running"):
        task_review._require_state(None, "task-1", "report_ready", "request_execution")


def test_require_state_reports_missing_task(monkeypatch):
    """查不到任务时给 task_not_found，而不是空指针。"""
    monkeypatch.setattr(
        task_review, "connect_app", lambda settings: _FakeCtx(None)
    )
    with pytest.raises(task_review.ReviewError, match="task_not_found"):
        task_review._require_state(None, "task-1", "awaiting_approval", "reject")


def test_normalize_disposition_rejects_empty_commands():
    """处置方案必须有至少一条命令，不能靠审批页再补。"""
    with pytest.raises(task_review.ReviewError, match="至少需要一条命令"):
        task_review._normalize_disposition_operations([])


def test_normalize_disposition_keeps_argv_and_scope():
    """只保留 argv/scope，补上默认 role/expect；不把整行 shell 传给执行器。"""
    normalized = task_review._normalize_disposition_operations([
        {"argv": ["systemctl", "status", "nginx"], "scope": "read"},
        {"argv": ["systemctl", "restart", "nginx"], "scope": "dangerous"},
    ])
    assert normalized[0]["argv"] == ["systemctl", "status", "nginx"]
    assert normalized[0]["scope"] == "read"
    assert normalized[1]["scope"] == "dangerous"


def test_normalize_disposition_rejects_bad_scope():
    """scope 只允许 read/dangerous，避免前端随便塞值绕过审批提示。"""
    with pytest.raises(task_review.ReviewError, match="scope"):
        task_review._normalize_disposition_operations([
            {"argv": ["uptime"], "scope": "write"},
        ])


@pytest.mark.parametrize(
    ("title", "task_type", "expected"),
    [
        ("vm-150 磁盘快满了，看看是什么在涨", "investigation", "host"),
        ("nginx 启动后 502，帮我查一下", "investigation", "middleware"),
        ("MySQL 主从延迟持续升高", "investigation", "database"),
        ("支付 Pod 一直重启，帮我定位原因", "investigation", "k8s"),
        ("docker 容器不断退出", "investigation", "docker"),
        ("一个没有关键词的任务", "investigation", "other"),
        ("部署 keepalived", "deployment", "deployment"),
    ],
)
def test_classify_task_category(title, task_type, expected):
    """列表类型是展示规则，必须稳定映射到主机/中间件/数据库/K8S/DOCKER。"""
    assert task_review.classify_task_category(title, task_type) == expected


class _ListCursor:
    """模拟列表查询的 fetchall。"""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _ListConn:
    """列表查询用的假连接：返回固定任务行。"""

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return _ListCursor(self.rows)


def _task_row(title: str, task_type: str = "investigation", submitted_by: int = 9):
    """构造与 _TASK_COLUMNS + 关联字段一致的 21 列任务行。"""
    now = datetime.now(UTC)
    return (
        "task-1", "trace-1", task_type, "closed", "succeeded", "done",
        title, submitted_by, now, None,
        None, None, None, "submitter", "提交人", None, None, None, None, None, None,
    )


def test_list_tasks_category_filter_uses_title_column(monkeypatch):
    """分类筛选必须按标题列归类，不能因为删掉 mode 后索引偏移筛空。"""
    rows = [
        _task_row("主机磁盘使用率高"),
        _task_row("Nginx 502 排查"),
        _task_row("部署 keepalived", task_type="deployment"),
    ]
    result = listing.list_tasks(
        None,
        category="host",
        viewer_roles={"admin"},
        viewer_id=1,
        _connect=lambda _settings: _ListConn(rows),
    )
    assert result["total"] == 1
    assert result["items"][0]["title"] == "主机磁盘使用率高"
    assert result["items"][0]["category"] == "host"



def test_parse_filter_time_rejects_bad_value():
    """时间筛选参数坏了给明确错误，不能让数据库报 500。"""
    with pytest.raises(task_review.ReviewError, match="created_after_invalid"):
        task_review._parse_filter_time("not-a-time", "created_after")


def test_parse_filter_time_accepts_iso_z():
    """前端 Date.toISOString() 带 Z，后端要能解析成同一时刻。"""
    parsed = task_review._parse_filter_time("2026-11-16T00:00:00.000Z", "created_after")
    assert parsed is not None
    assert parsed.startswith("2026-11-16T00:00:00")


class _TermCtx(_FakeCtx):
    """终止事务的最小假连接：支持 transaction() 和多次 execute。"""

    def transaction(self):
        """返回一个不吞异常的上下文管理器。"""
        return self


def _patch_terminate(monkeypatch, row, revoked=True):
    """把审计写库和队列标记换成桩，只验证终止的状态机与返回。"""
    monkeypatch.setattr(task_review, "connect_app", lambda settings: _TermCtx(row))
    monkeypatch.setattr(task_review, "insert_event", lambda conn, event: 1)
    monkeypatch.setattr(
        task_review.task_queue, "request_cancel", lambda conn, task_id, reason: revoked
    )


def test_terminate_task_closes_executing_and_revokes_lease(monkeypatch):
    """执行中终止：任务原子结束，队列返回已撤销。"""
    _patch_terminate(monkeypatch, ("executing", "trace-1"))
    result = task_review.terminate_task(
        None, task_id="task-1", actor_id=1, reason="磁盘异常",
    )
    assert result["status"] == "closed"
    assert result["outcome"] == "cancelled"
    assert result["lease_revoked"] is True


def test_terminate_task_rejects_non_executing(monkeypatch):
    """只有 executing 能终止；待审批应走撤回，不能复用终止。"""
    _patch_terminate(monkeypatch, ("awaiting_approval", "trace-1"))
    with pytest.raises(task_review.ReviewError, match="cannot_terminate:awaiting_approval"):
        task_review.terminate_task(None, task_id="task-1", actor_id=1)


def _patch_claim(monkeypatch, row):
    """认领只查前置行；卡在状态/占用校验时不需要后续 execute。"""
    monkeypatch.setattr(task_review, "connect_app", lambda settings: _TermCtx(row))
    monkeypatch.setattr(task_review, "insert_event", lambda conn, event: 1)


def test_claim_task_rejects_closed(monkeypatch):
    """已结束的排查不能再认领。"""
    _patch_claim(monkeypatch, ("investigation", "closed", "trace-1", None, None, 1))
    with pytest.raises(task_review.ReviewError, match="cannot_claim:closed"):
        task_review.claim_task(None, task_id="task-1", actor_id=1)


def test_claim_task_rejects_another_assignee(monkeypatch):
    """已被别人认领时返回 already_claimed，不覆盖处理人。"""
    _patch_claim(monkeypatch, ("investigation", "report_ready", "trace-1", 2, None, 1))
    with pytest.raises(task_review.ReviewError, match="already_claimed"):
        task_review.claim_task(None, task_id="task-1", actor_id=1)


def test_claim_task_rejects_invisible_task(monkeypatch):
    """服务层传入 viewer 时也要拒绝不可见任务，不能只靠路由。"""
    _patch_claim(monkeypatch, ("investigation", "report_ready", "trace-1", None, None, 2))
    with pytest.raises(task_review.ReviewError, match="task_not_found"):
        task_review.claim_task(
            None, task_id="task-1", actor_id=1,
            viewer={"id": 1, "roles": ["investigator"]},
        )


def test_claim_task_rejects_deployment_task(monkeypatch):
    """认领入口只服务排查单，部署任务有独立审批和转派流程。"""
    _patch_claim(monkeypatch, ("deployment", "report_ready", "trace-1", None, None, 1))
    with pytest.raises(task_review.ReviewError, match="task_not_claimable"):
        task_review.claim_task(None, task_id="task-1", actor_id=1)


def test_release_task_rejects_non_assignee(monkeypatch):
    """普通角色不能释放别人的认领。"""
    _patch_claim(monkeypatch, ("report_ready", "trace-1", 2, None))
    with pytest.raises(task_review.ReviewError, match="not_assignee"):
        task_review.release_task(None, task_id="task-1", actor_id=1, is_admin=False)


def test_release_task_admin_can_release(monkeypatch):
    """管理员可以释放别人的认领，并写一条 task.released。"""
    _patch_claim(monkeypatch, ("report_ready", "trace-1", 2, None))
    result = task_review.release_task(None, task_id="task-1", actor_id=1, is_admin=True)
    assert result["assignee"] is None

