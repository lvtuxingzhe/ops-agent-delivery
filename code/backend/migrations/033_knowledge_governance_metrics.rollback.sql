-- 033 回滚：删除检索事件和判定次数字段。
BEGIN;

DROP TABLE IF EXISTS v3_knowledge_retrieval_events;
ALTER TABLE v3_knowledge_decisions DROP COLUMN IF EXISTS ai_runs;

COMMIT;
