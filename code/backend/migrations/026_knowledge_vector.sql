-- 026 知识库向量检索（pgvector）
--
-- 1) v3_knowledge_entries 增加 512 维 embedding，来源为本地 BGE-small-zh-v1.5；
-- 2) HNSW + cosine 距离索引，RAG 用 `<=>` 排序；
-- 3) 只对未软删除的条目建索引，删除的条目不参与召回。
--
-- 回滚：DROP INDEX + DROP COLUMN，保留 vector 扩展不影响其他表。

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE v3_knowledge_entries
    ADD COLUMN IF NOT EXISTS embedding vector(512);

CREATE INDEX IF NOT EXISTS idx_v3_knowledge_entries_embedding
    ON v3_knowledge_entries USING hnsw (embedding vector_cosine_ops)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN v3_knowledge_entries.embedding IS
    '本地 BGE-small-zh-v1.5 生成的 512 维归一化向量；cosine 距离检索';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON public.v3_knowledge_entries TO ops_app';
    END IF;
END
$$;
