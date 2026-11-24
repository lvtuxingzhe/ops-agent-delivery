-- 033：知识治理指标与检索事件。
--
-- 目的：
--   1. 记录 AI 判定次数，区分首次判定和人工重判；
--   2. 记录轻量检索事件，只存查询哈希、命中数、最高分，不存查询原文；
--   3. 给治理页统计卡提供可复算的数据源。

BEGIN;

ALTER TABLE v3_knowledge_decisions
    ADD COLUMN IF NOT EXISTS ai_runs INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS v3_knowledge_retrieval_events (
    id          BIGSERIAL PRIMARY KEY,
    query_hash  TEXT      NOT NULL,
    hits        INTEGER   NOT NULL DEFAULT 0,
    top_score   NUMERIC(6, 4) NOT NULL DEFAULT 0,
    mode        TEXT      NOT NULL DEFAULT '',
    scope       TEXT      NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS v3_knowledge_retrieval_created_idx
    ON v3_knowledge_retrieval_events (created_at DESC);

COMMENT ON TABLE v3_knowledge_retrieval_events IS
    '知识检索轻量事件；只存查询哈希和命中指标，不存查询原文';
COMMENT ON COLUMN v3_knowledge_decisions.ai_runs IS
    'AI 判定次数；首次为 1，重判或内容变化后再加 1';

GRANT SELECT, INSERT ON v3_knowledge_retrieval_events TO ops_app;
GRANT USAGE, SELECT ON SEQUENCE public.v3_knowledge_retrieval_events_id_seq TO ops_app;

COMMIT;
