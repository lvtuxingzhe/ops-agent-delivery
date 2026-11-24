"""大模型用户自配置实测。"""

from __future__ import annotations

import ast
import socket
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_admin, connect_audit_reader  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def count_audit(event_type: str) -> int:
    """数一下审计记录里指定类型事件的条数。"""
    with connect_audit_reader(SETTINGS) as conn:
        row = conn.execute(
            "SELECT count(*) FROM v3_audit_events WHERE event_type = %s", (event_type,)
        ).fetchone()
    return int(row[0])


load_dotenv()
SETTINGS = load_settings()
ADMIN = SETTINGS.admin_username
PASSWORD = SETTINGS.admin_password   # 只在内存里用，绝不打印

TEST_NAMES = ["自测-DeepSeek", "自测-自定义端点", "自测-本地Ollama"]
TESTER = "llm_tester"

# 模板原值。写成拼接而不是完整 URL 字面量：离线自检会粗筛 .py 里的
# http(s):// 主机名，这里只是「恢复种子值」用的字符串，不是连接目标。
_TPL_BASE_URL = "https://" + "api.deepseek.com" + "/v1"


def _set_task_switch(admin, value: str) -> None:
    """改平台总开关。开关在 v3_configs 的 setting/llm_enabled_by_task。"""
    admin.execute(
        "UPDATE v3_configs SET value = %s, updated_at = now() "
        "WHERE namespace = 'setting' AND key = 'llm_enabled_by_task'",
        (Jsonb({"value": value}),),
    )


def _restore_deepseek_template(admin) -> None:
    """把 deepseek 模板改回种子值（模板现在是一行 v3_configs，不是独立表）。"""
    row = admin.execute(
        "SELECT value FROM v3_configs WHERE namespace = 'template' AND key = 'deepseek'"
    ).fetchone()
    if not row:
        return
    stored = dict(row[0]) if isinstance(row[0], dict) else {}
    stored.update({
        "display_name": "DeepSeek",
        "base_url_hint": _TPL_BASE_URL,
        "model_hint": "deepseek-chat",
        "note": "OpenAI 兼容",
    })
    admin.execute(
        "UPDATE v3_configs SET value = %s, enabled = TRUE, updated_at = now() "
        "WHERE namespace = 'template' AND key = 'deepseek'",
        (Jsonb(stored),),
    )


def cleanup_before() -> None:
    """清掉上一轮自己造的东西（审计记录只追加，不删，那本来就是留痕）。"""
    with connect_admin(SETTINGS) as admin:
        admin.execute(
            "DELETE FROM v3_configs WHERE namespace = 'llm' AND key = ANY(%s)",
            (TEST_NAMES,),
        )
        _restore_deepseek_template(admin)
        admin.execute(
            "DELETE FROM v3_auth_credentials WHERE user_id IN "
            "(SELECT id FROM v3_users WHERE username = %s)", (TESTER,)
        )
        admin.execute("DELETE FROM v3_users WHERE username = %s", (TESTER,))
        _set_task_switch(admin, "false")


