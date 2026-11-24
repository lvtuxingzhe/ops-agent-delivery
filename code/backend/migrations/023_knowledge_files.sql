-- 023 知识库文档上传：文件元数据 + 文件类型筛选
--
-- 上传的文档仍落 v3_knowledge_entries，但用 source_file_type 区分来源；
-- 条目本身仍走“谁创建谁维护”的权限，不另建一套文件权限。
-- 原文件放 var/uploads/knowledge/，数据库只存相对路径，不存绝对路径，
-- 避免换机器/换目录后路径失效，也不把服务器目录结构暴露给接口。
--
-- 回滚：删列 + 删索引即可；已落盘的原文件不自动删除，保留审计证据。

ALTER TABLE v3_knowledge_entries
    ADD COLUMN IF NOT EXISTS source_file_name TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_file_type TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_file_size BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_file_path TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS source_file_sha256 TEXT NOT NULL DEFAULT '',
    -- 完整抽取文本只用于关键词检索，不在列表/详情里整段返回。
    ADD COLUMN IF NOT EXISTS source_text TEXT NOT NULL DEFAULT '';

ALTER TABLE v3_knowledge_entries
    DROP CONSTRAINT IF EXISTS ck_v3_knowledge_file_type;
ALTER TABLE v3_knowledge_entries
    ADD CONSTRAINT ck_v3_knowledge_file_type
    CHECK (source_file_type IN ('', 'pdf', 'text', 'excel', 'html', 'word', 'markdown'));

CREATE INDEX IF NOT EXISTS idx_v3_knowledge_file_type
    ON v3_knowledge_entries (source_file_type)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN v3_knowledge_entries.source_file_type IS
    '上传文档的文件类型：pdf/text/excel/html/word/markdown；空表示非上传条目';
COMMENT ON COLUMN v3_knowledge_entries.source_file_path IS
    '相对 CODE_ROOT 的原文件路径，仅服务端读取，不返回给前端';
COMMENT ON COLUMN v3_knowledge_entries.source_text IS
    '抽取出的完整文本，用于关键词检索；详情仍返回截断后的 body';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.v3_knowledge_entries TO ops_app';
    END IF;
END
$$;
