-- 027_user_admin.rollback.sql
-- 只回滚用户名大小写唯一索引；角色与授权由 init_user_admin.py 管理，不在 SQL 里反向删除。

DROP INDEX IF EXISTS public.uq_v3_users_username_lower;
