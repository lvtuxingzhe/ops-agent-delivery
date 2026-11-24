-- 029 回滚：只删本次新增的列和索引，不删表、不删历史告警状态。

BEGIN;

DROP INDEX IF EXISTS v3_monitor_alert_state_group_idx;
DROP INDEX IF EXISTS v3_monitor_alert_state_first_seen_idx;

ALTER TABLE v3_monitor_alert_state
    DROP COLUMN IF EXISTS task_id,
    DROP COLUMN IF EXISTS group_key,
    DROP COLUMN IF EXISTS labels,
    DROP COLUMN IF EXISTS annotations,
    DROP COLUMN IF EXISTS severity_level,
    DROP COLUMN IF EXISTS last_notified_at,
    DROP COLUMN IF EXISTS suppressed_until,
    DROP COLUMN IF EXISTS source;

COMMIT;
