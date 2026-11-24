"""登录方式与自审批清单实测。"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings
from app.core.db import connect_app, connect_audit_reader, connect_auth
from app.main import app
from app.services import auth_service
from app.services.auth_service import AuthError
from fastapi.testclient import TestClient

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def count_audit(events: set[str], actor: str) -> int:
    """按事件类型 + 操作人查审计条数（用审计只读账号，不污染权限）。"""
    with connect_audit_reader(SETTINGS) as conn:
        row = conn.execute(
            "SELECT count(*) FROM v3_audit_events WHERE event_type = ANY(%s) AND actor = %s",
            (list(events), actor),
        ).fetchone()
    return int(row[0])


load_dotenv()
SETTINGS = load_settings()
ADMIN = SETTINGS.admin_username
PASSWORD = SETTINGS.admin_password       # 只在脚本内存里用，绝不打印
PHONE = SETTINGS.admin_phone


def cleanup_sms_codes() -> None:
    """
    让脚本可重复跑：清掉自己这个测试手机号造过的验证码。

    业务上有硬限频（同一手机号 1 分钟 1 条、24 小时最多 5 条）。
    一天里反复跑实测会攒满 5 条，第 6 次开始短信登录返回 429，
    原因是测试残留，不是功能故障。清理范围只限本脚本使用的假手机号。
    """
    if not PHONE:
        return
    from app.core.db import connect_admin

    with connect_admin(SETTINGS) as admin:
        admin.execute("DELETE FROM v3_sms_codes WHERE phone = %s", (PHONE,))


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    cleanup_sms_codes()
    client = TestClient(app)
    print("=" * 66)
    print("第 ③ 步完成标志实测（三种登录 + 自审批清单）")
    print("=" * 66)

    # ------------------------------------------------------------------
    # 0. 安全原语自证：RFC 6238 官方测试向量
    # ------------------------------------------------------------------
    from app.core.security import (
        _totp_at,
        hash_password,
        totp_now,
        verify_password,
    )

    # RFC 6238 附录 B 的官方向量（SHA1、8 位）。我们用 6 位，取 8 位值的末 6 位。
    # 注意向量里的 T 是「秒」，要先除以 30 才是计数器 —— 第一次就是栽在这。
    # 这组期望值已和独立第三方实现（pyotp）逐点对过，全一致。
    rfc_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # 即 "12345678901234567890" 的 Base32
    rfc_cases = [
        (59, 94287082), (1111111109, 7081804),
        (1234567890, 89005924), (2000000000, 69279037),
    ]
    ok = all(
        _totp_at(rfc_secret, text // 30) == f"{value % 10**6:06d}" for text, value in rfc_cases
    )
    record("TOTP 算法符合 RFC 6238 官方向量", ok,
           f"{len(rfc_cases)} 组向量全对（SHA1/30秒/6位）" if ok else "向量不匹配，算法写错了")

    # 密码哈希：正向过、反向拒、盐随机（两次哈希不相等）
    h1 = hash_password(PASSWORD, rounds=1000)
    ok = verify_password(PASSWORD, h1) and not verify_password("wrong", h1) \
        and h1 != hash_password(PASSWORD, rounds=1000)
    record("密码加盐哈希：正确密码过 / 错误密码拒 / 盐随机", ok, "PBKDF2-SHA256")

    # JWT：签发 → 校验；篡改 → 拒（密钥长度给足 32 字节，别触发库的告警）
    from app.core.security import TokenIssuer
    issuer = TokenIssuer("unit-test-secret-0123456789abcdef", 1, "t")
    good = issuer.issue(7)
    token_ok = issuer.verify(good) == 7
    ok = token_ok and issuer.verify(good[:-2] + "zz") is None
    record("会话令牌：签发校验通过 / 篡改被拒", ok, "JWT HS256")

    # ------------------------------------------------------------------
    # 完成标志 1/3：三种方式都能登录
    # ------------------------------------------------------------------
    resp = client.get("/api/auth/methods")
    methods = resp.json()
    record("登录页能拿到登录方式开关", resp.status_code == 200
           and all(methods.get(key) for key in ("password", "totp", "sms")),
           f"开关：{methods}")

    # ---- ① 纯账号密码 ----
    failed_before = count_audit({"auth.login.failed"}, ADMIN)
    resp = client.post("/api/auth/login", json={
        "method": "password", "username": ADMIN, "password": "故意错的密码"})
    rejected = resp.status_code == 401
    resp = client.post("/api/auth/login", json={
        "method": "password", "username": ADMIN, "password": PASSWORD})
    body = resp.json()
    token = body.get("token", "")
    ok = resp.status_code == 200 and token and body["user"]["username"] == ADMIN \
        and "admin" in body["user"]["roles"]
    record("① 账号密码登录成功", ok, f"HTTP {resp.status_code}，角色 {body.get('user', {}).get('roles')}")
    record("密码错误被拒且留痕", rejected and
           count_audit({"auth.login.failed"}, ADMIN) == failed_before + 1,
           "失败事件已进审计记录")

    # 令牌可用：/me 返回当前身份；伪造令牌被拒
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    me = resp.json()
    record("登录后 /auth/me 正常", resp.status_code == 200 and me["username"] == ADMIN
           and me["self_approval_switch"] is True, f"自审批豁免开关 = {me.get('self_approval_switch')}")
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer fake.token.here"})
    record("伪造令牌访问被拒", resp.status_code == 401, f"HTTP {resp.status_code}")

    # ---- ② 账号密码 + TOTP ----
    headers = {"Authorization": f"Bearer {token}"}

    # 前置清理：后面两段测的是「首次绑定」和「一分钟一条」，
    # 上一次运行留下的绑定状态与短信记录会让它们必然失败。
    # 只动测试号，不碰别的账号。
    with connect_auth(SETTINGS) as conn:
        conn.execute("DELETE FROM v3_sms_codes WHERE phone = %s", (PHONE,))
        conn.execute(
            "UPDATE v3_auth_credentials "
            "   SET totp_secret_enc = NULL, totp_enabled = FALSE, totp_bound_at = NULL "
            " WHERE user_id = (SELECT id FROM v3_users WHERE username = %s)",
            (ADMIN,),
        )
        conn.commit()

    resp = client.post("/api/auth/totp/bind/start", headers=headers)
    bind = resp.json()
    # SVG 输出带 <?xml ...?> 声明头，所以用「包含」而不是「开头是」判断
    has_qr = resp.status_code == 200 and "<svg" in bind.get("qr_svg", "") \
        and bind.get("otpauth_uri", "").startswith("otpauth://totp/")
    record("TOTP 绑定：二维码 + otpauth 链接返回", has_qr, "SVG 只在此一次回显")

    secret = bind.get("secret", "")
    code = totp_now(secret)  # 模拟手机 App 上显示的当前口令
    resp = client.post("/api/auth/totp/bind/confirm",
                       headers=headers, json={"code": code})
    record("TOTP 绑定：口令确认后启用", resp.status_code == 200, f"HTTP {resp.status_code}")

    # 用「密码 + 动态口令」重新登录
    code = totp_now(secret)
    resp = client.post("/api/auth/login", json={
        "method": "totp", "username": ADMIN, "password": PASSWORD, "totp_code": code})
    ok = resp.status_code == 200 and resp.json().get("token")
    record("② 密码+TOTP 登录成功", ok, f"HTTP {resp.status_code}")
    resp = client.post("/api/auth/login", json={
        "method": "totp", "username": ADMIN, "password": PASSWORD, "totp_code": "000000"})
    record("错误动态口令被拒", resp.status_code == 401, f"HTTP {resp.status_code}")

    # ---- ③ 手机号 + 短信（测试通道） ----
    resp = client.post("/api/auth/sms/send", json={"phone": PHONE})
    sent = resp.json()
    dev_code = sent.get("dev_code", "")
    ok = resp.status_code == 200 and len(dev_code) == 6
    record("短信测试通道发出验证码", ok,
           f"ttl={sent.get('ttl_seconds')}秒（码不打印；dev_code 仅测试通道可见）")

    resp = client.post("/api/auth/login", json={
        "method": "sms", "phone": PHONE, "sms_code": dev_code})
    ok = resp.status_code == 200 and resp.json().get("token")
    record("③ 手机号+短信登录成功", ok, f"HTTP {resp.status_code}")

    # 一次性：用过一次的码再用 → 拒
    resp = client.post("/api/auth/login", json={
        "method": "sms", "phone": PHONE, "sms_code": dev_code})
    record("验证码一次性（重放被拒）", resp.status_code == 401, f"HTTP {resp.status_code}")

    # 限频：一分钟内第二条 → 429
    resp = client.post("/api/auth/sms/send", json={"phone": PHONE})
    record("短信限频：一分钟内第二条被拒", resp.status_code == 429, f"HTTP {resp.status_code}")

    # ------------------------------------------------------------------
    # 完成标志 2/2：自己批自己的记录能在「自审批清单」里查到
    # ------------------------------------------------------------------
    with connect_app(SETTINGS) as conn:
        task_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO v3_tasks (id, trace_id, title, task_type, submitted_by) "
            "VALUES (%s, %s, %s, 'investigation', "
            "(SELECT id FROM v3_users WHERE username = %s))",
            (task_id, f"step3-{task_id}", "第③步自审批实测任务", ADMIN),
        )
        conn.commit()
        admin_id = conn.execute(
            "SELECT id FROM v3_users WHERE username = %s", (ADMIN,)).fetchone()[0]

    # 管理员自己提交、自己确认 → 放行且打标
    result = auth_service.issue_confirmation_token(
        SETTINGS, task_id=str(task_id), plan_fingerprint="step3-fp-v1",
        submitted_by=admin_id, confirmer=admin_id, step_up_method="totp")
    record("管理员自审批被放行且打标", result["is_self_approval"] is True,
           "is_self_approval=True")

    # 清单能查到（走接口：管理员角色）
    resp = client.get("/api/auth/self-approvals", headers=headers)
    items = resp.json().get("items", [])
    found = any(i["task_id"] == str(task_id) for i in items)
    record("自审批清单里能查到这条记录", resp.status_code == 200 and found,
           f"清单当前 {len(items)} 条，含本次任务")

    # 开关关掉 → 同样的操作被拒（且留痕）
    auth_service.set_setting(SETTINGS, "self_approval_enabled", "false", admin_id)
    rejected = False
    try:
        auth_service.issue_confirmation_token(
            SETTINGS, task_id=str(task_id), plan_fingerprint="step3-fp-v2",
            submitted_by=admin_id, confirmer=admin_id, step_up_method="totp")
    except AuthError:
        rejected = True
    record("豁免开关一键关掉后自审批被拒", rejected, "管理员也不行，恢复全员强制分离")

    # 普通角色（无豁免资格）即使开关开着也不能自批：建一个 oncall 测试号
    with connect_app(SETTINGS) as conn:
        conn.execute(
            "INSERT INTO v3_users (username, display_name, roles) "
            "VALUES ('fake_oncall', '值班测试号', ARRAY['oncall']) "
            "ON CONFLICT (username) DO NOTHING")
        oncall_id = conn.execute(
            "SELECT id FROM v3_users WHERE username = 'fake_oncall'").fetchone()[0]
        conn.commit()
    auth_service.set_setting(SETTINGS, "self_approval_enabled", "true", admin_id)
    rejected = False
    try:
        auth_service.issue_confirmation_token(
            SETTINGS, task_id=str(task_id), plan_fingerprint="step3-fp-v3",
            submitted_by=oncall_id, confirmer=oncall_id, step_up_method="password")
    except AuthError:
        rejected = True
    record("非管理员角色自审批被拒（开关开着也不行）", rejected,
           "值班角色无豁免资格，强制换人确认")
    # 恢复默认：开关开
    auth_service.set_setting(SETTINGS, "self_approval_enabled", "true", admin_id)

    # ------------------------------------------------------------------
    # 登录全留痕：审计记录里能按 fake_admin 数出成功/失败事件
    # ------------------------------------------------------------------
    success_count = count_audit({"auth.login.success"}, ADMIN)
    reject_count = count_audit({"approval.self_rejected"}, str(admin_id))
    record("登录与审批事件都在审计记录里", success_count >= 3 and reject_count >= 1,
           f"login.success×{success_count}，approval.self_rejected×{reject_count}")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