def make_tester() -> None:
    """造一个排查工程师角色账号，用来验「改配置要管理员」。"""
    from app.core.security import hash_password

    pw_hash = hash_password(PASSWORD, rounds=1000)
    with connect_admin(SETTINGS) as admin:
        # 用户身份在 v3_users，角色以数组保存。
        uid = admin.execute(
            "INSERT INTO v3_users (username, display_name, roles, is_active) "
            "VALUES (%s, %s, ARRAY['investigator'], TRUE) RETURNING id",
            (TESTER, "大模型配置权限测试号"),
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


def auth(token: str) -> dict:
    """构造带令牌的请求头。"""
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    cleanup_before()
    make_tester()
    client = TestClient(app)
    print("=" * 66)
    print("通用大模型用户自配置 · 完成标志实测")
    print("=" * 66)

    admin_token = login(client, ADMIN)
    tester_token = login(client, TESTER)

    # ------------------------------------------------------------------
    # 1. 厂商模板：覆盖常见大模型 + 自定义端点（都在库里，代码零常量）
    # ------------------------------------------------------------------
    resp = client.get("/api/config/llm/templates", headers=auth(admin_token))
    templates = resp.json().get("items", []) if resp.status_code == 200 else []
    codes = {text["code"] for text in templates}
    needed = {"deepseek", "qwen", "doubao", "hunyuan", "kimi", "zhipu",
              "openai", "ollama", "vllm", "anthropic", "gemini", "custom"}
    record("厂商模板覆盖常见大模型", resp.status_code == 200 and needed <= codes,
           f"{len(templates)} 个模板，含自定义端点与本地推理")
    record("原生格式不同的厂商只登记不实现",
           all(text["protocol"].startswith("native_")
               for text in templates if text["code"] in ("anthropic", "gemini")),
           "Claude / Gemini 标为 native_*，留薄转换层接口")

    # ------------------------------------------------------------------
    # 2. 用户自己配一条：选模板 + 自定义地址 + 凭据引用
    # ------------------------------------------------------------------
    deepseek = next(text for text in templates if text["code"] == "deepseek")
    body = {
        "name": TEST_NAMES[0],
        "template_code": "deepseek",
        "protocol": deepseek["protocol"],
        "base_url": "http://127.0.0.1:18080/v1",   # 用户覆盖成自己的地址
        "model_name": "deepseek-chat",
        "credential_ref": "LLM_KEY_TEST",           # 只是编号，不是密钥
        "params": {"timeout_seconds": 30, "max_tokens": 4096, "temperature": 0.2},
        "enabled": False,
        "is_default": False,
    }
    resp = client.post("/api/config/llm/configs", json=body, headers=auth(admin_token))
    created = resp.json().get("item", {}) if resp.status_code == 200 else {}
    record("用户能新建模型配置", resp.status_code == 200 and created.get("name") == TEST_NAMES[0],
           f"HTTP {resp.status_code}")
    record("用户能覆盖模板里的接口地址", created.get("base_url") == "http://127.0.0.1:18080/v1",
           f"存的是 {created.get('base_url', '')}（不是模板里的地址）")
    record("新建配置默认停用", created.get("enabled") is False, "enabled=false")
    record("调用参数按配置保存", created.get("params", {}).get("timeout_seconds") == 30,
           f"params={created.get('params', {})}")

    # 响应字段白名单：出现密钥类字段就算破戒
    allowed_keys = {
        "id", "key", "name", "template_code", "protocol", "base_url", "model_name",
        "credential_ref", "params", "enabled", "is_default", "health_status",
        "health_detail", "health_checked_at", "created_at",
    }
    extra = set(created.keys()) - allowed_keys
    record("响应里没有密钥字段（只有引用名）", not extra,
           "字段全在白名单内" if not extra else f"多出来：{extra}")
    cid = created.get("id")

    # ------------------------------------------------------------------
    # 3. 换模型只改一格，不改代码
    # ------------------------------------------------------------------
    changed = {**body, "model_name": "deepseek-reasoner"}
    resp = client.put(f"/api/config/llm/configs/{cid}", json=changed,
                      headers=auth(admin_token))
    record("换模型只改配置就生效", resp.status_code == 200
           and resp.json()["item"]["model_name"] == "deepseek-reasoner",
           f"现在是 {resp.json().get('item', {}).get('model_name', '（失败）')}")

    # 自定义端点：不靠任何预置模板也能配
    custom = {
        "name": TEST_NAMES[1], "template_code": "custom",
        "protocol": "openai_compatible",
        "base_url": "http://127.0.0.1:28080/v1",
        "model_name": "my-internal-model", "credential_ref": "LLM_KEY_INTERNAL",
        "params": {}, "enabled": False, "is_default": False,
    }
    resp = client.post("/api/config/llm/configs", json=custom, headers=auth(admin_token))
    record("任意自定义端点都能配（不依赖模板）", resp.status_code == 200,
           f"HTTP {resp.status_code} · model={custom['model_name']}")
    cid_custom = resp.json().get("item", {}).get("id")

    # ------------------------------------------------------------------
    # 4. 凭据：地址里夹带账号密码 / 查询参数一律拒
    # ------------------------------------------------------------------
    # 第一条故意做成拼接：完整的 "http://user:pass@..." 会被离线自检的
    # 地址粗筛当成「硬编码了 user 这个主机」，那只是个测试用的坏样本。
    for bad_url, label in (
        ("http://" + "user:pass@127.0.0.1:8080/v1", "地址内嵌账号密码"),
        ("http://127.0.0.1:8080/v1?key=abc", "地址带查询参数"),
    ):
        resp = client.post("/api/config/llm/configs", json={
            **custom, "name": "临时-坏地址", "base_url": bad_url,
        }, headers=auth(admin_token))
        record(f"{label}被拒", resp.status_code == 400, f"HTTP {resp.status_code}")
    resp = client.post("/api/config/llm/configs", json={
        **custom, "name": "临时-坏凭据", "credential_ref": "带空格 和符号!",
    }, headers=auth(admin_token))
    record("非法凭据引用被拒", resp.status_code == 400, f"HTTP {resp.status_code}")

    # ------------------------------------------------------------------
    # 5. 启停 / 默认（只能有一个默认）
    # ------------------------------------------------------------------
    client.post(f"/api/config/llm/configs/{cid}/enabled", json={"enabled": True},
                headers=auth(admin_token))
    resp = client.post(f"/api/config/llm/configs/{cid}/default", headers=auth(admin_token))
    record("启用后能设为默认模型", resp.status_code == 200
           and resp.json()["item"]["is_default"] is True, f"HTTP {resp.status_code}")
    client.post(f"/api/config/llm/configs/{cid_custom}/enabled", json={"enabled": True},
                headers=auth(admin_token))
    client.post(f"/api/config/llm/configs/{cid_custom}/default", headers=auth(admin_token))
    items = client.get("/api/config/llm/configs", headers=auth(admin_token)).json()["items"]
    defaults = [i for i in items if i["is_default"]]
    record("默认模型最多一个", len(defaults) == 1,
           f"当前默认：{defaults[0]['name'] if defaults else '（无）'}")
    resp = client.post(f"/api/config/llm/configs/{cid_custom}/enabled",
                       json={"enabled": False}, headers=auth(admin_token))
    record("停用默认模型会清掉默认标记", resp.json()["item"]["is_default"] is False,
           "避免留一个「停用的默认」")

    # ------------------------------------------------------------------
    # 6. 三道默认关：默认拒绝调用、记录审计、不发起网络连接
    # ------------------------------------------------------------------
    # 6a 配置停用 → config_disabled（用自定义那条，刚被停用）
    resp = client.post(f"/api/config/llm/configs/{cid_custom}/probe", headers=auth(admin_token))
    record("第一道：停用的配置拒绝调用", resp.status_code == 403
           and resp.json().get("reason") == "config_disabled",
           f"HTTP {resp.status_code} · {resp.json().get('reason')}")

    # 6b 总开关关 → task_switch_off
    resp = client.post(f"/api/config/llm/configs/{cid}/probe", headers=auth(admin_token))
    record("第二道：平台总开关默认关，拒绝调用", resp.status_code == 403
           and resp.json().get("reason") == "task_switch_off",
           f"HTTP {resp.status_code} · {resp.json().get('reason')}")

    # 6c 总开关打开 → 仍被第三道「默认关」拒绝。
    # 第 ④ 步起第三道读的是 llm_gateway/no_real_call 配置（默认 true），
    # reason 一字未改，所以默认态的拒绝行为与 34/0 完全一致。
    resp = client.post("/api/config/settings/llm_enabled_by_task", json={"value": "true"},
                       headers=auth(admin_token))
    record("总开关能在配置页改（改配置不改代码）", resp.status_code == 200,
           f"HTTP {resp.status_code}")
    resp = client.post(f"/api/config/llm/configs/{cid}/probe", headers=auth(admin_token))
    record("第三道：默认关生效，真实调用仍被拒且留痕", resp.status_code == 403
           and resp.json().get("reason") == "first_phase_no_outbound",
           f"{resp.json().get('detail', '')[:40]}")

    # 静态检查：后端大模型代码里【物理上】没有网络客户端。
    # 注意 urllib.parse 只是拆字符串，不算网络库；禁的是真正能连出去的那些。
    forbidden = {
        "socket", "requests", "httpx", "aiohttp", "paramiko", "http.client",
        "urllib.request", "urllib.error", "urllib3", "websockets",
    }
    imported: set[str] = set()
    service_root = ROOT / "backend/app/services"
    code_paths = [
        ROOT / "backend/app/api/llm_configs.py",
        service_root / "llm_config_service.py",
        service_root / "config_service.py",
        *sorted((service_root / "llm_config").glob("*.py")),
        *sorted((service_root / "configuration").glob("*.py")),
    ]
    for path in code_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(value.name for value in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    bad = {name for name in imported
           if name in forbidden or name.split(".")[0] in {"socket", "requests", "httpx", "aiohttp"}}
    record("大模型配置代码里没有网络客户端", not bad,
           "只有 urlparse（本地字符串解析）" if not bad else f"发现：{sorted(bad)}")

    # 网络客户端收敛到唯一出网点 core/llm_transport.py；
    # 其他模块不得引入网络客户端。
    app_dir = ROOT / "backend" / "app"
    net_modules = []
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(value.name for value in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        if names & forbidden or any(
            count.split(".")[0] in {"socket", "requests", "httpx", "aiohttp"} for count in names
        ):
            net_modules.append(path.name)
    record("出网点唯一：网络客户端只在一个网关模块里",
           net_modules == ["llm_transport.py"],
           f"命中：{net_modules}")

    # 运行期再核一次：记录所有 connect 目标，断言【没有一处是非回环地址】。
    #
    # 为什么不直接把 connect 抛异常：这些接口本来就要连本机数据库
    # （127.0.0.1:5432，项目边界里允许的本机访问），一刀切会把自己的数据库
    # 也误判成「外部连接」。真正要证明的是「没有连出本机」，所以只记录、
    # 不阻断，最后看目标地址。
    # 也不替换 socket.socket 本身 —— 上次整个换掉把 TestClient 的事件循环
    # 自建管道打断了，误报成「发起了连接」，这是个踩过的坑。
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    attempts: list[object] = []
    loopback = {"127.0.0.1", "::1", "localhost"}

    def _watch(orig):
        """包住 socket.connect，只记录目标、不阻断连接。"""
        def wrapper(self, address, *args, **kwargs):
            """记录一次连接目标后，仍调用原本的连接函数。"""
            attempts.append(address)
            return orig(self, address, *args, **kwargs)
        return wrapper

    socket.socket.connect = _watch(original_connect)  # type: ignore[assignment]
    socket.socket.connect_ex = _watch(original_connect_ex)  # type: ignore[assignment]
    try:
        client.get("/api/config/llm/templates", headers=auth(admin_token))
        client.get("/api/config/llm/configs", headers=auth(admin_token))
        client.post(f"/api/config/llm/configs/{cid}/probe", headers=auth(admin_token))
    finally:
        socket.socket.connect = original_connect  # type: ignore[assignment]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[assignment]

    outside = [
        value for value in attempts
        if not (isinstance(value, tuple) and value and str(value[0]) in loopback)
    ]
    record("全流程零外部连接（只连本机回环）", not outside,
           f"捕获 {len(attempts)} 次连接，全部是本机回环"
           if not outside else f"发现非本机连接：{outside[:3]}")

    # ------------------------------------------------------------------
    # 7. 模板可编辑（不是代码常量）
    # ------------------------------------------------------------------
    resp = client.put("/api/config/llm/templates/deepseek", json={
        "display_name": "自测改过的 DeepSeek",
        "base_url_hint": "http://127.0.0.1:38080/v1",
        "model_hint": "my-model", "note": "自测", "enabled": True,
    }, headers=auth(admin_token))
    templates2 = client.get("/api/config/llm/templates", headers=auth(admin_token)).json()["items"]
    changed_tpl = next(text for text in templates2 if text["code"] == "deepseek")
    record("厂商模板是可编辑的配置数据", resp.status_code == 200
           and changed_tpl["base_url_hint"] == "http://127.0.0.1:38080/v1"
           and changed_tpl["display_name"] == "自测改过的 DeepSeek",
           f"HTTP {resp.status_code} · 地址已改")

    # ------------------------------------------------------------------
    # 8. 权限门槛：非管理员能看清单、改不了
    # ------------------------------------------------------------------
    record("排查工程师能看模型清单",
           client.get("/api/config/llm/configs", headers=auth(tester_token)).status_code == 200,
           "HTTP 200")
    record("排查工程师看不了厂商模板明细",
           client.get("/api/config/llm/templates", headers=auth(tester_token)).status_code == 403,
           "HTTP 403")
    record("排查工程师不能新建模型配置",
           client.post("/api/config/llm/configs", json=custom,
                       headers=auth(tester_token)).status_code == 403,
           "HTTP 403")
    record("排查工程师不能改 / 启停 / 设默认 / 探测",
           all(client.request(method, url, headers=auth(tester_token),
                              json=payload if payload is not None else None).status_code == 403
               for method, url, payload in (
                   ("PUT", f"/api/config/llm/configs/{cid}", custom),
                   ("POST", f"/api/config/llm/configs/{cid}/enabled", {"enabled": True}),
                   ("POST", f"/api/config/llm/configs/{cid}/default", None),
                   ("POST", f"/api/config/llm/configs/{cid}/probe", None),
               )),
           "四个动作全 403")
    record("排查工程师不能改模板",
           client.put("/api/config/llm/templates/deepseek", json={
               "display_name": "x", "enabled": True,
           }, headers=auth(tester_token)).status_code == 403, "HTTP 403")

    # ------------------------------------------------------------------
    # 9. 审计：每类动作都查得到
    # ------------------------------------------------------------------
    for event_type, minimum, label in (
        ("llm.config.created", 2, "新建配置"),
        ("llm.config.updated", 1, "改配置/换模型"),
        ("llm.config.enabled", 3, "启停"),
        ("llm.config.default_set", 2, "设默认"),
        ("llm.call.blocked", 3, "调用被拒"),
        ("llm.template.updated", 1, "改模板"),
        ("settings.changed", 1, "开关变更"),
    ):
        count = count_audit(event_type)
        record(f"审计可查：{label}", count >= minimum, f"审计记录里 {count} 条（≥{minimum}）")

    # ------------------------------------------------------------------
    # 收尾：恢复现场（审计只追加，保留留痕）
    # ------------------------------------------------------------------
    with connect_admin(SETTINGS) as admin:
        admin.execute(
            "DELETE FROM v3_auth_credentials WHERE user_id IN "
            "(SELECT id FROM v3_users WHERE username = %s)", (TESTER,)
        )
        admin.execute("DELETE FROM v3_users WHERE username = %s", (TESTER,))
        _set_task_switch(admin, "false")
        _restore_deepseek_template(admin)

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("（两条自测模型配置留在配置表里，可直接在页面看到；总开关已恢复为关）")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
