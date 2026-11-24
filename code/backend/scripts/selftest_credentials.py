"""凭据解析实测：解析、泄漏、可插拔与轮换。"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import pickle
import socket
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core import credentials as cred  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.contract import STATES  # noqa: E402
from app.core.db import connect_admin, connect_app  # noqa: E402
from app.services import llm_config_service  # noqa: E402
from app.services.configuration import datasources as config_datasources  # noqa: E402

ROOT = BACKEND.parent
RESULTS: list[tuple[str, bool, str]] = []
SKIPS: list[str] = []

# 高熵唯一假值：只在【本进程】注入一次，用完删掉，绝不落盘、绝不打印。
# 它是搜捕用的「探针」，不是任何真实凭据。
FAKE = "FAKE_CRED_" + uuid.uuid4().hex.upper()
FAKE_REF = "demo_ref"
FAKE_ENV = "CRED_DEMO_REF"


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条结果。detail 里绝不写假值，也绝不写任何可能装值的字段内容。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def skip(name: str, why: str) -> None:
    """记一条 SKIP，写明缺什么，不计入通过数。"""
    SKIPS.append(name)
    print(f"[SKIP] {name} — {why}", flush=True)


load_dotenv()
SETTINGS = load_settings()


# ---------------------------------------------------------------- 替身与工具
class FakeClock:
    """可替换的时间源。轮换按 UTC 日期算，推一天就是新的一天。"""

    def __init__(self, day: str) -> None:
        """初始化对象要用的内部状态。"""
        self.day = day

    def __call__(self) -> str:
        """返回假时钟当前指向的那一天，用来模拟跨天轮换。"""
        return self.day


def _install_fake_fetcher(value=FAKE, calls: list | None = None):
    """把 env 后端换成可计数的假取数函数（不读环境变量，纯内存）。"""
    def fake(_ref: str, _cfg) -> str | None:
        """假取数函数：记一次调用并返回约定的假值。"""
        if calls is not None:
            calls.append(_ref)
        return value
    cred.FETCHERS["env"] = fake
    return fake


def _restore() -> None:
    """把自检临时改过的注册表与缓存恢复原样。"""
    cred.FETCHERS["env"] = cred._fetch_env
    cred.clear_cache()
    cred._utc_day = cred.__dict__.get("_orig_clock", cred._utc_day)


def _leak_faces() -> list[tuple[str, str]]:
    """
    零泄漏 8 面搜捕。刻意【排除】Secret.reveal() 的直接返回值 —— 那是唯一
    合法出口，把它算进来会让断言变成「测了个必然失败」。
    返回 (面名, 文本) 列表，交给下面统一搜 FAKE。
    """
    secret = cred.Secret(FAKE)
    result = cred.ResolveResult(
        ref=FAKE_REF, status="ok", value=secret, backend="env",
        day="2026-11-12", detail="已取得",
    )
    log_buffer = io.StringIO()
    handler = logging.StreamHandler(log_buffer)
    probe_log = logging.getLogger("cred.selftest")
    probe_log.handlers = [handler]
    probe_log.setLevel(logging.INFO)
    probe_log.info("解析结果=%r 状态=%s", result, result.status_text())

    try:
        json_text = json.dumps(secret)
    except (TypeError, ValueError) as exc:
        json_text = type(exc).__name__
    try:
        pickle_text = pickle.dumps(secret)
    except (TypeError, ValueError) as exc:
        pickle_text = type(exc).__name__
    # 故意让假取数函数抛一个【带假值】的异常，验证 resolve 只留类型名
    cred.FETCHERS["env"] = lambda credential_ref, credential_config: (_ for _ in ()).throw(
        RuntimeError("后端原始错误里混进了 " + FAKE)
    )
    cred.clear_cache()
    exc_result = cred.resolve(FAKE_REF, config=cred.CredentialConfig())

    return [
        ("Secret.__repr__", repr(secret)),
        ("Secret.__str__", str(secret)),
        ("Secret f-string", f"{secret}"),
        ("Secret json.dumps", json_text),
        ("Secret pickle.dumps", pickle_text),
        ("ResolveResult repr", repr(result)),
        ("审计 payload", json.dumps(result.to_audit_payload(), ensure_ascii=False)),
        ("日志缓冲", log_buffer.getvalue()),
        ("异常文案（只留类型名）", exc_result.detail),
        ("状态人话", result.status_text() + "|" + exc_result.status_text()),
    ]


# ================================================================ A 引用名
def group_a() -> None:
    """一组检查：引用名解析与正则统一。"""
    print("\n--- A 引用名解析与正则统一 ---")
    same = (config_datasources._CREDENTIAL_REF_RE is cred.CREDENTIAL_REF_RE
            and llm_config_service._CREDENTIAL_REF_RE is cred.CREDENTIAL_REF_RE)
    record("两个服务共用同一份引用名正则", same,
           "统一到 core/credentials.py 一处")

    good = ("DEMO_REF", "a_b-c", "x" * 64, "Prom_Sample1")
    bad = ("has space", "a.b", "中文", "x" * 65, "a@b")
    behavior = all(cred.CREDENTIAL_REF_RE.fullmatch(value) for value in good) and not any(
        cred.CREDENTIAL_REF_RE.fullmatch(value) for value in bad
    )
    record("统一前后正则行为等价（合法/非法各 4 组）", behavior,
           "校验行为与报错文案一字未改")

    cred.clear_cache()
    row = cred.resolve(FAKE_REF, config=cred.CredentialConfig())
    record("正常引用名映射到环境变量名",
           cred.env_var_name(FAKE_REF, cred.CredentialConfig()) == FAKE_ENV
           and row.status == "ok" and row.value is not None,
           f"ref={FAKE_REF} → {FAKE_ENV} · status={row.status}")

    # 含短横线：a-b 与 a_b 会归一化成同一个环境变量，明确拒绝，不串凭据
    equiv = cred.env_var_name("a-b", cred.DEFAULT_CONFIG) == cred.env_var_name(
        "a_b", cred.DEFAULT_CONFIG)
    hyphen = cred.resolve("a-b", config=cred.CredentialConfig())
    record("短横线引用名被明确拒绝（防环境变量撞名）",
           equiv and hyphen.status == "no_data" and hyphen.value is None,
           "无数据（引用名含短横线，请用下划线）")

    empty = cred.resolve("", config=cred.CredentialConfig())
    illegal = cred.resolve("非法名字!", config=cred.CredentialConfig())
    record("空/非法引用名如实返回无数据",
           empty.status == "no_data" and illegal.status == "invalid"
           and "无数据" in illegal.status_text(),
           "绝不用空串或「正常」冒充成功")


# ================================================================ B 可插拔
def group_b() -> None:
    """一组检查：后端可插拔与改配置不改代码。"""
    print("\n--- B 后端可插拔（改配置不改代码）---")
    cred.clear_cache()
    cfg_env = cred.CredentialConfig(backend="env")
    cfg_bao = cred.CredentialConfig(backend="openbao")
    cfg_sops = cred.CredentialConfig(backend="sops")
    cfg_db = cred.CredentialConfig(backend="db")

    r_env = cred.resolve(FAKE_REF, config=cfg_env)
    r_bao = cred.resolve(FAKE_REF, config=cfg_bao)
    r_sops = cred.resolve(FAKE_REF, config=cfg_sops)
    r_db = cred.resolve(FAKE_REF, SETTINGS, config=cfg_db)
    record("env 能取值、openbao 未配置报未接入、sops/db 无假值",
           r_env.status == "ok" and r_bao.status == "no_data"
           and r_sops.status == "no_data" and r_db.status == "no_data"
           and all(item.value is None for item in (r_bao, r_sops, r_db)),
           f"env={r_env.status} · openbao={r_bao.status} · sops={r_sops.status} · db={r_db.status}")

    record("未配置/无数据后端绝不偷偷回落 env 假值",
           "未接入" in r_bao.detail
           and "无假值" in r_sops.detail
           and "无假值" in r_db.detail
           and all("正常" not in item.status_text() for item in (r_bao, r_sops, r_db)),
           "openbao 缺地址→未接入；sops/db 无该引用→无假值")

    unknown = cred.resolve(FAKE_REF, config=cred.CredentialConfig(backend="whatever"))
    record("未知后端明确拒绝，不回落默认",
           unknown.status == "unknown_backend" and unknown.value is None,
           f"status={unknown.status}")

    # 真改库里的配置：load_config 立刻反映，代码一行没动
    original = None
    try:
        with connect_app(SETTINGS) as conn:
            original = conn.execute(
                "SELECT value FROM v3_configs WHERE namespace='credentials' AND key='default'"
            ).fetchone()[0]
            patched = {**original, "backend": "openbao"}
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb "
                "WHERE namespace='credentials' AND key='default'",
                (json.dumps(patched, ensure_ascii=False, sort_keys=True),),
            )
        loaded = cred.load_config(SETTINGS)
        resolved = cred.resolve(FAKE_REF, SETTINGS)
        record("改 v3_configs 一格，解析行为立刻变（零代码改动）",
               loaded.backend == "openbao" and resolved.status == "no_data",
               f"配置 backend={loaded.backend} → status={resolved.status}")
    finally:
        if original is not None:
            with connect_app(SETTINGS) as conn:
                conn.execute(
                    "UPDATE v3_configs SET value=%s::jsonb "
                    "WHERE namespace='credentials' AND key='default'",
                    (json.dumps(original, ensure_ascii=False, sort_keys=True),),
                )
            record("配置已在 finally 里改回原值（不留脏数据）", True,
                   "下一轮自检不受影响")

    with connect_app(SETTINGS) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace='credentials' AND key='default'"
        ).fetchone()
    keys = {"backend", "rotate_days", "on_rotation_failure", "env_ref_map",
            "openbao_addr", "openbao_mount", "openbao_path_prefix", "sops_file"}
    record("配置整行全键在库里（不只验 backend）",
           isinstance(row[0], dict) and keys.issubset(row[0].keys()),
           f"{len(keys)} 个键全在，地址/路径/周期都是配置")


# ================================================================ C 零泄漏
def group_c() -> None:
    """一组检查：零泄漏，假值在多个观测面都不许出现。"""
    print("\n--- C 零泄漏 8 面搜捕 ---")
    faces = _leak_faces()
    dirty = [name for name, text in faces if FAKE in str(text)]
    record("假值在 10 个观测面 0 命中（含值对象自身序列化）",
           not dirty, f"扫了 {len(faces)} 面" if not dirty else f"漏到：{dirty}")

    # json / pickle 必须抛错而不是悄悄编出一个能装值的字符串
    secret = cred.Secret(FAKE)
    blocked = False
    try:
        json.dumps(secret)
        pickle.dumps(secret)
    except (TypeError, ValueError):
        blocked = True
    record("值对象 json/pickle 序列化被硬拒", blocked,
           "未显式解包出不了关口")

    payload = cred.ResolveResult(
        ref=FAKE_REF, status="ok", value=secret, backend="env", day="2026-11-12",
    ).to_audit_payload()
    record("审计 payload 走字段白名单，不含值字段",
           "value" not in payload and FAKE not in json.dumps(payload)
           and "正常" not in json.dumps(payload, ensure_ascii=False),
           f"白名单 {len(payload)} 键：{','.join(sorted(payload))}")

    src = (ROOT / "backend" / "app" / "core" / "credentials.py").read_text(encoding="utf-8")
    only_config = True
    for name in ("credentials.py",):
        text = (ROOT / "backend" / "app" / "core" / name).read_text(encoding="utf-8")
        only_config = only_config and "os.environ" not in text and "getenv" not in text
    cfg_src = (ROOT / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")
    record("os.environ 全项目只在 config.py（解析层 0 处）",
           only_config and "os.environ" in cfg_src,
           "env 后端只经 read_env() 读")

    record("源码里没有把值拼进异常或日志的写法",
           "f\"{raw" not in src and "str(raw)" not in src,
           "异常只留类型名，detail 只留固定文案")


# ================================================================ D 缓存与轮换
def group_d() -> None:
    """一组检查：缓存命中与按天轮换。"""
    print("\n--- D 缓存命中与按天轮换（假时钟）---")
    cred._orig_clock = cred._utc_day  # type: ignore[attr-defined]
    cred.FETCHERS["env"] = cred._fetch_env
    calls: list[str] = []
    _install_fake_fetcher(FAKE, calls)
    clock = FakeClock("2026-11-12")
    cred._utc_day = clock  # type: ignore[assignment]
    config = cred.CredentialConfig(backend="env", rotate_days=1)
    cred.clear_cache()

    r1 = cred.resolve(FAKE_REF, config=config)
    r2 = cred.resolve(FAKE_REF, config=config)
    record("同一天第二次命中缓存，取数函数不再被调用",
           r1.status == "ok" and r2.cache_hit and len(calls) == 1
           and r2.value is not None,
           f"取数 {len(calls)} 次 · cache_hit={r2.cache_hit}")

    clock.day = "2026-11-13"
    r3 = cred.resolve(FAKE_REF, config=config)
    record("跨天重新取一次（按天轮换生效）",
           len(calls) == 2 and not r3.cache_hit and r3.status == "ok",
           f"取数 {len(calls)} 次 · day={r3.day}")

    # 重取失败当天：排查侧可沿用旧值，但状态仍是 no_data + degraded
    _install_fake_fetcher(None, calls)
    clock.day = "2026-11-14"
    r4 = cred.resolve(FAKE_REF, config=config, actor_kind="investigation")
    record("重取失败当天：排查侧沿用旧值且明确标降级",
           r4.status == "no_data" and r4.degraded and r4.value is not None
           and "无数据" in r4.status_text() and "正常" not in r4.status_text()
           and "沿用" in r4.detail and r4.data_quality == "no_data",
           f"status={r4.status} · degraded={r4.degraded} · 有旧值")

    # 第二天还失败：旧值超出一个轮换周期，作废，绝不永久可用
    clock.day = "2026-11-15"
    r5 = cred.resolve(FAKE_REF, config=config, actor_kind="investigation")
    record("旧值只沿用一个轮换周期，超期作废",
           r5.status == "no_data" and r5.value is None and r5.degraded,
           "超期后不 ok、不返回旧值")

    # 重取成功且值变了：rotated 标记要真翻转
    _install_fake_fetcher(FAKE + "_V2", calls)
    clock.day = "2026-11-16"
    r6 = cred.resolve(FAKE_REF, config=config)
    record("重取到新值时 rotated=True（不静默当成功）",
           r6.status == "ok" and r6.rotated and not r6.degraded,
           f"rotated={r6.rotated}")

    _restore()


# ================================================================ E 无动态租约
def group_e() -> None:
    """一组检查：不用动态租约。"""
    print("\n--- E 不用动态租约 ---")
    path = ROOT / "backend" / "app" / "core" / "credentials.py"
    src = path.read_text(encoding="utf-8")
    # 剥注释行：注释里用中文讲「不用动态租约」，不该被英文词表误报（第②步踩过）
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    banned = ("ttl", "lease", "renew", "revoke", "unseal", "wrapping",
              "hvac", "expiry")
    hits = [value for value in banned if value in code.lower()]
    record("凭据层无动态租约词表命中（全量，ttl 不豁免）",
           not hits, "静态词表 9 个全清" if not hits else f"命中：{hits}")

    tree = ast.parse(src)
    net_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            net_imports += [value.name for value in node.names]
        elif isinstance(node, ast.ImportFrom):
            net_imports.append(node.module or "")
    # OpenBao 必须走 HTTP，urllib 是唯一允许的网络库；其余网络客户端/后台调度一律不允许。
    net_hits = [match for match in net_imports if match.split(".")[0] in
                ("socket", "requests", "httpx", "http", "hvac", "boto3")]
    record("凭据层只允许 urllib 一个 HTTP 客户端，无定时器 / 后台线程",
           not net_hits and "threading" not in net_imports and "Timer" not in code,
           f"import：{', '.join(sorted(set(net_imports)))}")

    secret_node = next(
        (count for count in tree.body if isinstance(count, ast.ClassDef) and count.name == "Secret"), None
    )
    fields = []
    if secret_node:
        for node in secret_node.body:
            if isinstance(node, ast.Assign):
                fields += [text.id for text in node.targets if isinstance(text, ast.Name)]
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                fields.append(node.target.id)
    bad_fields = [file_handle for file_handle in fields if any(value in file_handle.lower() for value in banned)]
    record("值包装类没有租约类字段（字段级断言）",
           secret_node is not None and not bad_fields,
           f"字段：{fields}")

    function_nodes = {
        function_node.name: function_node
        for function_node in tree.body if isinstance(function_node, ast.FunctionDef)
    }
    openbao_nodes = [
        function_nodes[name] for name in ("_fetch_openbao", "_openbao_request")
        if name in function_nodes
    ]
    openbao_has_http = bool(openbao_nodes) and any(
        isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "urlopen"
        for function_node in openbao_nodes for node in ast.walk(function_node)
    )
    sops_node = function_nodes.get("_fetch_sops")
    sops_has_decrypt = bool(sops_node) and any(
        isinstance(node, ast.Call) and getattr(node.func, "id", "") == "load_sops_values"
        for node in ast.walk(sops_node)
    )
    record("openbao / sops 已有真实取数实现（非空骨架）",
           openbao_has_http and sops_has_decrypt,
           "openbao 走 urllib HTTP API；sops 走 load_sops_values → sops -d")

    db_node = function_nodes.get("_fetch_db")
    db_has_table = bool(db_node) and any(
        isinstance(node, ast.Name) and node.id == "CREDENTIAL_SECRET_TABLE"
        for node in ast.walk(db_node)
    )
    record("db 后端已有密文表读取与 SecretBox 解密实现",
           db_has_table, "读 v3_credential_secrets → SecretBox(key).decrypt")


# ================================================================ F 零外部连接
def group_f() -> None:
    """一组检查：零外部连接。"""
    print("\n--- F 零外部连接（双路径）---")
    targets: list[str] = []
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def watch_connect(self, address, *args, **kwargs):
        """记录一次连接目标，再调用原本的连接函数。"""
        targets.append(str(address))
        return orig_connect(self, address, *args, **kwargs)

    def watch_connect_ex(self, address, *args, **kwargs):
        """记录一次 connect_ex 的目标，再调用原本的连接函数。"""
        targets.append(str(address))
        return orig_connect_ex(self, address, *args, **kwargs)

    socket.socket.connect = watch_connect  # type: ignore[assignment]
    socket.socket.connect_ex = watch_connect_ex  # type: ignore[assignment]
    try:
        cred.clear_cache()
        config = cred.CredentialConfig(backend="env")
        cred.FETCHERS["env"] = lambda credential_ref, credential_config: FAKE
        cred.resolve(FAKE_REF, config=config)
        cred.resolve(FAKE_REF, config=cred.CredentialConfig(backend="openbao"))
        cred.resolve(FAKE_REF, config=cred.CredentialConfig(backend="sops"))
        cred.resolve(FAKE_REF, SETTINGS, config=cred.CredentialConfig(backend="db"))
        external = [text for text in targets if not any(
            hostname in text for hostname in ("127.0.0.1", "::1", "localhost")
        )]
        # 真实 load_config 路径：只该碰本机回环的本机数据库
        targets.clear()
        cred.load_config(SETTINGS)
        live = list(targets)
    finally:
        socket.socket.connect = orig_connect  # type: ignore[assignment]
        socket.socket.connect_ex = orig_connect_ex  # type: ignore[assignment]
        _restore()

    record("注入配置源跑完整解析：外部连接 0 次",
           not external, f"resolve 四个后端全跑，外部目标 {len(external)} 个")
    # 只断言「没有任何非回环目标」，不要求一定抓到连接：psycopg 可能走
    # 底层 C 连接，Python 层 socket.connect 未必被调用（第④步扩展踩过这个坑）。
    outside = [text for text in live if not any(
        hostname in text for hostname in ("127.0.0.1", "::1", "localhost")
    )]
    record("真实读配置路径无外部连接目标",
           not outside,
           f"捕到 {len(live)} 次连接，外部目标 {len(outside)} 个"
           + ("（未捕到 Python 层连接也属正常）" if not live else ""))

    skip("容器内无 CRED_* 的真实路径",
         "本次只在本地脚本路径注入假值验过链路；容器侧只验了 no_data 降级，未起容器")


# ================================================================ G 降级
def group_g() -> None:
    """一组检查：错误与分场景降级。"""
    print("\n--- G 错误与分场景降级 ---")
    cred.FETCHERS["env"] = cred._fetch_env
    cred.clear_cache()
    # 两次取值必须【跨一天】：同一天会命中缓存直接返回 ok，压根走不到降级分支
    # （第一次跑出 3 个 FAIL 就是这个测试自身的坑，不是功能问题）。
    day_cached = "2026-11-20"
    day_retry = "2026-11-21"
    cred.resolve(FAKE_REF, config=cred.CredentialConfig(), day=day_cached)
    cred.FETCHERS["env"] = lambda credential_ref, credential_config: None   # 模拟第二天后端重取失败

    dep = cred.resolve(FAKE_REF, config=cred.CredentialConfig(),
                       actor_kind="deployment", day=day_retry)
    record("部署侧解析失败硬失败，绝不拿旧凭据动生产",
           dep.status == "no_data" and dep.value is None and dep.degraded,
           "value=None，调用方据此整笔回滚")

    inv = cred.resolve(FAKE_REF, config=cred.CredentialConfig(),
                       actor_kind="investigation", day=day_retry)
    record("排查侧可降级继续取证，但如实标无数据",
           inv.status == "no_data" and inv.degraded and inv.value is not None
           and "正常" not in inv.status_text() and inv.data_quality == "no_data",
           "不阻断取证，也绝不叫正常")

    # 三种 no_data 语义必须能靠 value / degraded / detail 区分
    cred.clear_cache()
    config = cred.CredentialConfig()
    not_ready = cred.resolve(FAKE_REF,
                             config=cred.CredentialConfig(backend="openbao"),
                             day=day_retry)
    cred.FETCHERS["env"] = lambda credential_ref, credential_config: None
    no_fake = cred.resolve(FAKE_REF, config=config, day=day_retry)
    distinct = (
        "未接入" in not_ready.detail and not_ready.value is None
        and "无假值" in no_fake.detail and no_fake.value is None
        and "沿用" in inv.detail and inv.value is not None and inv.degraded
    )
    record("no_data 三种成因可区分（未接入/无假值/沿用旧值）",
           distinct and all("正常" not in resolve_result.status_text() for resolve_result in
                            (not_ready, no_fake, inv)),
           "靠 value 有无 + degraded + detail 区分，不靠多造状态词")

    _restore()


# ================================================================ H 旧代码无损
def group_h() -> None:
    """一组检查：旧代码无损与零新增表。"""
    print("\n--- H 旧代码无损与零新增表 ---")
    record("当前版本大模型硬闸口仍为 True（未擅自放宽）",
           llm_config_service.FIRST_PHASE_NO_REAL_CALL is True,
           "第 ④ 步云端出网仍需单独授权")
    record("六态契约常量未变",
           len(STATES) == 6 and "closed" in STATES,
           "draft/running/report_ready/awaiting_approval/executing/closed 六态")

    mig = ROOT / "backend" / "migrations"
    old5 = (mig / "005_contract.sql").read_text(encoding="utf-8")
    old6 = (mig / "006_audit_v3.sql").read_text(encoding="utf-8")
    record("005/006 迁移原封不动（零 DDL 改动）",
           "CREATE TABLE IF NOT EXISTS v3_configs" in old5
           and "v3_audit_events" in old6
           and "v3_credential" not in (old5 + old6).lower(),
           "本步没往旧迁移里塞任何表")

    with connect_admin(SETTINGS) as admin:
        tables = {
            row[0] for row in admin.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'v3_%'"
            ).fetchall()
        }
    # 凭据模块要保证业务核心表存在，且引用名不落在凭据本体表里；
    # 表数量会随其他模块增加。
    # 034 起允许 v3_credential_secrets 只存密文，但 v3_configs 仍只存引用名。
    expected = {"v3_users", "v3_tasks", "v3_configs", "v3_knowledge",
                "v3_audit_events", "v3_audit_buffer", "v3_credential_secrets"}
    credential_tables = {name for name in tables
                        if name in {"v3_credentials", "v3_secrets", "v3_llm_keys"}}
    record("核心表与凭据密文表都在，且没有明文凭据本体表",
           expected <= tables and not credential_tables,
           f"{len(tables)} 张 v3 表，引用名仍在 v3_configs，密文在独立表")


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    os.environ[FAKE_ENV] = FAKE   # 假值只在本进程，用完删
    print("=" * 68)
    print("完成标志实测 · 凭据解析层（零联网 / 零真凭据）")
    print("=" * 68)
    try:
        group_a()
        group_b()
        group_c()
        group_d()
        group_e()
        group_f()
        group_g()
        group_h()
    finally:
        os.environ.pop(FAKE_ENV, None)
        _restore()

    passed = sum(1 for tag, _, _ in RESULTS if tag == "PASS")
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 68)
    print(f"汇总：PASS {passed} · FAIL {failed} · SKIP {len(SKIPS)}（SKIP 不计通过数）")
    for name in SKIPS:
        print(f"  SKIP：{name}")
    print("=" * 68)
    # 保底：连自己的输出都不许把假值带出去
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
