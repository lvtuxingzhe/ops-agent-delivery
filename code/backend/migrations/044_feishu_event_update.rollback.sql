-- 回滚 044：收回飞书事件表的 UPDATE 权限。
--
-- 回滚后事件仍能插入，但 result / detail / task_id / actor_id 无法补写，
-- 排查前先确认没有人还依赖这些结果字段。

BEGIN;

REVOKE UPDATE ON v3_feishu_events FROM ops_app;

COMMIT;
