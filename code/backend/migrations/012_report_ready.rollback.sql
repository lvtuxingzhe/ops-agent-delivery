-- 012 回滚：合并回「报告完成即待审批」的旧语义。
-- 只用于紧急回退；执行前确认没有报告已经发起过处置（那些任务已是 awaiting_approval）。

DO $$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'v3_tasks'::regclass
           AND contype = 'c'
           AND pg_get_constraintdef(oid) LIKE '%report_ready%'
    LOOP
        EXECUTE format('ALTER TABLE v3_tasks DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE v3_tasks
    ADD CONSTRAINT v3_tasks_status_check
    CHECK (status IN ('draft','running','awaiting_approval','executing','closed'));

UPDATE v3_tasks
   SET status = 'awaiting_approval',
       updated_at = now()
 WHERE status = 'report_ready';
