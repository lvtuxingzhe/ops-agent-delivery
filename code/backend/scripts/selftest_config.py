"""配置页实测：配置驱动、权限与健康检查。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings
from app.core.db import connect_admin, connect_audit_reader
from app.core.security import hash_password
from app.main import app
from fastapi.testclient import TestClient

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def count_audit(event_type: str) -> int:
    """数审计记录里某类事件的条数（用审计只读账号，别污染权限）。"""
    with connect_audit_reader(SETTINGS) as conn:
        row = conn.execute(
            "SELECT count(*) FROM v3_audit_events WHERE event_type = %s",
            (event_type,),
        ).fetchone()
    return int(row[0])


load_dotenv()
SETTINGS = load_settings()
ADMIN = SETTINGS.admin_username
PASSWORD = SETTINGS.admin_password   # 只在脚本内存里用，绝不打印

# 三个样例数据源：留给后面步骤（⑤ 样例数据、⑦ 排查链路）当现成的配置，
# 名字带「样例-」前缀，重跑脚本时先删旧的同名行，保证可重复执行。
SAMPLES = [
    {"name": "样例-Prometheus", "kind": "prometheus", "adapter": "self",
     "endpoint": "http://127.0.0.1:9090", "credential_ref": "CRED_PROM_SAMPLE"},
    {"name": "样例-ELK", "kind": "elk", "adapter": "self",
     "endpoint": "http://127.0.0.1:9200", "credential_ref": "CRED_ELK_SAMPLE"},
    {"name": "样例-Obsidian", "kind": "obsidian", "adapter": "self",
     "endpoint": "D:/fake/obsidian/vault", "credential_ref": ""},
]
TESTER = "cfg_tester"   # 排查工程师角色，用来验「非管理员改不了配置」


def cleanup_before() -> None:
    """清掉上一轮残留（只删本脚本自己造的东西，别的配置一概不碰）。"""
    with connect_admin(SETTINGS) as admin:
        names = [text["name"] for text in SAMPLES]
        admin.execute(
            "DELETE FROM v3_configs WHERE namespace = 'datasource' AND key = ANY(%s)",
            (names,),
        )
        # tester 连带凭据一起清（v3 里用户与凭据是两张表）
        admin.execute(
            "DELETE FROM v3_auth_credentials WHERE user_id = "
            "(SELECT id FROM v3_users WHERE username = %s)", (TESTER,))
        admin.execute("DELETE FROM v3_users WHERE username = %s", (TESTER,))


def make_tester() -> None:
    """造一个「排查工程师」角色的账号，专门用来验权限门槛。"""
    pw_hash = hash_password(PASSWORD, rounds=1000)
    with connect_admin(SETTINGS) as admin:
        admin.execute(
            "INSERT INTO v3_users (username, display_name, roles) "
            "VALUES (%s, %s, ARRAY['investigator'])",
            (TESTER, "配置页权限测试号"),
        )
        uid = admin.execute(
            "SELECT id FROM v3_users WHERE username = %s", (TESTER,)
        ).fetchone()[0]
        admin.execute(
            "INSERT INTO v3_auth_credentials (user_id, password_hash) VALUES (%s, %s)",
            (uid, pw_hash),
        )


def login(client: TestClient, username: str) -> str:
    """用测试账号登录，拿一个令牌给后面的接口用。"""
    resp = client.post("/api/auth/login", json={
        "method": "password", "username": username, "password": PASSWORD,
    })
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败：{username} HTTP {resp.status_code}")
    return resp.json()["token"]


def auth_headers(token: str) -> dict:
    """把令牌包成请求头。"""
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    cleanup_before()
    make_tester()
    client = TestClient(app)
    print("=" * 66)
    print("第 ④ 步完成标志实测（配置页 · 改配置不用改代码）")
    print("=" * 66)

    admin_token = login(client, ADMIN)
    tester_token = login(client, TESTER)

    # ------------------------------------------------------------------
    # 1. 数据源：建 → 查 → 改 → 启停（全走接口，代码零变动）
    # ------------------------------------------------------------------
    for sample in SAMPLES:
        resp = client.post("/api/config/datasources", json={
            **sample, "enabled": True,
        }, headers=auth_headers(admin_token))
        ok = resp.status_code == 200
        record(f"新建数据源「{sample['name']}」", ok,
               f"HTTP {resp.status_code}" + ("" if ok else f" · {resp.text[:120]}"))
    prom = client.get("/api/config/datasources",
                      headers=auth_headers(admin_token)).json()["items"]
    prom = next(value for value in prom if value["name"] == "样例-Prometheus")

    # 响应字段白名单：出现任何白名单外的字段都算泄漏面扩大
    allowed_keys = {
        "id", "key", "name", "kind", "adapter", "endpoint", "credential_ref",
        "readonly", "enabled", "health_status", "health_detail",
        "health_checked_at", "created_at",
    }
    extra = set(prom.keys()) - allowed_keys
    record("数据源响应字段在白名单内", not extra,
           "没有多余字段（也没有密码列）" if not extra else f"多出来的字段：{extra}")
    record("新建数据源默认只读", prom["readonly"] is True,
           "readonly=true（当前版本强制）" if prom["readonly"] else "竟然可写")

    # 坏地址进不来：格式校验在服务层，本地检查、零连接
    resp = client.post("/api/config/datasources", json={
        "name": "坏地址", "kind": "prometheus", "adapter": "self",
        "endpoint": "这不是一个网址",
    }, headers=auth_headers(admin_token))
    record("地址格式不合法会被拒", resp.status_code == 400,
           f"HTTP {resp.status_code} · {resp.json().get('detail', '')[:60]}")

    # 同名冲突
    resp = client.post("/api/config/datasources", json={**SAMPLES[0]},
                       headers=auth_headers(admin_token))
    record("同名数据源会被拒", resp.status_code == 400, f"HTTP {resp.status_code}")

    # 改地址：改配置不用改代码的最小例子
    resp = client.put(f"/api/config/datasources/{prom['id']}", json={
        "name": prom["name"], "kind": "prometheus", "adapter": "self",
        "endpoint": "http://127.0.0.1:9091",        # 换个端口
        "credential_ref": "CRED_PROM_SAMPLE",
    }, headers=auth_headers(admin_token))
    new_endpoint = resp.json()["item"]["endpoint"] if resp.status_code == 200 else ""
    record("改地址立即生效（新值可查）", new_endpoint == "http://127.0.0.1:9091",
           f"查到 {new_endpoint or '（失败）'}")

    # 启停
    resp = client.post(f"/api/config/datasources/{prom['id']}/enabled",
                       json={"enabled": False}, headers=auth_headers(admin_token))
    ok = resp.status_code == 200 and resp.json()["item"]["enabled"] is False
    record("停用数据源", ok, f"enabled=false（HTTP {resp.status_code}）")
    client.post(f"/api/config/datasources/{prom['id']}/enabled",
                json={"enabled": True}, headers=auth_headers(admin_token))

    # ------------------------------------------------------------------
    # 2. 健康检查：Prometheus 真探测；这里故意指向未监听端口，应如实 failed
    # ------------------------------------------------------------------
    resp = client.post(f"/api/config/datasources/{prom['id']}/health-check",
                       headers=auth_headers(admin_token))
    item = resp.json()["item"]
    ok = (
        resp.status_code == 200
        and item["health_status"] == "failed"
        and str(item["health_detail"]).startswith("network_")
    )
    record("健康检查真探测并如实登记 failed（不显示为正常）", ok,
           f"{item['health_status']}：{item['health_detail'][:40]}")

    # ------------------------------------------------------------------
    # 3. 开关：改配置 → 行为真的变（不动一行代码）
    # ------------------------------------------------------------------
    # 3a. 关掉短信登录 → 登录页立刻不显示短信方式 → 真去登录被拒
    resp = client.post("/api/config/settings/auth_sms_enabled",
                       json={"value": "false"}, headers=auth_headers(admin_token))
    methods = client.get("/api/auth/methods").json()
    blocked = client.post("/api/auth/login", json={
        "method": "sms", "phone": "13800000000", "sms_code": "000000",
    })
    ok = (resp.status_code == 200 and methods.get("sms") is False
          and blocked.status_code == 401)
    record("关掉短信开关 → 登录页少一种方式且登录被拒", ok,
           f"methods.sms={methods.get('sms')} · 登录 HTTP {blocked.status_code}")
    # 恢复，别把开关留在关的状态影响别的测试
    client.post("/api/config/settings/auth_sms_enabled",
                json={"value": "true"}, headers=auth_headers(admin_token))

    # 3b. 网段：合法的能存，非法的进不来
    resp = client.post("/api/config/settings/allowed_cidrs",
                       json={"value": "10.0.0.0/8, 192.168.0.0/16"},
                       headers=auth_headers(admin_token))
    stored = client.get("/api/config/settings",
                        headers=auth_headers(admin_token)).json()["settings"]
    ok = resp.status_code == 200 and "10.0.0.0/8" in stored.get("allowed_cidrs", "")
    record("网段可以配置（合法 CIDR 存得进）", ok,
           f"当前：{stored.get('allowed_cidrs', '')}")
    resp_bad = client.post("/api/config/settings/allowed_cidrs",
                           json={"value": "这不是网段"},
                           headers=auth_headers(admin_token))
    record("非法网段被拒", resp_bad.status_code == 400, f"HTTP {resp_bad.status_code}")
    client.post("/api/config/settings/allowed_cidrs",
                json={"value": ""}, headers=auth_headers(admin_token))

    # 3c. 未知开关进不来（键白名单）
    resp = client.post("/api/config/settings/随便加的键",
                       json={"value": "true"}, headers=auth_headers(admin_token))
    record("未知开关键被拒（白名单）", resp.status_code == 400, f"HTTP {resp.status_code}")

    # ------------------------------------------------------------------
    # 4. 权限门槛：非管理员看得见、改不了
    # ------------------------------------------------------------------
    resp = client.get("/api/config/datasources", headers=auth_headers(tester_token))
    record("排查工程师能看数据源清单", resp.status_code == 200,
           f"HTTP {resp.status_code} · {len(resp.json().get('items', []))} 条")
    resp = client.post("/api/config/datasources", json=SAMPLES[0],
                       headers=auth_headers(tester_token))
    record("排查工程师不能建数据源", resp.status_code == 403, f"HTTP {resp.status_code}")
    resp = client.post("/api/config/settings/auth_sms_enabled",
                       json={"value": "false"}, headers=auth_headers(tester_token))
    record("排查工程师不能改开关", resp.status_code == 403, f"HTTP {resp.status_code}")
    resp = client.get("/api/config/settings", headers=auth_headers(tester_token))
    record("排查工程师看不了开关明细", resp.status_code == 403, f"HTTP {resp.status_code}")

    # ------------------------------------------------------------------
    # 5. 审计：每类配置动作都在审计记录里
    # ------------------------------------------------------------------
    for event_type, minimum, label in (
        ("config.datasource.created", 3, "数据源新建"),
        ("config.datasource.updated", 1, "数据源修改"),
        ("config.datasource.enabled", 2, "数据源启停"),
        ("config.datasource.health_checked", 1, "健康检查"),
        ("settings.changed", 2, "开关变更"),
    ):
        count = count_audit(event_type)
        record(f"审计可查：{label}事件", count >= minimum,
               f"审计记录里 {count} 条（≥{minimum}）")
    # 短信登录被拒那次也留了痕（auth.login.blocked）
    blocked_count = count_audit("auth.login.blocked")
    record("关掉的登录方式被拒也留审计", blocked_count >= 1,
           f"auth.login.blocked {blocked_count} 条")

    # ------------------------------------------------------------------
    # 收尾：测试账号删掉；三个样例数据源留着（后面步骤直接用）
    # ------------------------------------------------------------------
    with connect_admin(SETTINGS) as admin:
        admin.execute("DELETE FROM v3_users WHERE username = %s", (TESTER,))

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("（样例数据源 3 条已留在配置表里，供第 ⑤/⑦ 步使用；测试账号已删除）")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
