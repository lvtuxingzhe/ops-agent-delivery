-- =============================================================================
-- 运维 Agent 平台 · 无哈希链审计层
--
-- 和旧审计记录的根本区别
-- ------------------
-- 旧 audit_events：prev_hash + hash 串成链，靠自写 SECURITY DEFINER 触发器
--   算哈希，再加三个守卫触发器挡 UPDATE/DELETE/TRUNCATE。是自研 WORM。
-- 新 v3_audit_events：**表里没有任何 hash 列，也没有任何自定义触发器**。
--   数据库层只做一件事：应用账号【只给 INSERT】，不给 UPDATE/DELETE/TRUNCATE/SELECT。
--   防篡改交给归档层的对象锁（S3 Object Lock 合规模式）+ 文件 SHA-256 清单。
--
-- 为什么权限就够了（反过度设计）
-- ------------------------------
-- 「审计写进去之后改不了」在数据库层用 GRANT 就能保证，不需要自己写链、
-- 不需要 advisory lock 串行化、不需要验证器。链带来的三个成本全消失：
-- 并发写要抢锁、验证工具要自己写、INSERT ... RETURNING 踩 SELECT 权限坑。
--
-- 过渡期：旧 audit_events 表与它的触发器【原封不动】。删表/删触发器是
-- 不可逆动作，需单独确认。旧版代码继续写旧审计记录，新链路写这里。
--
-- 授权：新表不会自动继承 init_db.py 的 GRANT，由 init_audit_v3.py 显式补。
-- =============================================================================

CREATE TABLE IF NOT EXISTS v3_audit_events (
    id            BIGSERIAL   PRIMARY KEY,
    trace_id      TEXT        NOT NULL,
    event_type    TEXT        NOT NULL,
    actor         TEXT        NOT NULL DEFAULT '',
    client_source TEXT        NOT NULL DEFAULT '',   -- web / desktop / api
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS idx_v3_audit_trace ON v3_audit_events (trace_id, id);
CREATE INDEX IF NOT EXISTS idx_v3_audit_created ON v3_audit_events (created_at);

COMMENT ON TABLE v3_audit_events IS
    '只追加审计流水。无哈希链、无自定义触发器；防篡改由归档对象锁 + 清单保证。应用账号只有 INSERT';
COMMENT ON COLUMN v3_audit_events.payload IS
    '只放事件内容，绝不许放密码/令牌/密钥（调用方负责脱敏）';

-- 排查侧降级用的本地缓冲表：审计暂时写不进去时先落这里，事后补写。
-- 它【不是】审计记录，只是待投递队列；补写成功后删除对应行。
CREATE TABLE IF NOT EXISTS v3_audit_buffer (
    id          BIGSERIAL   PRIMARY KEY,
    trace_id    TEXT        NOT NULL,
    event_type  TEXT        NOT NULL,
    actor       TEXT        NOT NULL DEFAULT '',
    client_source TEXT      NOT NULL DEFAULT '',
    payload     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE v3_audit_buffer IS
    '排查侧降级缓冲：审计写不进时先存这里，flush 后删除。部署场景不走缓冲（强一致）';
