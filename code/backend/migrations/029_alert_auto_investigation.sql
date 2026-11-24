-- 029：告警转只读排查的状态字段。
--
-- 目的：
--   1. 跨 Prometheus 数据源按 group_key 去重；
--   2. 记录哪个排查任务由哪条告警创建；
--   3. 为波动/风暴判断保留首见时间与通知时间。
--
-- 回滚：
--   backend/migrations/029_alert_auto_investigation.rollback.sql

BEGIN;

ALTER TABLE v3_monitor_alert_state
    ADD COLUMN IF NOT EXISTS task_id uuid,
    ADD COLUMN IF NOT EXISTS group_key text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS labels jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS annotations jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS severity_level text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_notified_at timestamptz,
    ADD COLUMN IF NOT EXISTS suppressed_until timestamptz,
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'prometheus-pull';

COMMENT ON COLUMN v3_monitor_alert_state.task_id IS '由该告警自动创建的排查任务；历史告警为空';
COMMENT ON COLUMN v3_monitor_alert_state.group_key IS 'alertname + host 的稳定摘要，跨数据源去重';
COMMENT ON COLUMN v3_monitor_alert_state.labels IS 'Prometheus 原始标签快照';
COMMENT ON COLUMN v3_monitor_alert_state.annotations IS 'Prometheus 原始说明快照';
COMMENT ON COLUMN v3_monitor_alert_state.severity_level IS '平台级别 P1/P2/P3';
COMMENT ON COLUMN v3_monitor_alert_state.last_notified_at IS '最近一次成功写通知的时间';
COMMENT ON COLUMN v3_monitor_alert_state.suppressed_until IS '预留：抑制截止时间';
COMMENT ON COLUMN v3_monitor_alert_state.source IS '告警来源，本轮固定 prometheus-pull';

CREATE INDEX IF NOT EXISTS v3_monitor_alert_state_group_idx
    ON v3_monitor_alert_state (group_key, state, last_seen DESC);

CREATE INDEX IF NOT EXISTS v3_monitor_alert_state_first_seen_idx
    ON v3_monitor_alert_state (first_seen DESC);

COMMIT;
