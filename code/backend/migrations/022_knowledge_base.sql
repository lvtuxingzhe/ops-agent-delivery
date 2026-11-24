-- 022 独立知识库 + 部署类型
--
-- 1) v3_knowledge_entries：只存“手工知识”。部署经验和排查报告仍留在
--    v3_tasks 里按需聚合，不复制第二份，避免任务更新后知识库变成旧快照。
-- 2) v3_tasks.deploy_type：部署类型只做分类筛选，不参与命令和 plan_hash。
--    历史任务留空，读取时按标题/模板/命令推断，推不出就显示“未分类”。
--
-- 回滚：删表 + 删列即可。两处都不被执行链依赖。

CREATE TABLE IF NOT EXISTS v3_knowledge_entries (
    id           UUID        PRIMARY KEY,
    title        TEXT        NOT NULL,
    summary      TEXT        NOT NULL DEFAULT '',
    body         TEXT        NOT NULL DEFAULT '',
    tech_domain  TEXT        NOT NULL DEFAULT 'other',
    tags         TEXT[]      NOT NULL DEFAULT '{}',
    created_by   BIGINT,
    updated_by   BIGINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 软删除：知识库可删，但审计和误删恢复仍要能看见原记录。
    deleted_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_v3_knowledge_updated
    ON v3_knowledge_entries (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_knowledge_domain
    ON v3_knowledge_entries (tech_domain);

COMMENT ON TABLE v3_knowledge_entries IS
    '手工知识条目；部署/排查条目从 v3_tasks 动态聚合，不在这里存副本';
COMMENT ON COLUMN v3_knowledge_entries.deleted_at IS
    '软删除时间；非空即不再出现在列表，但保留审计痕迹';

ALTER TABLE v3_tasks
    ADD COLUMN IF NOT EXISTS deploy_type TEXT NOT NULL DEFAULT '';

ALTER TABLE v3_tasks
    DROP CONSTRAINT IF EXISTS ck_v3_tasks_deploy_type;
ALTER TABLE v3_tasks
    ADD CONSTRAINT ck_v3_tasks_deploy_type
    CHECK (deploy_type IN (
        '', 'middleware', 'database', 'kubernetes', 'docker',
        'dependency', 'network', 'security', 'monitor'
    ));

CREATE INDEX IF NOT EXISTS idx_v3_tasks_deploy_type
    ON v3_tasks (deploy_type)
    WHERE task_type = 'deployment';

COMMENT ON COLUMN v3_tasks.deploy_type IS
    '部署类型（8 类固定枚举）；空表示历史任务未分类，读取时按内容推断';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.v3_knowledge_entries TO ops_app';
    END IF;
END
$$;
