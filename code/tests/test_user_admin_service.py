"""用户管理服务中不依赖真库的纯逻辑。

涉及事务、最后管理员保护、审计回滚等行为，放在
`backend/scripts/selftest_user_admin.py`（真库）和
`backend/scripts/selftest_user_admin_http.py`（真登录）里验收；
本文件只跑可以重复、无外部依赖的校验。
"""

from __future__ import annotations

import pytest
from app.services import user_admin_service as service
from app.services.user_admin_service import UserAdminError


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, role_codes=(), target=None):
        self.role_codes = role_codes
        self.target = target

    def execute(self, sql, params=None):
        if "v3_role_flags" in sql:
            return _Cursor([(code,) for code in self.role_codes])
        if "FROM v3_users WHERE id" in sql:
            return _Cursor([self.target] if self.target else [])
        if "FOR UPDATE" in sql:
            return _Cursor([(1,)])
        raise AssertionError(f"unexpected SQL in unit test: {sql}")


def test_generate_password_has_all_character_classes() -> None:
    password = service.generate_password()
    assert len(password) == service.PASSWORD_LENGTH
    assert any(ch.islower() for ch in password)
    assert any(ch.isupper() for ch in password)
    assert any(ch.isdigit() for ch in password)
    assert any(ch in "!@#$%^&*()-_=+[]{}" for ch in password)


def test_generate_password_is_not_repeated() -> None:
    values = {service.generate_password() for _ in range(20)}
    assert len(values) == 20


def test_generate_password_rejects_short_length() -> None:
    with pytest.raises(ValueError):
        service.generate_password(7)


@pytest.mark.parametrize("value", ["ab", "有中文", "a b", "a" * 33, "a/b"])
def test_username_rejects_invalid(value: str) -> None:
    with pytest.raises(UserAdminError) as exc:
        service._clean_username(value)
    assert exc.value.code == "USERNAME_INVALID"


@pytest.mark.parametrize("value", ["ops_alice", "ops-alice", "A1_test"])
def test_username_accepts_valid(value: str) -> None:
    assert service._clean_username(f"  {value}  ") == value


def test_display_name_limits() -> None:
    assert service._clean_display_name("  Alice  ") == "Alice"
    assert service._clean_display_name("", fallback="ops_alice") == "ops_alice"
    with pytest.raises(UserAdminError) as exc:
        service._clean_display_name("x" * 65)
    assert exc.value.code == "DISPLAY_NAME_INVALID"


def test_system_account_cannot_be_modified_from_page() -> None:
    """system- 前缀账号只给后台服务，页面改角色/停用/重置一律拒绝。"""
    with pytest.raises(UserAdminError) as exc:
        service._ensure_not_system_account(
            {"username": "system-alert"}, action="修改"
        )
    assert exc.value.code == "SYSTEM_ACCOUNT_PROTECTED"
    service._ensure_not_system_account({"username": "ops_alice"}, action="修改")


def test_delete_user_guards_system_and_self() -> None:
    """软删除前先挡系统账号和当前登录账号。"""
    with pytest.raises(UserAdminError) as exc:
        service._ensure_deletable_user({"id": 7, "username": "system-alert"}, 1)
    assert exc.value.code == "SYSTEM_ACCOUNT_PROTECTED"

    with pytest.raises(UserAdminError) as exc:
        service._ensure_deletable_user({"id": 1, "username": "admin"}, 1)
    assert exc.value.code == "SELF_PROTECTED"

    service._ensure_deletable_user({"id": 9, "username": "ops_alice"}, 1)


@pytest.mark.parametrize("value", ["12345678901", "1991234567", "199123456789"])
def test_phone_rejects_invalid(value: str) -> None:
    with pytest.raises(UserAdminError) as exc:
        service._clean_phone(value)
    assert exc.value.code == "PHONE_INVALID"


def test_phone_empty_is_none() -> None:
    assert service._clean_phone("   ") is None
    assert service._clean_phone("13812345678") == "13812345678"


def test_mask_phone_never_returns_full_number() -> None:
    assert service.mask_phone("13812345678") == "138****5678"
    assert service.mask_phone("12345678") == "12****78"
    assert service.mask_phone(None) == ""
    assert "12345678" not in service.mask_phone("12345678")


def test_validate_roles_rejects_unknown_and_empty() -> None:
    conn = _Conn(role_codes=["admin", "investigator"])
    with pytest.raises(UserAdminError) as exc:
        service._validate_roles(conn, [])
    assert exc.value.code == "ROLE_INVALID"
    with pytest.raises(UserAdminError) as exc:
        service._validate_roles(conn, ["admin", "root"])
    assert exc.value.code == "ROLE_INVALID"


def test_validate_roles_deduplicates_and_sorts() -> None:
    conn = _Conn(role_codes=["admin", "investigator", "approver"])
    assert service._validate_roles(conn, ["investigator", "admin", "investigator"]) == [
        "admin", "investigator",
    ]


def test_target_not_found() -> None:
    with pytest.raises(UserAdminError) as exc:
        service._target(_Conn(), 99)
    assert exc.value.status_code == 404
    assert exc.value.code == "USER_NOT_FOUND"


def test_serialize_user_does_not_expose_secrets_or_full_phone() -> None:
    row = (7, "ops_alice", "Alice", ["investigator"], True, "2026-11-21T00:00:00",
           True, False, "13812345678")
    result = service._serialize_user(row)
    assert result["phone_masked"] == "138****5678"
    assert result["phone_bound"] is True
    assert result["has_password"] is True
    assert result["totp_enabled"] is False
    assert "password_hash" not in result
    assert "totp_secret_enc" not in result
    assert "phone" not in result
