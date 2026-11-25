-- 042：飞书应用从单群改为多群。
--
-- 旧配置只有一个 chat_id；新配置用 chat_ids 数组。
-- 这条迁移只搬数据，不改表结构，执行后旧字段删除。
--
-- 回滚：
--   backend/migrations/042_feishu_multi_chat.rollback.sql

BEGIN;

UPDATE v3_configs
   SET value = jsonb_set(
           value - 'chat_id',
           '{chat_ids}',
           jsonb_build_array(value->>'chat_id')
       ),
       updated_at = now()
 WHERE namespace = 'notify_channel'
   AND key = 'feishu_app'
   AND value ? 'chat_id'
   AND NOT (value ? 'chat_ids');

COMMIT;
