"""
任务队列：把「提交」和「执行」解耦。

职责边界（刻意划得很窄）
------------------------
这个模块**只管队列本身**：入队、领取、成功结束、失败重试。
它不 import `investigation_service` —— 执行动作由调用方以 `runner` 传进来，
所以：
  * 不产生 `service ↔ queue` 的循环依赖；
  * 单元测试可以塞一个假 runner，不需要真跑 Agent；
  * 以后换 RabbitMQ，只换 `claim_next()` 内部实现，接口不动。

并发安全
--------
领取用 `FOR UPDATE SKIP LOCKED`：多个 worker 同时抢，不会拿到同一条，
也不会互相阻塞。`attempts` 在领取时就 +1，防止「领了没做完」被无限重放。

失败策略
--------
排查/规划任务异常 → 回到 `queued` 并按指数退避延后；超过 `max_attempts`
标 `failed`。`exec` 例外：命令可能已经产生部分副作用，异常后不自动重跑，
直接失败并结束任务。`last_error` 只记异常类型（不把内部细节写进队列表）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..core.config import Settings
from ..core.contract import CLOSED, EXECUTING, RUNNING
from ..core.db import connect_app

QUEUE_KINDS = (
    "investigation", "deployment", "exec", "deployment_plan", "knowledge_curation",
)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (10, 60, 300)
# 租约：领取时先给 90 秒，worker 运行期间每 20 秒续租。
# 为什么不是「任务多长租约就多长」：worker 被 kill 后必须能在有限时间内
# 被识别为失联；90 秒既不会误伤慢任务，也不会让卡死任务无限占行。
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20


def enqueue(conn, task_id: str, kind: str = "investigation") -> None:
    """
    入队。必须和「写任务」在**同一个事务**里调用 —— 否则可能出现
    「任务建了但没入队」这种永远不执行、也没人发现的孤儿任务。
    """
    if kind not in QUEUE_KINDS:
        raise ValueError(f"未知队列类型：{kind}")
    conn.execute(
        """
        INSERT INTO v3_task_queue (task_id, kind)
        VALUES (%s, %s)
        ON CONFLICT (task_id, kind) DO NOTHING
        """,
        (task_id, kind),
    )


def claim_next(conn, worker_id: str, *, lease_seconds: int = LEASE_SECONDS,
               kinds: tuple[str, ...] | None = None) -> dict | None:
    """领一条到期的任务；没有就返回 None。领取即写租约到期时间。

    `kinds` 用于专用 worker：部署 exec 不能被积压的排查任务饿死，
    传 `("exec",)` 时只领执行类队列行；不传则保持原有全局 FIFO 行为。
    """
    kind_clause = " AND kind = ANY(%s)" if kinds else ""
    params: tuple = (worker_id, int(lease_seconds))
    if kinds:
        params = (*params, list(kinds))
    row = conn.execute(
        f"""
        UPDATE v3_task_queue
           SET status = 'running', locked_by = %s, locked_at = now(),
               heartbeat_at = now(),
               lease_expires_at = now() + make_interval(secs => %s),
               attempts = attempts + 1, updated_at = now()
         WHERE id = (
             SELECT id FROM v3_task_queue
              WHERE status = 'queued' AND available_at <= now(){kind_clause}
              ORDER BY id
              LIMIT 1
              FOR UPDATE SKIP LOCKED
         )
         RETURNING id, task_id, kind, attempts
        """,
        params,
    ).fetchone()
    if not row:
        return None
    return {"job_id": row[0], "task_id": str(row[1]), "kind": row[2], "attempts": row[3]}


def heartbeat(conn, job_id: int, worker_id: str, *,
              lease_seconds: int = LEASE_SECONDS) -> bool:
    """续租：只有仍由本 worker 持有、且还在 running 的行才允许更新。"""
    cursor = conn.execute(
        """
        UPDATE v3_task_queue
           SET heartbeat_at = now(),
               lease_expires_at = now() + make_interval(secs => %s),
               updated_at = now()
         WHERE id = %s AND locked_by = %s AND status = 'running'
        """,
        (int(lease_seconds), job_id, worker_id),
    )
    return bool(getattr(cursor, "rowcount", 0))


def request_cancel(conn, task_id: str, reason: str = "") -> bool:
    """
    给执行中的队列行打终止标记；执行器轮询到后 kill 子进程。
    只改 queued/running 行，不把已经 done/failed 的历史改成取消。
    """
    cursor = conn.execute(
        """
        UPDATE v3_task_queue
           SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
               cancel_reason = %s,
               status = CASE WHEN status = 'queued' THEN 'failed' ELSE status END,
               last_error = CASE WHEN status = 'queued' THEN 'Cancelled' ELSE last_error END,
               updated_at = now()
         WHERE task_id = %s AND kind = 'exec' AND status IN ('queued', 'running')
        """,
        ((reason or "")[:200], task_id),
    )
    return bool(getattr(cursor, "rowcount", 0))


def cancellation_requested(conn, task_id: str) -> bool:
    """查询这条任务是否已有终止请求；没有队列行按未请求处理。"""
    row = conn.execute(
        """
        SELECT cancel_requested_at IS NOT NULL
          FROM v3_task_queue
         WHERE task_id = %s AND kind = 'exec'
         ORDER BY id DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return bool(row and row[0])


