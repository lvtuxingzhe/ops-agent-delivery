-- =============================================================================
-- 运维 Agent 平台 · 核心表 + 审计记录（只追加、带哈希链）
--
-- 这个文件负责「建表 / 建索引 / 建触发器」。
-- 角色名和密码都是配置项，不在 SQL 里写死；
-- 由 backend/scripts/init_db.py 从环境变量读取，并用 psycopg.sql 安全拼装。
--
-- 设计上的三条约束
--   1. 审计记录只追加：audit_events 只有 INSERT 权限（见 init_db.py 授权），
--      数据库触发器让 UPDATE / DELETE / TRUNCATE 直接报错；
--      每条记录带 prev_hash + hash，前后串成链，事后能发现被改动。
--   2. 审计写入、状态推进、执行许可放在【同一个事务】里；写入失败时不执行。
--      逻辑封装在 backend/app/core/audit.py。
--   3. 命令返回 0 不等于业务成功：task_steps 同时记录 exit_code 和
--      business_check_passed，两者都满足才算完成。
--
-- 表里部分字段为后续功能预留，注释中已标注。
--   提前保留字段，后续加功能不必改表结构，也不必迁移历史数据。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 扩展
-- -----------------------------------------------------------------------------
-- pgcrypto 提供 digest()，审计哈希链要用。没有它整个审计记录表就串不起来，必须成功。
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- pgvector 供知识库向量检索使用，当前版本先建扩展、不写入向量。
-- 用容错写法：某些内网 PG 镜像没带这个扩展时只提示、不挡启动。
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
    RAISE NOTICE 'pgvector 扩展已就绪';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector 扩展暂不可用，不影响其他迁移：%', SQLERRM;
END
$$;

-- -----------------------------------------------------------------------------
-- 用户与角色（第 ③ 步接真实认证）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    id                SMALLSERIAL PRIMARY KEY,
    code              TEXT        NOT NULL UNIQUE,
    name              TEXT        NOT NULL,
    -- 定稿：管理员可自审批，其他角色强制「提交人 ≠ 确认人」。
    -- 做成字段而不是写死在代码里，是为了第 ③ 步「豁免可一键关掉」能生效。
    can_self_approve  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE roles IS '六个角色；can_self_approve 是管理员例外的可配置开关（第③步）';

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL   PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE,
    display_name  TEXT        NOT NULL DEFAULT '',
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE users IS '账号主表。【刻意不存密码】第③步会建独立的认证表，独立库权限，应用主账号查不到';

CREATE TABLE IF NOT EXISTS user_roles (
    user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id  SMALLINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- -----------------------------------------------------------------------------
-- 任务与步骤
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id            UUID        PRIMARY KEY,
    -- trace_id 把「一次任务从头到尾所有审计事件」串起来，完成标志要求按它查得到。
    trace_id      TEXT        NOT NULL,
    title         TEXT        NOT NULL,
    task_type     TEXT        NOT NULL CHECK (task_type IN ('investigation', 'deployment')),
    status        TEXT        NOT NULL DEFAULT 'created'
                  CHECK (status IN ('created','running','waiting_confirm','approved',
                                    'rejected','done','failed','cancelled')),
    -- 历史上的任务模式字段；036 迁移会删除。
    -- 新环境按顺序执行迁移，最终表结构不含此列。
    mode          TEXT        NOT NULL DEFAULT 'dry-run',
    submitted_by  BIGINT      REFERENCES users(id),
    confirmed_by  BIGINT      REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tasks_trace_id ON tasks(trace_id);
COMMENT ON COLUMN tasks.status IS '状态推进必须和审计写入、执行许可在同一个事务里（见 core/audit.py）';

CREATE TABLE IF NOT EXISTS task_steps (
    id                      BIGSERIAL   PRIMARY KEY,
    task_id                 UUID        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_no                 INTEGER     NOT NULL,
    kind                    TEXT        NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'pending',
    -- 命令返回 0 不算成功：两个字段都要有，业务检查未通过不标记 done
    exit_code               INTEGER,
    business_check_passed   BOOLEAN,
    payload                 JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, step_no)
);

