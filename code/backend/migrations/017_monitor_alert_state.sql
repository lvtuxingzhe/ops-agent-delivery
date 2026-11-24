-- 017：Prometheus 告警状态，用于 firing → resolved 的恢复通知。
--
-- 为什么需要这张表
-- ----------------
-- Prometheus `/api/v1/alerts` 只返回当前活跃告警；告警恢复后会直接从列表消失。
-- 如果只拉当前列表，平台无法判断“之前 firing 的告警现在好了”，也就发不出恢复通知。
-- 这张表只存指纹和状态，不复制 annotations 正文，不存主机凭据。
--
-- 回滚
-- ----
--   DROP TABLE IF EXISTS v3_monitor_alert_state;

CREATE TABLE IF NOT EXISTS v3_monitor_alert_state (
    source_key  text NOT NULL,
    fingerprint text NOT NULL,
    alertname   text NOT NULL DEFAULT '',
    host        text NOT NULL DEFAULT '',
    severity    text NOT NULL DEFAULT '',
    state       text NOT NULL DEFAULT 'firing'
                CHECK (state IN ('firing', 'resolved')),
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    PRIMARY KEY (source_key, fingerprint)
);

COMMENT ON TABLE v3_monitor_alert_state IS 'Prometheus 告警指纹状态；只用于 firing/resolved 转换判断';
COMMENT ON COLUMN v3_monitor_alert_state.fingerprint IS '告警标签 + activeAt 生成，同一告警重新触发是新指纹';

CREATE INDEX IF NOT EXISTS v3_monitor_alert_state_open_idx
    ON v3_monitor_alert_state (source_key, state, last_seen DESC);
