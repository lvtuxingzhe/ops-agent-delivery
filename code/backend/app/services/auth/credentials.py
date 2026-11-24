"""认证凭据读取、失败计数、锁定与 TOTP 密钥解包。"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from ...core.config import Settings
from ...core.db import connect_auth
from ...core.security import SecretBox, totp_match_counter
from .core import AuthError
from .totp import totp_status


def _check_lockout(credential: tuple) -> None:
    """失败次数到阈值就锁。提示语不带内部细节。"""
    locked_until = credential[3]  # 字段顺序见 _fetch_credential
    if locked_until and locked_until.timestamp() > time.time():
        raise AuthError("账号已临时锁定，请稍后再试")


def _fetch_credential(settings: Settings, username: str) -> tuple | None:
    """
    用【认证专属账号】查凭据。返回 (user_id, password_hash, totp_secret_enc,
    locked_until, totp_enabled, failed_attempts, is_active)。

    ops_auth 对 users 只有 SELECT（init_auth.py 授的权），join 一下把
    is_active 也带出来，禁用账号在这里就拦掉。
    """
    with connect_auth(settings) as conn:
        row = conn.execute(
            """
            SELECT c.user_id, c.password_hash, c.totp_secret_enc,
                   c.locked_until, c.totp_enabled, c.failed_attempts,
                   u.is_active
            FROM v3_auth_credentials c
            JOIN v3_users u ON u.id = c.user_id
            WHERE u.username = %s AND u.deleted_at IS NULL
            """,
            (username,),
        ).fetchone()
    return row


def _record_password_failure(settings: Settings, user_id: int,
                              threshold: int, lock_minutes: int) -> None:
    """密码错一次记一次；到阈值锁账号。成功登录在 _login_success 里清零。"""
    with connect_auth(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_auth_credentials
                   SET failed_attempts = failed_attempts + 1,
                       locked_until = CASE
                           WHEN failed_attempts + 1 >= %s
                           THEN now() + (%s || ' minutes')::interval
                           ELSE locked_until END,
                       updated_at = now()
                 WHERE user_id = %s
                """,
                (threshold, str(lock_minutes), user_id),
            )


def _login_success(settings: Settings, user_id: int) -> None:
    """登录成功：失败计数清零、锁解除。"""
    with connect_auth(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_auth_credentials
                   SET failed_attempts = 0, locked_until = NULL, updated_at = now()
                 WHERE user_id = %s
                """,
                (user_id,),
            )


def _decrypt_totp(settings: Settings, credential: tuple) -> str:
    """解开数据库里存的 TOTP 密钥，只在校验那一刻用。"""
    secret_enc = credential[2]
    if not secret_enc:
        raise AuthError("该账号未绑定动态口令")
    return SecretBox(settings.totp_encryption_key).decrypt(secret_enc)


_TOTP_FAIL_LIMIT = 5
_TOTP_LOCK_SECONDS = 15 * 60


def verify_step_up(settings: Settings, user_id: int, code: str) -> None:
    """
    校验当前用户为高危操作提交的动态口令。

    只查认证专属账号；账号未绑定 TOTP、已停用或口令错误一律拒绝。
    同一个时间计数器只能成功使用一次，成功后原子写入
    `totp_last_counter`；连续错误达到阈值后锁定 15 分钟。
    这里不返回布尔值，调用方不需要也不应该把失败细节吞掉。
    """
    uid = int(user_id)
    with connect_auth(settings) as conn:
        row = conn.execute(
            """
            SELECT c.totp_secret_enc, c.totp_enabled, u.is_active,
                   c.totp_last_counter, c.totp_failed_attempts, c.totp_locked_until
            FROM v3_auth_credentials c
            JOIN v3_users u ON u.id = c.user_id
            WHERE c.user_id = %s AND u.deleted_at IS NULL
            """,
            (uid,),
        ).fetchone()
        if not row or not bool(row[2]):
            raise AuthError("账号不存在或已停用")
        secret_enc, enabled, _active, last_counter, failed_attempts, locked_until = row
        if not bool(enabled) or not secret_enc:
            raise AuthError("该账号未绑定动态口令，不能执行高危操作")
        if locked_until is not None and locked_until > datetime.now(UTC):
            raise AuthError("动态口令错误次数过多，请 15 分钟后再试")

        secret = SecretBox(settings.totp_encryption_key).decrypt(str(secret_enc))
        counter = totp_match_counter(secret, str(code or ""))
        if counter is None:
            failures = int(failed_attempts or 0) + 1
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE v3_auth_credentials
                       SET totp_failed_attempts = %s,
                           totp_locked_until = CASE
                               WHEN %s >= %s THEN now() + make_interval(secs => %s)
                               ELSE totp_locked_until
                           END,
                           updated_at = now()
                     WHERE user_id = %s
                    """,
                    (failures, failures, _TOTP_FAIL_LIMIT, _TOTP_LOCK_SECONDS, uid),
                )
            if failures >= _TOTP_FAIL_LIMIT:
                raise AuthError("动态口令错误次数过多，已锁定 15 分钟")
            raise AuthError("动态口令错误或已过期")

        # 一次性消费：只有计数器比上次成功值更大才能写入。并发两个请求
        # 同时拿同一个码，只有一个 UPDATE 会命中，另一个按重放拒绝。
        with conn.transaction():
            cursor = conn.execute(
                """
                UPDATE v3_auth_credentials
                   SET totp_last_counter = %s,
                       totp_failed_attempts = 0,
                       totp_locked_until = NULL,
                       updated_at = now()
                 WHERE user_id = %s
                   AND (totp_last_counter IS NULL OR totp_last_counter < %s)
                """,
                (int(counter), uid, int(counter)),
            )
            if not getattr(cursor, "rowcount", 0):
                raise AuthError("动态口令已使用，请等待下一个口令")


# 查看密码属于高频高危动作，单独加一层失败计数：
# 5 次错误锁 15 分钟，避免有人拿已登录会话暴力试 6 位数字。
# 进程内计数，单实例够用；多副本部署时每个副本各一份，需要换 Redis 共享。
_REVEAL_FAIL_LOCK = threading.Lock()
_REVEAL_FAILS: dict[int, list[float]] = {}
_REVEAL_FAIL_LIMIT = 5
_REVEAL_FAIL_WINDOW = 15 * 60


def verify_reveal_step_up(settings: Settings, user_id: int, code: str) -> None:
    """查看密码前的动态口令校验：先查失败次数，再验码。"""
    uid = int(user_id)
    now = time.monotonic()
    with _REVEAL_FAIL_LOCK:
        recent = [
            item for item in _REVEAL_FAILS.get(uid, [])
            if now - item < _REVEAL_FAIL_WINDOW
        ]
        if recent:
            _REVEAL_FAILS[uid] = recent
        else:
            _REVEAL_FAILS.pop(uid, None)
        if len(recent) >= _REVEAL_FAIL_LIMIT:
            raise AuthError("动态口令尝试次数过多，请 15 分钟后再试")
    # 没绑定不是“输错”，不占用失败名额；否则用户先点几次再去绑定，
    # 会被自己的试探锁在外面 15 分钟。
    if not totp_status(settings, uid):
        raise AuthError("该账号未绑定动态口令，不能执行高危操作")
    try:
        verify_step_up(settings, uid, code)
    except AuthError:
        with _REVEAL_FAIL_LOCK:
            _REVEAL_FAILS.setdefault(uid, []).append(now)
        raise
    with _REVEAL_FAIL_LOCK:
        _REVEAL_FAILS.pop(uid, None)
