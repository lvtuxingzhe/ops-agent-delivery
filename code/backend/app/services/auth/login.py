"""三种登录方式的统一入口与校验。"""

from __future__ import annotations

import time

from ...core.config import Settings
from ...core.db import connect_app, connect_auth
from ...core.security import sha256_hex, totp_verify, verify_password
from ..config_service import is_enabled
from .accounts import get_user_with_roles
from .audit import _audit_login
from .core import AuthError
from .credentials import (
    _check_lockout,
    _decrypt_totp,
    _fetch_credential,
    _login_success,
    _record_password_failure,
)
from .session import _issue_session


def login(settings: Settings, *, method: str, username: str, password: str,
          totp_code: str, phone: str, sms_code: str,
          client_source: str) -> tuple[str, dict]:
    """
    三种登录的统一入口。返回 (token, user信息)。

    method: password | totp | sms。每种都先查平台开关 —— 关了的方式
    返回 403 级错误且【留痕】（跟「mode: real 一律 403 并留痕」同一个习惯）。
    """
    with connect_app(settings) as conn:
        methods = {
            "password": is_enabled(conn, "auth_password_enabled"),
            "totp": is_enabled(conn, "auth_totp_enabled"),
            "sms": is_enabled(conn, "auth_sms_enabled"),
        }
    if method not in methods:
        raise AuthError("不支持的登录方式")
    if not methods[method]:
        _audit_login(settings, "auth.login.blocked", username or phone or "?",
                     client_source, {"method": method, "reason": "method_disabled"})
        raise AuthError("该登录方式已被管理员停用")

    if method in ("password", "totp"):
        return _login_with_password(
            settings, method=method, username=username, password=password,
            totp_code=totp_code, client_source=client_source,
        )
    return _login_with_sms(settings, phone=phone, sms_code=sms_code,
                           client_source=client_source)


def _login_with_password(settings: Settings, *, method: str, username: str,
                         password: str, totp_code: str,
                         client_source: str) -> tuple[str, dict]:
    """方式① / ②：账号密码（② 再加一道 TOTP）。"""
    if not username or not password:
        raise AuthError("请输入用户名和密码")

    credential = _fetch_credential(settings, username)
    if credential is None:
        _audit_login(settings, "auth.login.failed", username, client_source,
                     {"method": method, "reason": "unknown_user"})
        raise AuthError("用户名或密码不对")

    user_id, stored_hash, _, locked_until, totp_enabled, _, is_active = credential
    if not is_active:
        _audit_login(settings, "auth.login.failed", username, client_source,
                     {"method": method, "reason": "user_disabled"})
        raise AuthError("账号不可用")
    _check_lockout(credential)

    # 没有密码哈希 = 该账号只能使用其他登录方式。
    # 明确拒绝，绝不把空值当成可登录。
    if not stored_hash:
        _audit_login(settings, "auth.login.failed", username, client_source,
                     {"method": method, "reason": "password_not_set"})
        raise AuthError("该账号未启用密码登录，请联系管理员")

    if not verify_password(password, stored_hash):
        _record_password_failure(
            settings, user_id,
            settings.login_lockout_threshold, settings.login_lockout_minutes,
        )
        _audit_login(settings, "auth.login.failed", username, client_source,
                     {"method": method, "reason": "bad_password"})
        raise AuthError("用户名或密码不对")

    # ---- 方式②：TOTP ----
    if method == "totp":
        if not totp_enabled:
            # 开着 TOTP 登录入口但这个号没绑定 → 提示去绑定，而不是含糊报错
            _audit_login(settings, "auth.login.failed", username, client_source,
                         {"method": method, "reason": "totp_not_bound"})
            raise AuthError("该账号未绑定动态口令，请先用账号密码登录后绑定")
        secret = _decrypt_totp(settings, credential)
        if not totp_verify(secret, totp_code):
            # 动态口令错也算一次认证失败：6 位码如果不计次，就能被无限试。
            _record_password_failure(
                settings, user_id,
                settings.login_lockout_threshold, settings.login_lockout_minutes,
            )
            _audit_login(settings, "auth.login.failed", username, client_source,
                         {"method": method, "reason": "bad_totp"})
            raise AuthError("动态口令不对")

    _login_success(settings, user_id)
    _audit_login(settings, "auth.login.success", username, client_source,
                 {"method": method})
    return _issue_session(settings, user_id)


def _login_with_sms(settings: Settings, *, phone: str, sms_code: str,
                    client_source: str) -> tuple[str, dict]:
    """方式③：手机号 + 短信验证码。校验逻辑与「码怎么发出去的」无关。"""
    if not phone or not sms_code:
        raise AuthError("请输入手机号和验证码")

    # 手机号 → 绑定的用户（走认证账号：phone_bindings 归它管）
    with connect_auth(settings) as conn:
        bound = conn.execute(
            "SELECT user_id FROM v3_phone_bindings WHERE phone = %s", (phone,)
        ).fetchone()
        if not bound:
            _audit_login(settings, "auth.login.failed", phone, client_source,
                         {"method": "sms", "reason": "phone_not_bound"})
            raise AuthError("该手机号未绑定账号")

        user_id = bound[0]
        # 取最新一条未用过的码。过期 / 用过 / 试错超限都当作「验证码不对」
        # 统一话术，不告诉攻击者「这个码到底存在不存在」。
        row = conn.execute(
            """
            SELECT id, code_hash, expires_at, used_at, attempts
            FROM v3_sms_codes
            WHERE phone = %s AND used_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (phone,),
        ).fetchone()

    code_ok = False
    if row and row[4] < 5 and row[2].timestamp() > time.time():
        # 恒定时间比较，避免「前几位对了就提前返回」的时序侧信道
        import hmac as _hmac
        code_ok = _hmac.compare_digest(str(row[1]), sha256_hex(sms_code))
        with connect_auth(settings) as conn:
            with conn.transaction():
                if code_ok:
                    # 「一次性」必须是原子消费：UPDATE 带 used_at IS NULL 条件，
                    # 并发两个请求只有一个能把这一行改成功，另一个 rowcount=0，
                    # 于是同一码不会被登录两次。
                    consumed = conn.execute(
                        "UPDATE v3_sms_codes SET used_at = now(), attempts = attempts + 1 "
                        "WHERE id = %s AND used_at IS NULL RETURNING 1",
                        (row[0],),
                    ).fetchone()
                    code_ok = consumed is not None
                else:
                    conn.execute(
                        "UPDATE v3_sms_codes SET attempts = attempts + 1 WHERE id = %s",
                        (row[0],),
                    )
    if not code_ok:
        _audit_login(settings, "auth.login.failed", phone, client_source,
                     {"method": "sms", "reason": "bad_or_expired_code"})
        raise AuthError("验证码不对或已过期")

    with connect_app(settings) as conn:
        user = get_user_with_roles(conn, user_id)
    if not user or not user["is_active"]:
        raise AuthError("账号不可用")

    _audit_login(settings, "auth.login.success", user["username"], client_source,
                 {"method": "sms", "phone_bound": True})
    return _issue_session(settings, user_id)
