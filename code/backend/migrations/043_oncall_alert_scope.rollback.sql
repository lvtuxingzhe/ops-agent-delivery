-- 043 回滚：值班 scope 恢复为只允许 approval。
--
-- 执行前必须先清理 alert 值班记录，否则检查约束无法加上：
--   DELETE FROM v3_oncall_shifts WHERE scope = 'alert';
--
-- 旧飞书群机器人 webhook 配置已在正向迁移中删除，这里不恢复。

BEGIN;

ALTER TABLE v3_oncall_shifts
  DROP CONSTRAINT IF EXISTS ck_v3_oncall_shifts_scope;

ALTER TABLE v3_oncall_shifts
  ADD CONSTRAINT ck_v3_oncall_shifts_scope
  CHECK (scope = 'approval');

COMMIT;
