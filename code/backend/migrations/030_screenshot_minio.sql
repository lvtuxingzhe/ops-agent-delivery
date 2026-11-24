-- 030：排查截图原图迁对象存储。
--
-- 背景：原图直接进 PostgreSQL bytea，单任务最多 5×10 MB，任务量上来后
-- 数据库体积和备份成本会明显变重。改为原图进 MinIO 私有桶，数据库只留
-- 元数据、对象键和报告用的缩略图。
--
-- 兼容：历史行保持 storage_backend='db'、image 有值；新行可写 minio 且 image 为空。
--
-- 回滚：backend/migrations/030_screenshot_minio.rollback.sql
--       回滚前必须先把 minio 行的原图搬回数据库，否则 SET NOT NULL 会失败。

BEGIN;

ALTER TABLE v3_task_screenshots
    ADD COLUMN IF NOT EXISTS storage_backend text NOT NULL DEFAULT 'db',
    ADD COLUMN IF NOT EXISTS object_key text;

ALTER TABLE v3_task_screenshots
    ALTER COLUMN image DROP NOT NULL;

COMMENT ON COLUMN v3_task_screenshots.storage_backend IS
    'db=原图在 image 字段；minio=原图在对象存储 object_key';
COMMENT ON COLUMN v3_task_screenshots.object_key IS
    'MinIO 对象键；仅 storage_backend=minio 时有效';
COMMENT ON COLUMN v3_task_screenshots.image IS
    '原始截图；storage_backend=db 时有值，minio 行为空';

COMMIT;