def reclaim_stale(conn) -> int:
    """
    回收租约已过期的 running 行。

    * `exec`：命令可能已产生部分副作用，重跑比停住更危险，直接失败并结束任务；
    * 其他类型：回队列并按尝试次数退避，超过 `MAX_ATTEMPTS` 才失败并结束任务。
      Worker 在排查/规划中途被 kill 时，任务不会直接判死，而是按队列重试。

    返回本次回收的队列行数；任务关闭只发生在队列行最终失败时。
    """
    rows = conn.execute(
        """
        SELECT id, task_id, kind, attempts
          FROM v3_task_queue
         WHERE status = 'running'
           AND lease_expires_at IS NOT NULL
           AND lease_expires_at < now()
         ORDER BY id
         FOR UPDATE
        """
    ).fetchall()
    if not rows:
        return 0

    failed_task_ids: set[str] = set()
    for job_id, task_id, kind, attempts in rows:
        attempt = max(1, int(attempts or 1))
        if kind == "exec" or attempt >= MAX_ATTEMPTS:
            conn.execute(
                """
                UPDATE v3_task_queue
                   SET status = 'failed', last_error = 'LeaseExpired',
                       locked_by = '', locked_at = NULL,
                       heartbeat_at = NULL, lease_expires_at = NULL, updated_at = now()
                 WHERE id = %s AND status = 'running'
                """,
                (int(job_id),),
            )
            failed_task_ids.add(str(task_id))
            continue
        delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
        conn.execute(
            """
            UPDATE v3_task_queue
               SET status = 'queued', available_at = now() + make_interval(secs => %s),
                   last_error = 'LeaseExpired',
                   locked_by = '', locked_at = NULL,
                   heartbeat_at = NULL, lease_expires_at = NULL, updated_at = now()
             WHERE id = %s AND status = 'running'
            """,
            (int(delay), int(job_id)),
        )

    if failed_task_ids:
        conn.execute(
            """
            UPDATE v3_tasks
               SET status = %s, outcome = 'failed', reason_code = 'lease_expired',
                   error = jsonb_build_object(
                       'code', 'lease_expired',
                       'message', '任务租约过期，执行进程可能已中断'
                   ),
                   closed_at = now(), updated_at = now()
             WHERE id = ANY(%s)
               AND status IN (%s, %s)
            """,
            (CLOSED, list(failed_task_ids), RUNNING, EXECUTING),
        )
    return len(rows)


