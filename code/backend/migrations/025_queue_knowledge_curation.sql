-- 025 队列 kind 增加 knowledge_curation
-- 排查报告 / 部署任务结束后异步判知识准入，复用同一张队列表。

ALTER TABLE v3_task_queue DROP CONSTRAINT IF EXISTS v3_task_queue_kind_check;
ALTER TABLE v3_task_queue
    ADD CONSTRAINT v3_task_queue_kind_check
    CHECK (kind IN ('investigation', 'deployment', 'exec', 'deployment_plan', 'knowledge_curation'));

-- 024 建表时漏了应用角色授权；这里补上，避免治理接口 500。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.v3_knowledge_decisions TO ops_app';
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.v3_knowledge_decisions_id_seq TO ops_app';
    END IF;
END
$$;
