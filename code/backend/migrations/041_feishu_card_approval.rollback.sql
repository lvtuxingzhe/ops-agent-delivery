-- 回滚 041：删除飞书身份绑定和回调事件记录。
-- 执行前先导出 v3_feishu_identities，否则会丢失“谁绑定过谁”的历史。

BEGIN;

DROP TABLE IF EXISTS v3_feishu_events;
DROP TABLE IF EXISTS v3_feishu_identities;

COMMIT;
