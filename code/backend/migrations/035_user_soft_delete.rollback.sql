-- 035_user_soft_delete.rollback.sql
-- 回滚前先确认没有账号处于软删除状态，否则这些账号会重新出现在列表里。
BEGIN;

DROP INDEX IF EXISTS public.v3_users_live_idx;

ALTER TABLE public.v3_users
    DROP COLUMN IF EXISTS deleted_by,
    DROP COLUMN IF EXISTS deleted_at;

COMMIT;
