-- 032 回滚：删除已读游标与评论增强字段。
BEGIN;

DROP TABLE IF EXISTS v3_task_comment_reads;
DROP INDEX IF EXISTS v3_task_comments_live_idx;
ALTER TABLE v3_task_comments
    DROP COLUMN IF EXISTS mentions,
    DROP COLUMN IF EXISTS deleted_by,
    DROP COLUMN IF EXISTS deleted_at;

COMMIT;
