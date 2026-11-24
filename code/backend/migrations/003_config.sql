-- =============================================================================
-- 运维 Agent 平台 · 第 ④ 步：配置页（数据源健康检查结果列）
--
-- 这份文件很小，因为它只补结构 —— 数据源表的主体（名称/类型/接法/地址/
-- 凭据引用/只读标记/启停）在 001_init.sql 里第 ② 步就建好了，本步是兑现。
--
-- 新增的东西
--   datasource_configs.health_status   健康检查结果
--   datasource_configs.health_detail   结果说明（给页面看的一句人话）
--
-- health_status 的取值（和证据的 data_status 同一套词汇）：
--   unknown   还没做过健康检查（新数据源的初始状态）
--   ok        真的连上过并且通（当前版本不会出现这个值，见下）
--   no_data   当前版本不连外部系统，结果记为「无数据」，不记为「正常」
--   failed    做过的检查不通过（比如地址格式本身不合法）
--
-- 显示规则：报告/页面里「无数据」显示为「无数据」，不显示为「正常」。
-- 健康检查当前版本【不实际连接】任何外部系统（不连真实环境、不连公网），
-- 所以合法的最好结果就是 no_data，这是有意的，不是没写完。
--
-- 为什么 platform_settings 不在这里加「网段」键：键值对表加键不用改表
-- 结构，默认值由 scripts/init_config.py 种子（值是配置不是结构）。
-- =============================================================================

-- 健康检查结果两列。ADD COLUMN IF NOT EXISTS 让整个文件可重复执行。
ALTER TABLE public.datasource_configs
    ADD COLUMN IF NOT EXISTS health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (health_status IN ('unknown', 'ok', 'no_data', 'failed')),
    ADD COLUMN IF NOT EXISTS health_detail TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN datasource_configs.health_status IS
    '健康检查结果：unknown=没查过，ok=真连上过，no_data=当前版本不连外部（诚实登记），failed=检查不通过';
COMMENT ON COLUMN datasource_configs.health_detail IS
    '健康检查的一句人话说明，直接显示在配置页上';
