-- 009_task_queue.sql
-- 任务队列：把「提交任务」与「执行任务」解耦（POST 返回 202，worker 领任务）。
--
-- 为什么用数据库队列而不是直接上 RabbitMQ：
--   1. 队列状态要和任务状态同库、可查、可审计；当前部署未引入 RabbitMQ，
--      先落 DB 队列能立刻支持「提交即返回、执行在后台」；
--   2. 领取用 `FOR UPDATE SKIP LOCKED`，多 worker 并发天然不抢同一条；
--   3. 迁移到 MQ 时只换 `claim_next()` 的实现，接口不变。
--
-- 唯一约束 (task_id, kind)：同一任务同一种执行只入队一次，重试走 attempts 字段。

BEGIN;

CREATE TABLE IF NOT EXISTS v3_task_queue (
    id bigserial PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES v3_tasks(id) ON DELETE CASCADE,
    kind text NOT NULL DEFAULT 'investigation'
        CHECK (kind IN ('investigation', 'deployment')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    locked_by text NOT NULL DEFAULT '',
    locked_at timestamptz,
    last_error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, kind)
);

-- 领取只走这个索引：status + 到期时间 + 自增 id（先来先做）
CREATE INDEX IF NOT EXISTS v3_task_queue_claim_idx
    ON v3_task_queue (status, available_at, id);

COMMIT;

-- 执行后自检（应看到一张表 + 一个索引 + 唯一约束）：
--   \d v3_task_queue
-- 授权（角色名是部署配置，不写进迁移）：
--   GRANT SELECT, INSERT, UPDATE, DELETE ON public.v3_task_queue TO ops_app;
--   GRANT USAGE, SELECT ON SEQUENCE public.v3_task_queue_id_seq TO ops_app;
