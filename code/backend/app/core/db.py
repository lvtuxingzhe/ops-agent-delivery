"""数据库连接管理。"""

from __future__ import annotations

import atexit
import os
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from .config import Settings, require_database

# (连接串, autocommit) → 池。一个进程内每个身份只建一个。
_POOLS: dict[tuple[str, bool], ConnectionPool] = {}


def pool_enabled() -> bool:
    """默认开池；`DB_POOL_ENABLED=false` 走直连，给排障留一条后路。"""
    return os.environ.get("DB_POOL_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _pool_limits() -> tuple[int, int]:
    """池上下限也走环境变量，给不同规格的机器留调节余地。"""
    try:
        minimum = max(0, int(os.environ.get("DB_POOL_MIN", "1")))
        maximum = max(1, int(os.environ.get("DB_POOL_MAX", "8")))
    except ValueError:
        minimum, maximum = 1, 8
    if minimum > maximum:
        minimum = maximum
    return minimum, maximum


def _pool(dsn: str, *, autocommit: bool) -> ConnectionPool:
    """取（或建）对应身份的连接池。第一次借连接时才真正去连库。"""
    key = (dsn, autocommit)
    pool = _POOLS.get(key)
    if pool is not None:
        return pool
    minimum, maximum = _pool_limits()
    pool = ConnectionPool(
        dsn,
        min_size=minimum,
        max_size=maximum,
        # 借不到连接最多等 10 秒，超时就直接失败，不让请求无限挂住
        timeout=10.0,
        kwargs={"autocommit": autocommit},
        name=f"ops-{'app' if not autocommit else 'auto'}",
    )
    _POOLS[key] = pool
    return pool


def close_pools() -> None:
    """关掉所有池。进程退出、测试收尾时调用；生产由进程生命周期保障。"""
    for pool in list(_POOLS.values()):
        pool.close()
    _POOLS.clear()


# 进程退出时主动关池：不关的话解释器回收顺序不确定，
# 后台线程会在退出时报 “couldn't stop thread within 5.0 seconds”。
atexit.register(close_pools)


def connect(dsn: str, *, autocommit: bool = False) -> Any:
    """建一条**直连**（不进池）。只有排障与池自身故障时才该用它。"""
    if not dsn:
        raise RuntimeError("连接串为空，拒绝连接（避免误连默认地址）")
    return psycopg.connect(dsn, autocommit=autocommit)


def _borrow(dsn: str, *, autocommit: bool) -> Any:
    """借一条连接：默认走池，`DB_POOL_ENABLED=false` 时退回直连。"""
    if not dsn:
        raise RuntimeError("连接串为空，拒绝连接（避免误连默认地址）")
    if not pool_enabled():
        return connect(dsn, autocommit=autocommit)
    return _pool(dsn, autocommit=autocommit).connection()


def connect_app(settings: Settings) -> Any:
    """日常业务连接：应用账号（走池）。"""
    return _borrow(require_database(settings), autocommit=False)


def connect_audit_reader(settings: Settings) -> Any:
    """查审计记录专用：只读账号（走池，autocommit 打开）。"""
    if not settings.db_audit_reader_url:
        raise RuntimeError("缺少 DB_AUDIT_READER_URL")
    return _borrow(settings.db_audit_reader_url, autocommit=True)


def connect_admin(settings: Settings) -> Any:
    """初始化专用：管理账号（走池，autocommit 打开）。业务代码不该 import 这个函数。"""
    if not settings.db_admin_url:
        raise RuntimeError("缺少 DB_ADMIN_URL")
    return _borrow(settings.db_admin_url, autocommit=True)


def connect_auth(settings: Settings) -> Any:
    """
    认证专用账号（ops_auth）：只给登录校验用 —— 查/改 auth_credentials、
    phone_bindings、sms_codes。除了 app/services/ 里的认证服务，别的模块
    不该 import 这个函数。

    为什么认证要单独一条连接：认证表归这个账号所有，应用主账号对它
    零权限（连 SELECT 都没有）。这是「密码独立权限」的数据库落地 ——
    就算业务代码被注入，也物理上摸不到密码哈希。

    autocommit 关着：绑定 TOTP、写验证码这类动作要走事务，失败整体回滚。
    """
    if not settings.db_auth_url:
        raise RuntimeError("缺少 DB_AUTH_URL（认证表专属连接串）")
    return _borrow(settings.db_auth_url, autocommit=False)


def connect_user_admin(settings: Settings) -> Any:
    """
    用户管理专用账号（ops_user_admin）：只给账号管理服务用。

    它比 ops_app / ops_auth 权限大：能在同一个事务里写用户资料、认证凭据、
    手机绑定，并向审计表追加事件。这样建用户不会出现「资料建了、凭据没建」
    的半成品，审计也能和业务变更一起提交或回滚。

    只允许 `user_admin_service` 依赖它。不要把它当日常业务连接用。
    """
    if not settings.db_user_admin_url:
        raise RuntimeError("缺少 DB_USER_ADMIN_URL（用户管理专用连接串）")
    return _borrow(settings.db_user_admin_url, autocommit=False)
