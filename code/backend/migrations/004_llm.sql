-- =============================================================================
-- 运维 Agent 平台 · 第 ④ 步扩展：通用大模型用户自配置
--
-- 对应方案 6.1 / 开工清单第 ④ 步。核心目标：换模型、加模型是页面上填几项的
-- 事，不用改代码、不用重新发版。
--
-- 两张表的分工（很重要，别合成一张）
--   llm_provider_templates  厂商预置模板：帮用户把「接口地址 / 默认模型名」
--                           带出来而已。它是【可编辑的配置数据】，不是代码常量；
--                           管理员可以在配置页改它，用户选完仍可覆盖。
--   llm_provider_configs    用户真正配的那条模型。每条都带自己的地址、模型名、
--                           凭据引用、启停、默认标记。
--
-- 关于表里的公网地址（必须说清楚）
--   模板里的 base_url_hint 只是「填表时带出来的默认值」，是公开文档信息。
--   第一期代码里【没有任何 HTTP 客户端会去连它们】：方案 6.1 定稿「默认关、
--   不连公网」，真实调用一律拒绝并留痕，等合规与出网审批后才开放。
--   内网部署时把地址改成自建网关 / 内网地址即可，一行代码都不用动。
--
-- 凭据
--   两张表都【没有密钥列】。llm_provider_configs 只有 credential_ref（编号），
--   真实密钥由运行期环境变量 / 密钥管理临时注入，不写入数据库、不回显、不进审计。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 厂商预置模板
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_provider_templates (
    id              BIGSERIAL   PRIMARY KEY,
    code            TEXT        NOT NULL UNIQUE,   -- deepseek / qwen / custom ...
    display_name    TEXT        NOT NULL,
    -- 协议：openai_compatible = 直接用统一兼容层；
    --       native_* = 原生格式不同，第一期只登记、不实现真实调用（薄转换层留接口）
    protocol        TEXT        NOT NULL DEFAULT 'openai_compatible',
    base_url_hint   TEXT        NOT NULL DEFAULT '',  -- 填表参考，不是连接目标
    model_hint      TEXT        NOT NULL DEFAULT '',  -- 默认模型名参考
    note            TEXT        NOT NULL DEFAULT '',
    enabled         BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE llm_provider_templates IS
    '厂商预置模板：只是给配置页带出默认值的配置数据，代码不写死任何厂商地址';
COMMENT ON COLUMN llm_provider_templates.base_url_hint IS
    '填写参考地址。第一期不发起任何连接；内网部署改成自建网关即可';

-- 种子模板。ON CONFLICT DO UPDATE 只更新说明文字，
-- 【不覆盖 enabled】—— 管理员在配置页停用的模板不能被初始化脚本重新打开。
INSERT INTO llm_provider_templates
    (code, display_name, protocol, base_url_hint, model_hint, note) VALUES
    ('deepseek',  'DeepSeek',          'openai_compatible', 'https://api.deepseek.com/v1',                    'deepseek-chat',  'OpenAI 兼容'),
    ('qwen',      '通义千问',           'openai_compatible', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus', '阿里云百炼兼容模式'),
    ('doubao',    '豆包 / 火山方舟',    'openai_compatible', 'https://ark.cn-beijing.volces.com/api/v3',        '',             '模型名填你的接入点 ID'),
    ('hunyuan',   '腾讯混元',           'openai_compatible', 'https://api.hunyuan.cloud.tencent.com/v1',       'hunyuan-standard', 'OpenAI 兼容'),
    ('kimi',      'Kimi / Moonshot',   'openai_compatible', 'https://api.moonshot.cn/v1',                     'moonshot-v1-8k', 'OpenAI 兼容'),
    ('zhipu',     '智谱 GLM',           'openai_compatible', 'https://open.bigmodel.cn/api/paas/v4',            'glm-4-flash',    'OpenAI 兼容'),
    ('minimax',   'MiniMax',            'openai_compatible', 'https://api.minimax.chat/v1',                    '',               '模型名按控制台填'),
    ('siliconflow','硅基流动',          'openai_compatible', 'https://api.siliconflow.cn/v1',                    '',               '聚合多家开源模型'),
    ('openai',    'OpenAI',             'openai_compatible', 'https://api.openai.com/v1',                      'gpt-4o-mini',    '兼容协议原版'),
    ('ollama',    '本地 Ollama',        'openai_compatible', 'http://127.0.0.1:11434/v1',                      '',               '本机推理，不出公网'),
    ('vllm',      '本地 vLLM',          'openai_compatible', 'http://127.0.0.1:8000/v1',                       '',               '内网推理服务'),
    ('anthropic', 'Claude / Anthropic', 'native_claude',     '',                                                '',               '原生格式不同；第一期只登记，薄转换层留接口'),
    ('gemini',    'Gemini',             'native_gemini',     '',                                                '',               '原生格式不同；第一期只登记，薄转换层留接口'),
    ('custom',    '自定义端点',          'openai_compatible', '',                                                '',               '任何 OpenAI 兼容的地址都能填')
ON CONFLICT (code) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        protocol     = EXCLUDED.protocol,
        note         = EXCLUDED.note;

-- -----------------------------------------------------------------------------
-- 用户配置的模型
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_provider_configs (
    id                BIGSERIAL   PRIMARY KEY,
    name              TEXT        NOT NULL UNIQUE,   -- 页面上叫什么
    template_code     TEXT        NOT NULL DEFAULT 'custom',  -- 参考哪个模板（可改）
    protocol          TEXT        NOT NULL DEFAULT 'openai_compatible',
    base_url          TEXT        NOT NULL DEFAULT '',        -- 用户可以覆盖模板
    model_name        TEXT        NOT NULL DEFAULT '',        -- 换模型只改这一格
    -- 【只存编号】真实密钥不写入数据库、不回显、不进审计
    credential_ref    TEXT        NOT NULL DEFAULT '',
    -- 调用参数（可选）：超时 / 最大返回长度 / 温度
    params            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- 默认关：新建的模型一律先停用，管理员明确启用才生效
    enabled           BOOLEAN     NOT NULL DEFAULT FALSE,
    is_default        BOOLEAN     NOT NULL DEFAULT FALSE,
    -- 健康检查：第一期不连外部系统，只登记，不实际探测
    health_status     TEXT        NOT NULL DEFAULT 'unknown'
                      CHECK (health_status IN ('unknown','ok','no_data','failed')),
    health_detail     TEXT        NOT NULL DEFAULT '',
    health_checked_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 最多只能有一个默认模型（部分唯一索引：只有 is_default=true 的行参与约束）
CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_single_default
    ON llm_provider_configs (is_default) WHERE is_default;
CREATE INDEX IF NOT EXISTS idx_llm_enabled ON llm_provider_configs (enabled);

COMMENT ON TABLE llm_provider_configs IS
    '用户在配置页自己配的大模型。第一期只做配置能力：默认关，不连公网';
COMMENT ON COLUMN llm_provider_configs.credential_ref IS
    '密钥引用编号，不是密钥；真实值运行期由环境变量/密钥管理临时注入';
COMMENT ON COLUMN llm_provider_configs.enabled IS
    '默认 false。按任务开的总开关在 platform_settings.llm_enabled_by_task，也是默认关';
