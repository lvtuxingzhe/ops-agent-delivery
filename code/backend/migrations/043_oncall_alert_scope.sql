-- 043：值班拆成“审批值班”和“告警值班”。
--
-- 审批值班继续只允许管理员，负责卡片审批；告警值班允许任意启用用户，
-- 只接收告警文本和被 @。scope 列已存在，这里只放宽检查约束。
--
-- 同一条迁移顺手删除旧飞书群机器人 webhook 配置；它已不再被任何代码读取。
-- 删除不可恢复，回滚脚本只恢复 scope 约束，不恢复旧 webhook 配置。
--
-- 回滚：
--   backend/migrations/043_oncall_alert_scope.rollback.sql

BEGIN;

ALTER TABLE v3_oncall_shifts
  DROP CONSTRAINT IF EXISTS ck_v3_oncall_shifts_scope;

ALTER TABLE v3_oncall_shifts
  ADD CONSTRAINT ck_v3_oncall_shifts_scope
  CHECK (scope IN ('approval', 'alert'));

DELETE FROM v3_configs
 WHERE namespace = 'notify_channel'
   AND key = 'feishu';

COMMIT;
