-- 知识库 AI 准入决策：部署经验 / 排查报告先判后入。
-- 只存“这条任务的知识值不值得入库”，不直接写 v3_knowledge_entries，
-- 避免和现有虚拟聚合、去重逻辑打架。

CREATE TABLE IF NOT EXISTS v3_knowledge_decisions (
  id BIGSERIAL PRIMARY KEY,
  task_id TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('deployment', 'investigation')),
  decision TEXT NOT NULL CHECK (decision IN ('admit', 'review', 'reject')),
  value_score NUMERIC(3, 1) NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  signals JSONB NOT NULL DEFAULT '[]'::jsonb,
  curated_title TEXT NOT NULL DEFAULT '',
  curated_summary TEXT NOT NULL DEFAULT '',
  curated_body TEXT NOT NULL DEFAULT '',
  curated_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  curated_tech_domain TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  prompt_version INTEGER NOT NULL DEFAULT 1,
  manual_override TEXT NOT NULL DEFAULT '',
  manual_reason TEXT NOT NULL DEFAULT '',
  decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (task_id, source_kind)
);

CREATE INDEX IF NOT EXISTS idx_v3_knowledge_decisions_lookup
  ON v3_knowledge_decisions (source_kind, decision, decided_at DESC);
