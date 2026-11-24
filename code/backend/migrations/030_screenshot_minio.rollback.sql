-- 030 回滚：先确认所有 minio 行都已把原图搬回 image 字段，再执行。
-- 如果有 image 为空的 minio 行，下面的 SET NOT NULL 会失败，这是保护，不是缺陷。

BEGIN;

ALTER TABLE v3_task_screenshots
    ALTER COLUMN image SET NOT NULL;

ALTER TABLE v3_task_screenshots
    DROP COLUMN IF EXISTS object_key,
    DROP COLUMN IF EXISTS storage_backend;

COMMIT;
