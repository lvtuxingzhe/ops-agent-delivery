-- 044：飞书事件结果回写需要 UPDATE 权限。
--
-- 041 只给了应用账号 SELECT + INSERT，但群命令和卡片回调要在同一条
-- event_id 上补写 result / detail / task_id / actor_id，需要 UPDATE。
--
-- 回滚：
--   backend/migrations/044_feishu_event_update.rollback.sql

BEGIN;

GRANT UPDATE ON v3_feishu_events TO ops_app;

COMMIT;
