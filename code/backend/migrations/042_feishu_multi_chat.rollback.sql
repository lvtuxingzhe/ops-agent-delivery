-- 回滚 042：把多群配置收回单群。
-- 只保留 chat_ids 里的第一个群；执行前确认没有依赖第二个群的审批卡片。

BEGIN;

UPDATE v3_configs
   SET value = jsonb_set(
           value - 'chat_ids',
           '{chat_id}',
           to_jsonb(COALESCE((value->'chat_ids')->>0, ''))
       ),
       updated_at = now()
 WHERE namespace = 'notify_channel'
   AND key = 'feishu_app'
   AND value ? 'chat_ids';

COMMIT;