-- -----------------------------------------------------------------------------
-- 方案 / 确认令牌 / 白名单 / 执行许可（第 ⑥ 步闸口用）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plans (
    id           BIGSERIAL   PRIMARY KEY,
    task_id      UUID        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version      INTEGER     NOT NULL DEFAULT 1,
    -- 方案指纹：确认的是「哪一版方案」。方案或目标环境一变，令牌立即失效（第⑥步）。
    fingerprint  TEXT        NOT NULL,
    content      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS confirmation_tokens (
    id               BIGSERIAL   PRIMARY KEY,
    task_id          UUID        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    plan_id          BIGINT      NOT NULL REFERENCES plans(id),
    -- 只存令牌的哈希，不存明文。一次性：used_at 有值就作废。
    token_hash       TEXT        NOT NULL UNIQUE,
    -- 二次认证方式：登录通过不等于本次变更授权
    step_up_method   TEXT,
    -- 管理员的每次自审批都打标、可检索、可导出成「自审批清单」
    is_self_approval BOOLEAN     NOT NULL DEFAULT FALSE,
    expires_at       TIMESTAMPTZ NOT NULL,
    used_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tokens_self_approval ON confirmation_tokens(is_self_approval, created_at);

CREATE TABLE IF NOT EXISTS whitelists (
    id            BIGSERIAL   PRIMARY KEY,
    scope         TEXT        NOT NULL,
    registered_by BIGINT      REFERENCES users(id),
    approved_by   BIGINT      REFERENCES users(id),
    valid_until   TIMESTAMPTZ,
    enabled       BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE whitelists IS '白名单「自动停」免的是每次点确认，不是免审核；登记人和审批人分离（第⑥步）';

CREATE TABLE IF NOT EXISTS execution_permits (
    id                BIGSERIAL   PRIMARY KEY,
    task_id           UUID        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    plan_fingerprint  TEXT        NOT NULL,
    permit_hash       TEXT        NOT NULL,
    issued_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_permits_task ON execution_permits(task_id, issued_at);
COMMENT ON TABLE execution_permits IS '执行许可：和审计事件、任务状态推进同事务产生；审计写失败时本表不产生记录';

-- -----------------------------------------------------------------------------
-- 报告与证据
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reports (
    id         BIGSERIAL   PRIMARY KEY,
    task_id    UUID        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status     TEXT        NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft','adopted','rejected')),
    content    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidences (
    id             BIGSERIAL   PRIMARY KEY,
    report_id      BIGINT      NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    -- 报告里每条结论都要挂证据，这个字段指向那条结论
    conclusion_ref TEXT        NOT NULL DEFAULT '',
    source         TEXT        NOT NULL,          -- prometheus / elk / k8s / docker
    -- 显示规则：「无数据」显示为「无数据」，不显示为「正常」
    data_status    TEXT        NOT NULL DEFAULT 'ok'
                   CHECK (data_status IN ('ok','no_data','unknown')),
    payload        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evidences_report ON evidences(report_id);

-- -----------------------------------------------------------------------------
-- 数据源配置（第 ④ 步配置页）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasource_configs (
    id                BIGSERIAL   PRIMARY KEY,
    name              TEXT        NOT NULL UNIQUE,
    kind              TEXT        NOT NULL,       -- prometheus / elk / k8s / docker / bluelking_a / bluelking_b / obsidian / feishu / siyuan
    adapter           TEXT        NOT NULL DEFAULT 'self',  -- self（自研适配器）/ mcp（插件层）
    -- 地址、端口、路径、网段、Obsidian 目录全在这里，代码里一个都不写死
    endpoint          TEXT        NOT NULL DEFAULT '',
    -- 只存「凭据在哪把钥匙里」的编号，绝不存密码明文；页面也不回显
    credential_ref    TEXT        NOT NULL DEFAULT '',
    readonly          BOOLEAN     NOT NULL DEFAULT TRUE,
    enabled           BOOLEAN     NOT NULL DEFAULT FALSE,
    health_checked_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN datasource_configs.credential_ref IS '凭据引用编号，不是密码；真实值由密钥管理在调用时临时注入';

-- -----------------------------------------------------------------------------
-- Skill（第 ⑧ 步最小闭环）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
    id           BIGSERIAL   PRIMARY KEY,
    name         TEXT        NOT NULL,
    version      INTEGER     NOT NULL DEFAULT 1,
    -- 未审核的 Skill 不进入可调用状态，版本只增不改
    status       TEXT        NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft','approved','enabled','disabled')),
    reviewed_by  BIGINT      REFERENCES users(id),
    package_ref  TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

-- =============================================================================
-- 审计记录：只追加、带哈希链
-- =============================================================================
CREATE TABLE IF NOT EXISTS audit_events (
    id            BIGSERIAL   PRIMARY KEY,
    trace_id      TEXT        NOT NULL,
    event_type    TEXT        NOT NULL,
    actor         TEXT        NOT NULL DEFAULT '',
    actor_role    TEXT        NOT NULL DEFAULT '',
    -- 来自网页还是桌面，由后台判定，不信任前端自报
    client_source TEXT        NOT NULL DEFAULT '',
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- 下面两个字段【由触发器强制计算】，应用传入值会被覆盖，事后不可修改
    prev_hash     TEXT        NOT NULL DEFAULT repeat('0', 64),
    hash          TEXT        NOT NULL DEFAULT repeat('0', 64)
);
CREATE INDEX IF NOT EXISTS idx_audit_trace_id ON audit_events(trace_id, id);
COMMENT ON TABLE audit_events IS '不可删改的审计记录。应用账号只有 INSERT 权限，哈希链由 SECURITY DEFINER 触发器维护';
COMMENT ON COLUMN audit_events.hash IS 'sha256(规范化字段 + prev_hash)，由触发器计算，应用无法伪造';

-- 计算哈希链的触发器函数。
-- 为什么要 SECURITY DEFINER：应用账号对 audit_events 只有 INSERT、没有 SELECT，
--   而算链必须读上一条的 hash。让函数以创建者（管理账号）身份执行才能读到，
--   同时用 SET search_path 固定搜索路径，避免被调用者用同名对象劫持。
CREATE OR REPLACE FUNCTION audit_chain_before_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    last_hash TEXT;
    canonical TEXT;
BEGIN
    -- 串行化审计写入：不加这把锁，两个并发事务会读到同一个 prev_hash，
    -- 链就分叉了，事后也验不出来。事务级锁，提交后自动释放。
    PERFORM pg_advisory_xact_lock(918273645);

    SELECT hash INTO last_hash
      FROM public.audit_events
     ORDER BY id DESC
     LIMIT 1;

    -- 无条件覆盖：哪怕应用故意传了 hash / prev_hash，也一律以数据库算的为准
    NEW.prev_hash := COALESCE(last_hash, repeat('0', 64));
    NEW.created_at := COALESCE(NEW.created_at, clock_timestamp());

    canonical := concat_ws('|',
        NEW.trace_id,
        NEW.event_type,
        NEW.actor,
        NEW.actor_role,
        NEW.client_source,
        NEW.payload::text,
        to_char(NEW.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        NEW.prev_hash
    );
    NEW.hash := encode(digest(canonical, 'sha256'), 'hex');
    RETURN NEW;
END;
$$;

-- 每个触发器前先 DROP，让整个 migration 可以重复执行（初始化脚本可能跑多次）。
DROP TRIGGER IF EXISTS trg_audit_chain ON audit_events;
CREATE TRIGGER trg_audit_chain
BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_chain_before_insert();

-- 防篡改双保险：即使误授权，数据库这一层也阻止修改。
-- TRUNCATE 是表级操作，使用语句级触发器。
CREATE OR REPLACE FUNCTION audit_chain_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '审计记录不可修改或删除（audit_events 只允许追加）';
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_events;
CREATE TRIGGER trg_audit_no_update
BEFORE UPDATE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_chain_guard();

DROP TRIGGER IF EXISTS trg_audit_no_delete ON audit_events;
CREATE TRIGGER trg_audit_no_delete
BEFORE DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_chain_guard();

DROP TRIGGER IF EXISTS trg_audit_no_truncate ON audit_events;
CREATE TRIGGER trg_audit_no_truncate
BEFORE TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION audit_chain_guard();
