-- 028 知识条目结构化目标字段
--
-- 1) target_hosts / target_services 都是 TEXT[]，空数组表示未标注；
-- 2) 检索时优先精确匹配这两个字段，未标注时才回退正文包含匹配；
-- 3) 只做加列，不复制任务知识，不重建 embedding。
--
-- 回滚：删两列；新写入的标注数据会随列删除丢失，回滚前先导出。

ALTER TABLE v3_knowledge_entries
    ADD COLUMN IF NOT EXISTS target_hosts TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE v3_knowledge_entries
    ADD COLUMN IF NOT EXISTS target_services TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN v3_knowledge_entries.target_hosts IS
    '结构化目标主机；空数组表示未标注，检索时回退正文匹配';
COMMENT ON COLUMN v3_knowledge_entries.target_services IS
    '结构化目标服务；空数组表示未标注，检索时回退正文匹配';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON public.v3_knowledge_entries TO ops_app';
    END IF;
END
$$;
