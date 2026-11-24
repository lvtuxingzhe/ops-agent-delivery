-- 028 回滚：删除结构化目标字段。
-- 回滚前先导出 v3_knowledge_entries 的 target_hosts / target_services。

ALTER TABLE v3_knowledge_entries
    DROP COLUMN IF EXISTS target_services;

ALTER TABLE v3_knowledge_entries
    DROP COLUMN IF EXISTS target_hosts;
