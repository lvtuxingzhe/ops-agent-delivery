-- 012：把「报告完成」和「待执行审批」拆成两个状态。
--
-- 旧模型里排查报告一生成就进 awaiting_approval，导致报告看起来也在等审批。
-- 新模型：running → report_ready（报告已完成，不催审批）
--         report_ready → awaiting_approval（用户明确发起处置后，才审批命令）
--
-- 这是一次可重复执行的迁移：
--   1. 找到 v3_tasks.status 上包含 awaiting_approval 的 CHECK 并删掉；
--   2. 用包含 report_ready 的新 CHECK 代替；
--   3. 把历史上已有报告、还没有执行许可的 awaiting_approval 任务迁到 report_ready。
--
-- 注意：只改 v3_tasks，不动 v3_knowledge；知识条目没有「报告完成」语义。

DO $$
DECLARE
    c record;
BEGIN
    FOR c IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'v3_tasks'::regclass
           AND contype = 'c'
           AND pg_get_constraintdef(oid) LIKE '%awaiting_approval%'
    LOOP
        EXECUTE format('ALTER TABLE v3_tasks DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE v3_tasks
    ADD CONSTRAINT v3_tasks_status_check
    CHECK (status IN ('draft','running','report_ready','awaiting_approval','executing','closed'));

-- 已有报告、尚未进入执行的任务，按新语义归到「报告已完成」。
-- 旧库没有 execution_requested 标记，所有旧的 awaiting_approval 都是报告完成态。
UPDATE v3_tasks
   SET status = 'report_ready',
       updated_at = now()
 WHERE status = 'awaiting_approval'
   AND report IS NOT NULL
   AND report <> '{}'::jsonb;

COMMENT ON COLUMN v3_tasks.status IS
    '状态：draft / running / report_ready / awaiting_approval / executing / closed；报告完成不再等于待审批';
