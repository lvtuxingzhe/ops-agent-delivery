-- =============================================================================
-- 运维 Agent 平台 · v1.1 改造第 ① 步：数据契约层（五态 + 4 张表）
--
-- 对应《运维Agent平台-落地架构设计说明书》第 5 章（已采纳方案）。
--
-- 为什么用 v3_ 前缀（过渡期设计，不是长期命名）
-- --------------------------------------------
-- 旧 ①～⑤ 步已验收的代码还在用那 20 张表（tasks / reports / audit_events ...），
-- 直接改名或删表会把已通过的实测全打断，属于不可逆动作。
-- 所以过渡期【新旧并存】：本文件新建 4 张 v3_ 表，迁移脚本把旧数据映射进来，
-- 后面的步骤逐个把读写切到 v3 表。等全部切换完成、玉龙单独确认后，
-- 再归档旧表并把 v3_ 前缀去掉。删旧表这一步本文件【不做】。
--
-- 四条设计取舍（反过度设计）
-- --------------------------
-- 1. 不加外键：任务历史不该因为删个用户被级联删掉，迁移也不该被插入顺序绑死；
--    引用完整性由应用层保证。
-- 2. 方案 / 证据 / 报告 / 确认信息全折进 tasks 的 JSONB：它们生命周期跟着任务走，
--    单独建表只带来 JOIN 和状态不一致。
-- 3. 状态只有五个，成败/驳回/取消放 outcome，不各占一个状态。
-- 4. no_data / unknown 是「数据质量标签」，不是状态，不进 CHECK。
--    铁律：无数据要能如实显示为「无数据」，不能显示成「正常」。
--
-- 授权提醒：新建表不会自动继承 init_db.py 的 GRANT（那条只覆盖当时已存在的表），
-- 必须由 scripts/init_contract.py 显式给 ops_app 授权，否则接口报 permission denied。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 表 1：用户（谁在用）。密码哈希 / TOTP / 短信码不落这里（见方案 5.2）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS v3_users (
    id            BIGSERIAL   PRIMARY KEY,
    username      TEXT        NOT NULL UNIQUE,
    display_name  TEXT        NOT NULL DEFAULT '',
    roles         TEXT[]      NOT NULL DEFAULT '{}',  -- 六角色是枚举，不是关系模型
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    mfa_enabled   BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE v3_users IS '4 张表之一。凭据在 OpenBao / 认证服务，不进业务库';

-- -----------------------------------------------------------------------------
-- 表 2：任务（一个任务的全生命周期）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS v3_tasks (
    id             UUID        PRIMARY KEY,
    trace_id       TEXT        NOT NULL,
    task_type      TEXT        NOT NULL CHECK (task_type IN ('investigation','deployment')),
    -- 五态：draft(已提交) → running(进行中) → awaiting_approval(待人确认)
    --       → executing(执行中，仅部署) → closed(终态)
    status         TEXT        NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','running','awaiting_approval','executing','closed')),
    -- 结果不是状态：只有 closed 时才有值
    outcome        TEXT        CHECK (outcome IN ('succeeded','failed','rejected','cancelled','partial','adopted')),
    reason_code    TEXT        NOT NULL DEFAULT '',   -- 为什么失败/驳回，只用于统计，不参与流转
    stage          TEXT        NOT NULL DEFAULT 'investigation',  -- 走到哪一段，用于列表筛选
    title          TEXT        NOT NULL DEFAULT '',
    description    TEXT        NOT NULL DEFAULT '',
    target_scope   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    submitted_by   BIGINT,
    confirmed_by   BIGINT,
    mode           TEXT        NOT NULL DEFAULT 'dry-run',
    plan           JSONB       NOT NULL DEFAULT '{}'::jsonb,       -- 方案正文 + fingerprint
    evidences      JSONB       NOT NULL DEFAULT '[]'::jsonb,       -- 每条带 id/observed_at/target/source/data_status
    report         JSONB       NOT NULL DEFAULT '{}'::jsonb,       -- 每条结论带 evidence_ids
    approval       JSONB       NOT NULL DEFAULT '{}'::jsonb,       -- 令牌哈希/二次认证/自审批标记
    redaction_map  JSONB       NOT NULL DEFAULT '{}'::jsonb,       -- 脱敏映射，本地留存永不出网
    error          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at      TIMESTAMPTZ,
    -- 终态必须有结果；非终态不许提前写结果（避免"还在跑就标失败"）
    CONSTRAINT ck_v3_tasks_outcome CHECK (
        (status = 'closed' AND outcome IS NOT NULL)
        OR (status <> 'closed' AND outcome IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_v3_tasks_status_created ON v3_tasks (status, created_at);
CREATE INDEX IF NOT EXISTS idx_v3_tasks_trace ON v3_tasks (trace_id);
COMMENT ON TABLE v3_tasks IS '4 张表之一。五态 + outcome，方案/证据/报告/确认全折在 JSONB';
COMMENT ON COLUMN v3_tasks.status IS '五态之一；成败驳回取消看 outcome，不再各占状态';
COMMENT ON COLUMN v3_tasks.evidences IS 'data_status 的 no_data 是数据质量标签，必须显示为「无数据」不是「正常」';

-- -----------------------------------------------------------------------------
-- 表 3：配置（所有可变配置一张 KV 表）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS v3_configs (
    namespace       TEXT        NOT NULL,  -- datasource / llm / setting / network / template
    key             TEXT        NOT NULL,
    value           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    credential_ref  TEXT        NOT NULL DEFAULT '',  -- 只存引用名，指向 OpenBao 路径
    enabled         BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_by      BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_v3_configs_ns ON v3_configs (namespace);
COMMENT ON TABLE v3_configs IS '4 张表之一。数据源/模型/网段/开关/模板全在这，改配置不发版';
COMMENT ON COLUMN v3_configs.credential_ref IS '引用编号，不是密钥；真实值运行期由 OpenBao 注入';

-- -----------------------------------------------------------------------------
-- 表 4：知识（经验与 Skill 合一）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS v3_knowledge (
    id              BIGSERIAL   PRIMARY KEY,
    kind            TEXT        NOT NULL CHECK (kind IN ('experience','skill')),
    title           TEXT        NOT NULL,
    content         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    source_task_id  UUID,
    version         INTEGER     NOT NULL DEFAULT 1,
    status          TEXT        NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','running','awaiting_approval','executing','closed')),
    outcome         TEXT,
    reviewed_by     BIGINT,
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, title, version)
);
CREATE INDEX IF NOT EXISTS idx_v3_knowledge_kind_status ON v3_knowledge (kind, status);
COMMENT ON TABLE v3_knowledge IS '4 张表之一。经验与 Skill 都是「内容+版本+审核」，没审过不进检索';

-- pgvector 列：本地镜像可能没装 vector 扩展，用容错写法，缺了不挡契约层落地。
-- 第 ⑧ 步知识库真正用向量时再确认扩展已装。
DO $$
BEGIN
    ALTER TABLE v3_knowledge ADD COLUMN IF NOT EXISTS embedding vector;
    RAISE NOTICE 'v3_knowledge.embedding 已就绪（第 ⑧ 步用）';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'vector 扩展暂不可用，embedding 列跳过，不影响第 ① 步契约层：%', SQLERRM;
END
$$;
