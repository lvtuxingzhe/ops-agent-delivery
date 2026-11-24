-- 008_drop_legacy.sql
-- 删除 20 张旧业务表。
--
-- 执行前提（全部满足后才允许执行）：
--   1. 后端代码已不再引用这些表；
--   2. 007 已执行且演练库验证通过；
--   3. 已做数据库备份，并确认备份可还原；
--   4. 删除范围已经确认。
--
-- 新环境引导（init_db.py）包含 008，避免新库留下 20 张空旧表。
--
-- 为什么按这个顺序删：先删子表（有外键指向别人的），再删父表，
-- 否则 CASCADE 会把没预料到的边一起带走。这里仍显式写 CASCADE，
-- 是为了即便有遗漏的引用也能一次删干净，而不是删一半留下坏外键。

BEGIN;

-- 1. 依赖 v3_tasks / tasks / plans 的令牌类
DROP TABLE IF EXISTS confirmation_tokens CASCADE;

-- 2. 认证类（顺序：映射表 → 凭据 → 绑定 → 验证码）
DROP TABLE IF EXISTS user_roles CASCADE;
DROP TABLE IF EXISTS auth_credentials CASCADE;
DROP TABLE IF EXISTS phone_bindings CASCADE;
DROP TABLE IF EXISTS sms_codes CASCADE;

-- 3. 任务执行链（子 → 父）
DROP TABLE IF EXISTS task_steps CASCADE;
DROP TABLE IF EXISTS evidences CASCADE;
DROP TABLE IF EXISTS reports CASCADE;
DROP TABLE IF EXISTS plans CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;

-- 4. 旧审计记录（哈希链触发器一并删除）
DROP TABLE IF EXISTS audit_events CASCADE;

-- 5. 执行许可 / 配置 / 白名单 / 知识
DROP TABLE IF EXISTS execution_permits CASCADE;
DROP TABLE IF EXISTS datasource_configs CASCADE;
DROP TABLE IF EXISTS llm_provider_configs CASCADE;
DROP TABLE IF EXISTS llm_provider_templates CASCADE;
DROP TABLE IF EXISTS platform_settings CASCADE;
DROP TABLE IF EXISTS whitelists CASCADE;
DROP TABLE IF EXISTS skills CASCADE;

-- 6. 角色与用户（最后删，前面所有表都可能引用它们）
DROP TABLE IF EXISTS roles CASCADE;
DROP TABLE IF EXISTS users CASCADE;

COMMIT;

-- ---------------------------------------------------------------------------
-- 执行前自检（结果应全部为 0）
-- ---------------------------------------------------------------------------
-- 在仓库根执行：
--   Select-String -Path "code\backend\app\*.py","code\backend\app\**\*.py" `
--     -Pattern "FROM (tasks|plans|reports|evidences|task_steps|audit_events|users|user_roles|auth_credentials|sms_codes|phone_bindings|confirmation_tokens|roles|skills|whitelists|platform_settings|datasource_configs|execution_permits|llm_provider_configs|llm_provider_templates)\b"
--
-- 执行前实测引用数：
--   后端 app 层：audit_events 0 / tasks 0 / execution_permits 0
--   20 张表全部为 0 —— 应用代码不再读写任何一张旧表。
--
-- 迁移后的调用关系：
--   * core/audit.py 只保留 AuditEvent + insert_audit_event，内部委托
--     audit_v3.insert_event 写 v3_audit_events；actor_role 折进 payload；
--   * 哈希链（prev_hash / hash）、commit_transition、verify_chain 全部退役，
--     强一致入口统一到 audit_v3.commit_transition（五态由 contract 判定）；
--   * init_db.py 改为顺序跑 001→007 + 对 v3 审计记录做自检；
--   * 四个自检脚本 + init_auth 的负向权限测试改查 v3_audit_events。
--
-- 执行后会出现以下变化：
--   * 早期的迁移核对工具仍会去数旧表行数（init_contract.py、
--     selftest_contract.py），旧表没了它们会报错；它们只是迁移核对工具，
--     已经跑完，不再需要在新环境重跑；
--   * 另外两个旧审计记录专项自检（init_audit_v3.py / init_credentials.py、
--     selftest_audit_v3.py 的部分断言）检查的是「旧 audit_events 触发器是否还在」，
--     删表后这类断言必然失败——v3 审计记录的等价检查已由 selftest_audit_v3 的
--     v3 用例与 init_db 的 7 条覆盖；
--   * 新环境引导走 init_db（001→007），它不包含 008，因此不受影响。
--
-- 结论：执行完成后，20 张旧表不再存在，认证表也在其中 ——
-- 登录、任务、配置、审计全部走 v3_*。
--
-- 退役的脚本（008 后不再有意义，已加显式校验，跑起来只打印「已退役」）：
--   init_contract / init_llm / init_samples 的旧种子 / init_auth 的旧种子 /
--   selftest_audit_v3 的旧审计记录断言（已改成「旧表 0 张」）。
--
--
-- ---------------------------------------------------------------------------
-- 删除范围与影响面（执行前核对用）
-- ---------------------------------------------------------------------------
-- 会永久删除下面 20 张表（括号内为当前行数）：
--
--   旧审计记录
--     audit_events(865)             → 已迁 v3_audit_events；旧哈希链退役
--
--   任务执行链
--     tasks(17)                     → 已迁 v3_tasks
--     plans(16)                     → 方案 JSONB 折进 v3_tasks
--     reports(0) / evidences(0)     → 报告与证据 JSONB 折进 v3_tasks
--     task_steps(0)                 → 步骤信息折进 v3_tasks
--     confirmation_tokens(16)       → 确认走五态 contract + v3 审计记录，旧令牌不再可用
--     execution_permits(1)          → 执行许可走闸口（执行许可上线之后）
--
--   认证与会话（认证统一走 v3_* 登录）
--     users(2) / user_roles(2) / roles(6)   → 已迁 v3_users / v3_role_flags
--     auth_credentials(1)           → 密码哈希迁 v3_users.password_hash
--     phone_bindings(1)             → 手机绑定迁 v3_users
--     sms_codes(0)                  → 短信码表不再使用（限频逻辑已内建）
--
--   配置与知识
--     datasource_configs(2)         → 已迁 v3_configs/datasource
--     llm_provider_configs(2)       → 已迁 v3_configs/llm
--     llm_provider_templates(14)    → 已迁 v3_configs/template
--     platform_settings(6)          → 已迁 v3_configs/setting
--     skills(0) / whitelists(0)     → 未使用
--
-- 迁移落点：v3_tasks / v3_audit_events / v3_configs / v3_users / v3_role_flags。
--
-- 执行前提：① 新备份并验证可还原；② 确认「旧 audit_events 不再需要
-- 留档」（865 条只存在于旧表，v3 审计记录不含这 865 条的原文）；③ 确认认证表可删；
-- ④ 接受 init_contract / selftest_contract / init_audit_v3 / init_credentials
--    这些一次性核对脚本在删表后失效。
--
-- 建议执行顺序：pg_dump → 在临时演练库先跑一遍 → 验证登录/提交/
-- 查状态/查报告四条链路 → 再在生产库执行。
--
