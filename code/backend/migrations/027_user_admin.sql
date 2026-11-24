-- 027_user_admin.sql
-- 账号与权限管理的唯一表结构改动：用户名大小写不敏感唯一。
--
-- 为什么需要函数索引：
--   现有 UNIQUE(username) 只挡完全相同的字符串，挡不住 Alice / alice 并存。
--   管理员建号时如果不加这层，日志、审批、审计里会出现两个“看起来一样”的账号。
--
-- 不在这里建角色/授权：角色名和密码是配置项，按现有约定放 Python 初始化脚本
-- （backend/scripts/init_user_admin.py），避免把密码写进 SQL。

BEGIN;

-- 先查重，有重复就明确失败，不自动改历史用户名。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.v3_users
         GROUP BY lower(username)
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'v3_users 存在大小写不同的重复用户名，先人工处理再执行 027';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_v3_users_username_lower
    ON public.v3_users (lower(username));

COMMIT;
