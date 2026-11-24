-- 013：报错截图与 OCR 证据。
--
-- 原图存 bytea，报告只给脱敏 OCR 文本与缩略图 data URI；
-- 原图不通过 API 对外提供，避免普通角色拿到含凭据的整屏截图。
--
-- 新表不会自动继承 init_db.py 的 GRANT，必须在迁移里显式授权。

CREATE TABLE IF NOT EXISTS v3_task_screenshots (
    id              UUID PRIMARY KEY,
    task_id         UUID NOT NULL REFERENCES v3_tasks(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    mime            TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    sha256          CHAR(64) NOT NULL,
    ocr_text        TEXT NOT NULL,
    ocr_confidence  REAL NOT NULL DEFAULT 0,
    data_status     TEXT NOT NULL,
    thumbnail_uri   TEXT NOT NULL,
    image           BYTEA NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_v3_task_screenshots_task
    ON v3_task_screenshots (task_id, created_at);

COMMENT ON TABLE v3_task_screenshots IS
    '报错截图：OCR 脱敏文本 + 缩略图给报告；原图仅写入数据库，不直接对外提供';
COMMENT ON COLUMN v3_task_screenshots.thumbnail_uri IS
    'data:image/jpeg;base64,... 缩略图，供报告页展示';
COMMENT ON COLUMN v3_task_screenshots.image IS
    '原始截图；普通角色不通过 API 读取，管理员可按需导出取证';

-- 新环境 init_db 会先跑迁移、后建角色；角色还不存在时先跳过，
-- 等 init_roles 的 “GRANT ... ON ALL TABLES” 再统一授权。
-- 已有环境直接补 013 时，这个 DO 块会立刻把权限补上。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.v3_task_screenshots TO ops_app';
    END IF;
END $$;