def mark_done(conn, job_id: int, worker_id: str | None = None) -> bool:
    """标记成功。传入 worker_id 时必须仍由该 worker 持有 running 行。"""
    sql = "UPDATE v3_task_queue SET status='done', updated_at=now() WHERE id=%s"
    params: tuple = (job_id,)
    if worker_id:
        sql += " AND locked_by=%s AND status='running'"
        params = (job_id, worker_id)
    cursor = conn.execute(sql, params)
    return bool(getattr(cursor, "rowcount", 0))


def mark_retry(conn, job_id: int, *, attempts: int, reason: str,
               max_attempts: int = MAX_ATTEMPTS,
               worker_id: str | None = None) -> str:
    """
    失败结束。返回最终状态：'queued'（还会重试）或 'failed'（放弃）。
    退避时间按次数取 10s / 60s / 300s，超过就用最后一档。
    传 worker_id 时，只有仍由该 worker 持有的 running 行才允许改状态；
    否则返回 'lease_lost'，避免旧 worker 把已经 failed 的行重新入队。
    """
    owner_clause = ""
    owner_params: tuple = ()
    if worker_id:
        owner_clause = " AND locked_by=%s AND status='running'"
        owner_params = (worker_id,)
    if attempts >= max_attempts:
        cursor = conn.execute(
            "UPDATE v3_task_queue SET status='failed', last_error=%s, "
            "locked_by='', locked_at=NULL, heartbeat_at=NULL, "
            "lease_expires_at=NULL, updated_at=now() WHERE id=%s"
            + owner_clause,
            (reason, job_id) + owner_params,
        )
        return "failed" if getattr(cursor, "rowcount", 0) else "lease_lost"
    delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
    available = datetime.now(UTC) + timedelta(seconds=delay)
    cursor = conn.execute(
        """
        UPDATE v3_task_queue
           SET status='queued', available_at=%s, locked_by='', locked_at=NULL,
               heartbeat_at=NULL, lease_expires_at=NULL,
               last_error=%s, updated_at=now()
         WHERE id=%s
        """
        + owner_clause,
        (available, reason, job_id) + owner_params,
    )
    return "queued" if getattr(cursor, "rowcount", 0) else "lease_lost"


def _close_exec_task(settings: Settings, task_id: str, reason: str) -> None:
    """exec 失败后把仍停在 executing 的任务结束，避免页面一直显示执行中。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_tasks
                   SET status=%s, outcome='failed', reason_code='exec_failed',
                       error=jsonb_build_object('code', 'exec_failed', 'message', %s),
                       closed_at=now(), updated_at=now()
                 WHERE id=%s AND status IN (%s, %s)
                """,
                (CLOSED, reason[:300], task_id, RUNNING, EXECUTING),
            )


