-- 035_user_soft_delete.sql
-- 用户删除改为软删除：页面隐藏、不能登录，任务和审计里的历史身份保留。
--
-- 为什么不在页面里直接物理删 v3_users：
--   v3_tasks.submitted_by / confirmed_by 故意没有外键（见 005 的设计取舍），
--   审计事件的 actor 也只是数字字符串。物理删用户不会级联删任务，但会让
--   历史任务和审计出现“这个 ID 是谁查不到”的情况。软删除保留用户行，
--   身份仍可解析，页面和登录则按 deleted_at 过滤。

BEGIN;

ALTER TABLE public.v3_users
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_by BIGINT;

-- 列表只查未删除用户；部分索引让这条过滤保持轻量。
CREATE INDEX IF NOT EXISTS v3_users_live_idx
    ON public.v3_users (id)
    WHERE deleted_at IS NULL;

COMMENT ON COLUMN public.v3_users.deleted_at IS
    '软删除时间；非空表示该账号已从页面移除，不能登录，历史任务/审计仍保留';
COMMENT ON COLUMN public.v3_users.deleted_by IS
    '执行删除的管理员用户 ID，仅用于审计核对';

COMMIT;
