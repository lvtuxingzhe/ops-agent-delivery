-- 011_litellm_template.sql
-- 新增 LiteLLM Proxy 厂商模板：默认不改变当前直连 DeepSeek 的配置，
-- 只在配置页多一个可选项；生产把 base_url 指向内网 proxy，模型名按 proxy 的 model_list 填。
INSERT INTO v3_configs (namespace, key, value, credential_ref, enabled)
VALUES (
    'template',
    'litellm',
    '{
      "display_name": "LiteLLM Proxy",
      "protocol": "openai_compatible",
      "base_url_hint": "http://litellm.internal:4000/v1",
      "model_hint": "deepseek-chat",
      "note": "统一出网点；base_url 填内网 proxy，credential_ref 填 proxy 的虚拟 key 引用名"
    }'::jsonb,
    '',
    TRUE
)
ON CONFLICT (namespace, key) DO UPDATE
SET value = EXCLUDED.value,
    enabled = TRUE,
    updated_at = now();
