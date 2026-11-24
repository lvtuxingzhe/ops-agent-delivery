"""用户管理 HTTP 实测：登录、权限与账号操作。"""

from __future__ import annotations

import json
import random
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_user_admin  # noqa: E402

BASE = "http://127.0.0.1:8000/api"
PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"[PASS] {name}")
    else:
        FAILED += 1
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


class Response:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body

    def json(self) -> dict:
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return {}


def call(method: str, path: str, token: str = "", body: dict | None = None) -> Response:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Client-Kind": "web",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return Response(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read())


def login(username: str, password: str) -> Response:
    return call("POST", "/auth/login", body={
        "method": "password",
        "username": username,
        "password": password,
    })


def error_code(response: Response) -> str:
    body = response.json()
    detail = body.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code", ""))
    return ""


def main() -> int:
    load_dotenv()
    settings = load_settings()

    admin_login = login(settings.admin_username, settings.admin_password)
    check("管理员密码登录", admin_login.status == 200, f"HTTP {admin_login.status}")
    if admin_login.status != 200:
        print("管理员登录失败，停止验收")
        return 1
    admin_token = admin_login.json()["token"]
    admin_id = int(admin_login.json()["user"]["id"])

    roles = call("GET", "/admin/roles", admin_token)
    check("管理员读取角色清单", roles.status == 200 and len(roles.json()) >= 6,
          f"HTTP {roles.status}")
    users = call("GET", "/admin/users?page=1&size=20", admin_token)
    check("管理员读取用户列表", users.status == 200 and users.json().get("total", 0) >= 2,
          f"HTTP {users.status}")

    suffix = secrets.token_hex(4)
    username = f"a1_http_{suffix}"
    phone = f"139{random.randint(0, 99999999):08d}"
    created = call("POST", "/admin/users", admin_token, {
        "username": username,
        "display_name": "A1 HTTP 验收",
        "roles": ["investigator"],
        "phone": phone,
    })
    check("创建用户返回 201", created.status == 201, f"HTTP {created.status}")
    if created.status != 201:
        print("建用户失败，停止验收")
        return 1
    created_body = created.json()
    user_id = int(created_body["id"])
    initial_password = str(created_body["initial_password"])
    check("初始密码为 16 位", len(initial_password) == 16, f"len={len(initial_password)}")
    check("返回手机号已脱敏", "*" in str(created_body.get("phone_masked", "")))

    user_login = login(username, initial_password)
    check("新账号能登录", user_login.status == 200, f"HTTP {user_login.status}")
    if user_login.status != 200:
        print("新账号登录失败，停止验收")
        return 1
    user_token = user_login.json()["token"]
    check("新账号角色为登记角色", user_login.json()["user"]["roles"] == ["investigator"],
          str(user_login.json()["user"]["roles"]))

    denied = call("GET", "/admin/users", user_token)
    check("非 admin 访问用户管理被拒", denied.status == 403, f"HTTP {denied.status}")

    promoted = call("PATCH", f"/admin/users/{user_id}", admin_token, {"roles": ["admin"]})
    check("管理员改角色成功", promoted.status == 200 and promoted.json()["roles"] == ["admin"],
          f"HTTP {promoted.status}")

    effective = call("GET", "/admin/users", user_token)
    check("角色变更下一次请求生效", effective.status == 200, f"HTTP {effective.status}")

    disabled = call("PATCH", f"/admin/users/{user_id}", admin_token, {"is_active": False})
    check("管理员停用账号成功", disabled.status == 200 and disabled.json()["is_active"] is False,
          f"HTTP {disabled.status}")
    disabled_login = login(username, initial_password)
    check("停用后不能登录", disabled_login.status in (401, 403),
          f"HTTP {disabled_login.status}")

    reenabled = call("PATCH", f"/admin/users/{user_id}", admin_token, {"is_active": True})
    check("管理员启用账号成功", reenabled.status == 200 and reenabled.json()["is_active"] is True,
          f"HTTP {reenabled.status}")

    reset = call("POST", f"/admin/users/{user_id}/reset-password", admin_token)
    check("重置密码返回新明文", reset.status == 200 and len(str(reset.json().get("password", ""))) == 16,
          f"HTTP {reset.status}")
    new_password = str(reset.json().get("password", ""))
    old_login = login(username, initial_password)
    check("旧密码已失效", old_login.status in (401, 403), f"HTTP {old_login.status}")
    new_login = login(username, new_password)
    check("新密码能登录", new_login.status == 200, f"HTTP {new_login.status}")

    totp = call("POST", f"/admin/users/{user_id}/reset-totp", admin_token)
    check("重置 TOTP 成功", totp.status == 200 and totp.json().get("totp_enabled") is False,
          f"HTTP {totp.status}")

    bound = call("PUT", f"/admin/users/{user_id}/phone", admin_token, {"phone": phone})
    check("绑定手机号成功", bound.status == 200 and "*" in str(bound.json().get("phone_masked", "")),
          f"HTTP {bound.status}")
    listed = call("GET", f"/admin/users?q={username}", admin_token)
    found = next((item for item in listed.json().get("items", []) if int(item["id"]) == user_id), None)
    check("列表手机号脱敏", bool(found) and "*" in str(found.get("phone_masked", "")))
    unbound = call("DELETE", f"/admin/users/{user_id}/phone", admin_token)
    check("解绑手机号成功", unbound.status == 200, f"HTTP {unbound.status}")

    self_protect = call("PATCH", f"/admin/users/{admin_id}", admin_token, {"is_active": False})
    check("管理员不能停用自己", self_protect.status == 403
          and error_code(self_protect) == "SELF_PROTECTED",
          f"HTTP {self_protect.status} code={error_code(self_protect)}")

    # 清理临时账号：凭据/手机号/用户行删除，审计事件保留。
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            conn.execute("DELETE FROM v3_auth_credentials WHERE user_id = %s", (user_id,))
            conn.execute("DELETE FROM v3_phone_bindings WHERE user_id = %s", (user_id,))
            conn.execute("DELETE FROM v3_users WHERE id = %s", (user_id,))
            remaining = conn.execute("SELECT count(*) FROM v3_users").fetchone()[0]
    check("临时账号已清理", int(remaining) == 2, f"remaining={remaining}")

    print(f"\n结果：{PASSED} pass / {FAILED} fail")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
