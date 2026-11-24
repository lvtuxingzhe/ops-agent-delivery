"""任务队列的单元测试：不连库，用假连接只验证 SQL 形态与状态归类。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services import task_queue


class FakeConn:
    """记录执行过的 SQL，不真的连库。"""

    def __init__(self, row=None, rowcount=1, rows=None):
        """row 用来模拟 fetchone 的返回值，rows 模拟 fetchall。"""
        self.calls: list[tuple[str, tuple | None]] = []
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def execute(self, sql: str, params=None):
        """记一条 SQL；返回自己以支持链式 fetchone。"""
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        """模拟查询结果。"""
        return self._row

    def fetchall(self):
        """模拟批量查询结果。"""
        return self._rows


def test_enqueue_rejects_unknown_kind():
    """队列类型白名单之外直接拒绝。"""
    with pytest.raises(ValueError):
        task_queue.enqueue(FakeConn(), "task-1", "unknown")


def test_enqueue_uses_on_conflict():
    """入队用 ON CONFLICT，重复入队不报错。"""
    conn = FakeConn()
    task_queue.enqueue(conn, "task-1")
    sql, params = conn.calls[0]
    assert "ON CONFLICT" in sql
    assert params == ("task-1", "investigation")


def test_claim_next_returns_none_when_empty():
    """空队列返回 None，领取用 SKIP LOCKED。"""
    conn = FakeConn(row=None)
    assert task_queue.claim_next(conn, "worker-1") is None
    sql, params = conn.calls[0]
    assert "SKIP LOCKED" in sql
    assert "lease_expires_at" in sql
    assert params == ("worker-1", 90)


def test_claim_next_shapes_job():
    """领取结果字段形状固定。"""
    conn = FakeConn(row=(7, "11111111-1111-1111-1111-111111111111", "investigation", 2))
    job = task_queue.claim_next(conn, "worker-1")
    assert job == {"job_id": 7, "task_id": "11111111-1111-1111-1111-111111111111",
                   "kind": "investigation", "attempts": 2}


def test_claim_next_filters_kinds_for_dedicated_worker():
    """专用 worker 只领指定 kind，避免部署 exec 被排查积压饿死。"""
    conn = FakeConn(row=None)
    task_queue.claim_next(conn, "worker-exec", kinds=("exec",))
    sql, params = conn.calls[0]
    assert "kind = ANY(%s)" in sql
    assert params == ("worker-exec", 90, ["exec"])


def test_claim_next_without_kinds_keeps_fifo():
    """不传 kinds 时保持原来的全局 FIFO，不额外加过滤条件。"""
    conn = FakeConn(row=None)
    task_queue.claim_next(conn, "worker-1")
    sql, _ = conn.calls[0]
    assert "kind = ANY" not in sql


def test_mark_done_marks_done():
    """成功结束把队列行标 done。"""
    conn = FakeConn()
    task_queue.mark_done(conn, 7)
    sql, params = conn.calls[0]
    assert "status='done'" in sql and params == (7,)


def test_mark_retry_backs_off_before_max():
    """未到上限时回到 queued 并延后到未来。"""
    conn = FakeConn()
    outcome = task_queue.mark_retry(conn, 7, attempts=1, reason="RuntimeError")
    assert outcome == "queued"
    sql, params = conn.calls[0]
    assert "status='queued'" in sql
    # 退避时间在未来（第一档 10 秒）
    assert isinstance(params[0], datetime)
    assert (params[0] - datetime.now(UTC)).total_seconds() > 0


def test_mark_retry_gives_up_after_max():
    """到达上限标 failed，只记异常类型。"""
    conn = FakeConn()
    outcome = task_queue.mark_retry(conn, 7, attempts=3, reason="RuntimeError", max_attempts=3)
    assert outcome == "failed"
    sql, params = conn.calls[0]
    assert "status='failed'" in sql
    assert params == ("RuntimeError", 7)


def test_backoff_is_monotonic():
    """退避档位单调递增。"""
    assert list(task_queue.BACKOFF_SECONDS) == sorted(task_queue.BACKOFF_SECONDS)


def test_heartbeat_renews_lease_for_owner_only():
    """续租只更新仍由本 worker 持有的 running 行。"""
    conn = FakeConn()
    assert task_queue.heartbeat(conn, 7, "worker-1") is True
    sql, params = conn.calls[0]
    assert "lease_expires_at" in sql
    assert "locked_by = %s" in sql
    assert params == (90, 7, "worker-1")


def test_heartbeat_reports_lost_lease():
    """行已不属于本 worker 时返回 False，不假装续租成功。"""
    conn = FakeConn(rowcount=0)
    assert task_queue.heartbeat(conn, 7, "worker-1") is False


def test_request_cancel_only_targets_exec_rows():
    """终止标记只落在 exec 的 queued/running 行。"""
    conn = FakeConn(rowcount=1)
    assert task_queue.request_cancel(conn, "task-1", "磁盘故障") is True
    sql, params = conn.calls[0]
    assert "kind = 'exec'" in sql
    assert params == ("磁盘故障", "task-1")


def test_cancellation_requested_reads_latest_exec_row():
    """查询终止标记按最近一条 exec 行。"""
    conn = FakeConn(row=(True,))
    assert task_queue.cancellation_requested(conn, "task-1") is True
    sql, params = conn.calls[0]
    assert "ORDER BY id DESC" in sql
    assert params == ("task-1",)


def test_reclaim_stale_requeues_non_exec_rows():
    """排查/规划租约过期时回到队列，不直接判死。"""
    conn = FakeConn(rows=[(1, "task-1", "investigation", 1)])
    assert task_queue.reclaim_stale(conn) == 1
    sql, params = conn.calls[1]
    assert "status = 'queued'" in sql
    assert params == (task_queue.BACKOFF_SECONDS[0], 1)
    assert len(conn.calls) == 2


def test_reclaim_stale_fails_exec_rows_and_closes_task():
    """exec 可能已产生副作用，租约过期直接失败并关闭任务。"""
    conn = FakeConn(rows=[(2, "task-2", "exec", 1)])
    assert task_queue.reclaim_stale(conn) == 1
    sql, params = conn.calls[1]
    assert "status = 'failed'" in sql
    assert params == (2,)
    sql, params = conn.calls[2]
    assert "v3_tasks" in sql
    assert params[1] == ["task-2"]


def test_reclaim_stale_fails_rows_past_max_attempts():
    """非 exec 行超过最大尝试次数后同样失败并关闭任务。"""
    conn = FakeConn(rows=[(3, "task-3", "investigation", task_queue.MAX_ATTEMPTS)])
    assert task_queue.reclaim_stale(conn) == 1
    sql, params = conn.calls[1]
    assert "status = 'failed'" in sql
    sql, params = conn.calls[2]
    assert "v3_tasks" in sql
    assert params[1] == ["task-3"]

