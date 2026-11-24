-- 031 回滚：删除任务协作讨论表。
--
-- 评论是纯追加数据，回滚会丢失全部讨论；执行前先导出需要的记录。

BEGIN;

DROP TABLE IF EXISTS v3_task_comments;

COMMIT;