def process_next(settings: Settings, *, worker_id: str = "worker",
                 runner: Callable[..., dict] | dict[str, Callable[..., dict]],
                 max_attempts: int = MAX_ATTEMPTS,
                 kinds: tuple[str, ...] | None = None) -> dict:
    """
    领一条 → 跑 runner → 收尾。**不抛异常**：

        {"claimed": False}
        {"claimed": True, "job_id": n, "task_id": "...", "outcome": "done"|"queued"|"failed",
         "error": "<异常类型>"?}

    runner 可以是单个函数（全部 kind 走同一条路），也可以是
    `{kind: 函数}`：审批通过后的 `exec` 任务要走另一条执行器，
    不能和排查复用同一个入口。
    """
    with connect_app(settings) as conn:
        with conn.transaction():
            stale_reclaimed = reclaim_stale(conn)
            job = claim_next(conn, worker_id, kinds=kinds)
    if not job:
        if stale_reclaimed:
            return {"claimed": False, "stale_reclaimed": stale_reclaimed}
        return {"claimed": False}

    if isinstance(runner, dict):
        selected = runner.get(job["kind"])
        if selected is None:
            reason = "NoRunnerForKind"
            attempt_limit = 1 if job["kind"] == "exec" else max_attempts
            with connect_app(settings) as conn:
                with conn.transaction():
                    outcome = mark_retry(
                        conn, job["job_id"], attempts=job["attempts"],
                        reason=reason, max_attempts=attempt_limit, worker_id=worker_id,
                    )
            if outcome == "failed" and job["kind"] == "exec":
                _close_exec_task(settings, job["task_id"], reason)
            return {**job, "claimed": True, "outcome": outcome, "error": reason}
    else:
        selected = runner

    # exec 不做自动重试：命令可能已执行一部分，重跑会重复产生副作用。
    attempt_limit = 1 if job["kind"] == "exec" else max_attempts

    # 运行期间后台续租。runner 自己可能跑 5～20 分钟（执行命令 1200s 上限），
    # 不能靠 runner 内部记得续租；放在队列层统一做。
    heartbeat_stop = threading.Event()

    def _keep_lease() -> None:
        while not heartbeat_stop.wait(HEARTBEAT_SECONDS):
            try:
                with connect_app(settings) as conn:
                    with conn.transaction():
                        heartbeat(conn, job["job_id"], worker_id)
            except Exception:  # noqa: BLE001 - 续租失败不杀任务，下一轮再试
                # 连续续租失败时，租约到期后由 reclaim_stale 识别为失联。
                # 这里不打印异常内容，避免把连接串带进日志。
                continue

    lease_thread = threading.Thread(
        target=_keep_lease, name=f"lease-{job['job_id']}", daemon=True,
    )
    lease_thread.start()
    try:
        selected(settings, task_id=job["task_id"], trace_id=None)
        with connect_app(settings) as conn:
            with conn.transaction():
                done = mark_done(conn, job["job_id"], worker_id=worker_id)
        if not done:
            return {**job, "claimed": True, "outcome": "lease_lost",
                    "error": "lease_lost"}
        return {**job, "claimed": True, "outcome": "done"}
    except Exception as exc:  # noqa: BLE001 - 队列层兜住一切，交给重试策略
        reason = type(exc).__name__
        with connect_app(settings) as conn:
            with conn.transaction():
                outcome = mark_retry(
                    conn, job["job_id"], attempts=job["attempts"],
                    reason=reason, max_attempts=attempt_limit, worker_id=worker_id,
                )
        if outcome == "failed" and job["kind"] == "exec":
            # exec 不再重跑，队列失败的同时把任务结束；否则页面会一直
            # 显示“执行中”，而实际没有 worker 会再处理它。
            _close_exec_task(settings, job["task_id"], reason)
        return {**job, "claimed": True, "outcome": outcome, "error": reason}
    finally:
        heartbeat_stop.set()
        lease_thread.join(timeout=1)


def queue_stats(settings: Settings) -> dict:
    """队列水位：给健康检查或运维看「有没有积压」。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT status, count(*) FROM v3_task_queue GROUP BY status ORDER BY status"
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def requeue(conn, task_id: str, kind: str = "knowledge_curation") -> None:
    """把已经 done/failed 的队列行重新放回队列；用于管理员手动重判。

    只重置这一条 (task_id, kind)，不碰其他队列行；attempts 归零让重判
    有自己的重试预算。
    """
    if kind not in QUEUE_KINDS:
        raise ValueError(f"未知队列类型：{kind}")
    cursor = conn.execute(
        """
        UPDATE v3_task_queue
           SET status = 'queued', attempts = 0, available_at = now(),
               locked_by = '', locked_at = NULL, heartbeat_at = NULL,
               lease_expires_at = NULL, last_error = '', updated_at = now()
         WHERE task_id = %s AND kind = %s
        """,
        (task_id, kind),
    )
    if not getattr(cursor, "rowcount", 0):
        enqueue(conn, task_id, kind)


__all__ = [
    "BACKOFF_SECONDS", "HEARTBEAT_SECONDS", "LEASE_SECONDS", "MAX_ATTEMPTS",
    "QUEUE_KINDS", "cancellation_requested", "claim_next", "enqueue",
    "heartbeat", "mark_done", "mark_retry", "process_next", "queue_stats",
    "reclaim_stale", "requeue", "request_cancel",
]
