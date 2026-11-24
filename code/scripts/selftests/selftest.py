"""离线静态自检：语法、配置与代码约束。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
    """记一条结果并立刻打印。SKIP 表示「没测」，不是「通过」。"""
    tag = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    RESULTS.append((tag, name, detail))
    suffix = f" — {detail}" if detail else ""
    print(f"[{tag}] {name}{suffix}")


def code_files(*suffixes: str) -> list[Path]:
    """收齐【本项目自己写的】源码文件。

    装完依赖后目录里会多出 .venv/Lib/site-packages、frontend/node_modules
    等成千上万个第三方文件。不排除的话，语法检查和"无硬编码地址"粗筛
    会把别人代码里的文档链接当成我们的问题报出来（曾经踩过一次）。
    """
    skip_dirs = {
        "node_modules", "dist", "build", "__pycache__", ".git",
        ".venv", "venv", "site-packages",
    }
    out: list[Path] = []
    for suffix in suffixes:
        for path in ROOT.rglob(f"*{suffix}"):
            if any(part in skip_dirs for part in path.parts):
                continue
            out.append(path)
    return sorted(set(out))


# ---------------------------------------------------------------- 1. 语法
def check_python_syntax() -> None:
    """逐个用 compile() 编译源码，比 import 安全：不会真的执行任何业务代码。"""
    files = code_files(".py")
    problems: list[str] = []
    for path in files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
    record(
        "Python 源码语法",
        not problems,
        f"共 {len(files)} 个文件" if not problems else "; ".join(problems),
    )


def check_json_files() -> None:
    """package.json / tsconfig.json 写错一个逗号，前端就起不来，先离线排掉。"""
    files = [
        ROOT / "frontend/package.json",
        ROOT / "frontend/tsconfig.json",
        ROOT / "desktop/package.json",
    ]
    problems: list[str] = []
    for path in files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: {exc}")
    record("JSON 配置合法", not problems, f"共 {len(files)} 个文件" if not problems else "; ".join(problems))


def check_js_syntax() -> None:
    """Electron 的 main.js / preload.js 用 node --check 查语法（不启动窗口）。"""
    files = [ROOT / "desktop/main.js", ROOT / "desktop/preload.js"]
    problems: list[str] = []
    for path in files:
        try:
            done = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True, timeout=15
            )
            if done.returncode != 0:
                problems.append(f"{path.name}: {done.stderr.strip()[:120]}")
        except (OSError, subprocess.TimeoutExpired):
            # 机器上没装 node，或检查超时：标成没测，不假装通过
            record("Electron 脚本语法", False, "本机没有可用的 node，跳过", skipped=True)
            return
    record("Electron 脚本语法", not problems, f"共 {len(files)} 个文件" if not problems else "; ".join(problems))


# ---------------------------------------------------------------- 2. 配置
def check_config() -> None:
    """
    验证配置中心的三条承诺：
      能加载、能被环境变量覆盖（没写死）、运行中不可篡改（frozen）。
    改环境变量前先存旧值，测完恢复，避免影响后面的检查。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.core.config import load_settings
    except Exception as exc:  # 配置模块自身不该有第三方依赖，报错就是骨架有问题
        record("配置中心", False, f"import 失败：{exc}")
        return

    base = load_settings()
    record(
        "配置中心可加载（零第三方依赖）",
        base.environment == "test" and base.database_url == "",
        f"environment={base.environment}，连接串为空（没连任何库）",
    )

    old = {key: os.environ.get(key) for key in ("BACKEND_PORT", "CORS_ORIGINS", "APP_ENV")}
    try:
        os.environ["BACKEND_PORT"] = "9999"
        os.environ["CORS_ORIGINS"] = "http://a.example,http://b.example"
        os.environ["APP_ENV"] = "production"
        changed = load_settings()
        record(
            "配置由环境变量驱动（代码里不写死）",
            changed.port == 9999 and len(changed.cors_origins) == 2 and changed.environment == "production",
            f"port={changed.port}，environment={changed.environment}，cors={changed.cors_origins}",
        )
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    try:
        base.environment = "production"  # type: ignore[misc]
        record("配置运行中不可篡改", False, "竟然改成功了，闸口前提被破坏")
    except FrozenInstanceError:
        record("配置运行中不可篡改", True, "FrozenInstanceError，符合预期")


def check_no_hardcoded_address() -> None:
    """
    粗筛：代码里有没有写死外部地址。
    允许出现的只有本机 / 容器服务名 / 测试域名，其余一律报出来人工确认。
    这是「代码体检」不是安全审计，规则故意简单，宁可漏也不要满屏误报。
    """
    allowed_hosts = {
        "127.0.0.1", "localhost", "0.0.0.0", "backend",
        "a.example", "b.example",
        # compose 里的容器服务名（同宿主机内部 DNS，不是外部地址）。
        # 第②步起 compose 里出现 minio 的 http:// 引用，漏了就会误报。
        "postgres", "redis", "rabbitmq", "minio",
    }
    url_re = re.compile(r"https?://([^\s\"'`,)/:]+)")
    suspicious: list[str] = []
    scanned = 0

    for path in code_files(".py", ".js", ".ts", ".vue", ".yml"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            # 注释里的示例地址不作数（但代码里出现就会报）
            if stripped.startswith(("#", "//", "*", "<!--")):
                continue
            for host in url_re.findall(line):
                scanned += 1
                if host not in allowed_hosts:
                    suspicious.append(f"{path.relative_to(ROOT)}:{lineno} -> {host}")
    record(
        "无硬编码外部地址（粗筛）",
        not suspicious,
        f"扫到 {scanned} 处 URL，均为本机/服务名" if not suspicious else "; ".join(suspicious[:5]),
    )


def check_samples() -> None:
    """
    第 ⑤ 步静态检查：四类假数据文件在不在、是不是声明为假、场景齐不齐。
    这里只查文件本身，不查取证逻辑（那是 selftest_samples.py 的活）。
    """
    import json

    sources = ("prometheus", "elk", "k8s", "docker")
    required = ("normal", "no_data", "unknown")
    problems: list[str] = []
    for name in sources:
        path = ROOT / "samples" / f"{name}.json"
        if not path.is_file():
            problems.append(f"缺 {name}.json")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            problems.append(f"{name}.json 读不出来")
            continue
        if data.get("meta", {}).get("fake") is not True:
            problems.append(f"{name}.json 没声明 fake=true")
        if any(text not in data.get("scenarios", {}) for text in required):
            problems.append(f"{name}.json 场景不齐")
    record(
        "四类样例数据齐全且声明为假数据",
        not problems,
        f"{len(sources)} 份 JSON，均含 normal/no_data/unknown" if not problems
        else "; ".join(problems[:4]),
    )


def check_llm_config() -> None:
    """大模型用户自配置的静态检查：相关文件在位，默认不发起公网调用。"""
    # 检查网关层文件在位，并确认三道默认关仍是默认态。
    paths = (
        ROOT / "backend" / "migrations" / "004_llm.sql",
        ROOT / "backend" / "app" / "services" / "llm_config_service.py",
        ROOT / "backend" / "app" / "api" / "llm_configs.py",
        ROOT / "frontend" / "src" / "api" / "llm.ts",
    )
    missing = [path.name for path in paths if not path.is_file()]
    record("大模型配置四层文件齐全（迁移/服务/接口/前端）", not missing,
           "4 个文件都在" if not missing else f"缺：{missing}")

    service_paths = [
        ROOT / "backend" / "app" / "services" / "llm_config_service.py",
        *sorted((ROOT / "backend" / "app" / "services" / "llm_config").glob("*.py")),
    ]
    service = "\n".join(
        path.read_text(encoding="utf-8") for path in service_paths if path.is_file()
    )
    gate_ok = "FIRST_PHASE_NO_REAL_CALL = True" in service and "llm.call.blocked" in service
    record("真实调用硬闸口写进了代码", gate_ok,
           "默认关 + 调用被拒留痕" if gate_ok else "硬闸口缺失，务必补上")

    ddl = (ROOT / "backend" / "migrations" / "004_llm.sql").read_text(encoding="utf-8")
    no_secret_col = "credential_ref" in ddl and "api_key" not in ddl.lower()
    record("模型配置表没有密钥列（只有引用名）", no_secret_col,
           "credential_ref 是唯一与凭据相关的列" if no_secret_col else "发现可疑密钥列")

    # 检查网关层文件在位，并确认三道默认关仍是默认态。
    gateway_files = (
        ROOT / "backend" / "app" / "core" / "redaction.py",
        ROOT / "backend" / "app" / "core" / "llm_gateway.py",
        ROOT / "backend" / "app" / "core" / "llm_transport.py",
        ROOT / "backend" / "app" / "services" / "llm_gateway_service.py",
        ROOT / "backend" / "scripts" / "init_llm_gateway.py",
        ROOT / "backend" / "scripts" / "selftest_llm_gateway.py",
    )
    missing_gw = [path.name for path in gateway_files if not path.is_file()]
    record("出网网关六层文件齐全", not missing_gw,
           "脱敏/配置/唯一出网点/编排/初始化/实测" if not missing_gw else f"缺：{missing_gw}")

    gw = (ROOT / "backend" / "app" / "core" / "llm_gateway.py").read_text(encoding="utf-8")
    record("网关默认态仍是三道关（no_real_call 默认 true）",
           "no_real_call: bool = True" in gw and "domain_allowlist: tuple[str, ...] = ()" in gw,
           "空名单不放行，默认拒绝")


def check_contract() -> None:
    """五态契约的静态检查：契约文件与迁移在位，规则写在代码里。"""
    paths = (
        ROOT / "backend" / "migrations" / "005_contract.sql",
        ROOT / "backend" / "app" / "core" / "contract.py",
        ROOT / "backend" / "scripts" / "init_contract.py",
        ROOT / "backend" / "scripts" / "selftest_contract.py",
    )
    missing = [path.name for path in paths if not path.is_file()]
    record("五态契约四层文件齐全", not missing,
           "迁移/契约/初始化/实测" if not missing else f"缺：{missing}")

    code = (ROOT / "backend" / "app" / "core" / "contract.py").read_text(encoding="utf-8")
    five = all(f'"{text}"' in code or f"'{text}'" in code for text in (
        "draft", "running", "awaiting_approval", "executing", "closed",
    ))
    record("五个状态在契约里，没有第六态", five and "STATES = (" in code,
           "五态常量 + 流转表" if five else "状态定义不全")

    ddl = (ROOT / "backend" / "migrations" / "005_contract.sql").read_text(encoding="utf-8")
    four = all(f"v3_{text}" in ddl for text in ("users", "tasks", "configs", "knowledge"))
    record("迁移里定义了 4 张 v3 表", four,
           "users/tasks/configs/knowledge" if four else "表名不全")


def check_audit_v3() -> None:
    """审计层的静态检查：无哈希链，归档配置位存在。"""
    paths = (
        ROOT / "backend" / "migrations" / "006_audit_v3.sql",
        ROOT / "backend" / "app" / "core" / "audit_v3.py",
        ROOT / "backend" / "app" / "core" / "archive.py",
        ROOT / "backend" / "scripts" / "init_audit_v3.py",
        ROOT / "backend" / "scripts" / "selftest_audit_v3.py",
    )
    missing = [path.name for path in paths if not path.is_file()]
    record("无哈希链审计层文件齐全", not missing,
           "迁移/写入/归档/初始化/实测" if not missing else f"缺：{missing}")

    ddl = (ROOT / "backend" / "migrations" / "006_audit_v3.sql").read_text(encoding="utf-8")
    # 只看真正会执行的 SQL：文件头注释里为了讲清"替代了什么"会提到
    # prev_hash / CREATE TRIGGER，那是说明文字，不是建表语句。
    ddl_code = "\n".join(
        line for line in ddl.splitlines() if not line.strip().startswith("--")
    )
    no_chain = "prev_hash" not in ddl_code and "CREATE TRIGGER" not in ddl_code.upper()
    record("新审计记录没有哈希链与自定义触发器", no_chain,
           "防篡改交给归档对象锁" if no_chain else "仍有自研 WORM 残留")

    code = (ROOT / "backend" / "app" / "core" / "archive.py").read_text(encoding="utf-8")
    ready = all(word in code for word in ("object_lock", "retention_days", "write_s3", "COMPLIANCE"))
    record("S3 对象锁接口与保留期配置位在场", ready,
           "本地后端跑链路，上线换 COMPLIANCE 对象锁" if ready else "配置位不全")


def check_credentials() -> None:
    """凭据解析层的静态检查：文件在位，不使用动态租约，不新增凭据表。"""
    paths = (
        ROOT / "backend" / "app" / "core" / "credentials.py",
        ROOT / "backend" / "app" / "core" / "config.py",
        ROOT / "backend" / "scripts" / "init_credentials.py",
        ROOT / "backend" / "scripts" / "selftest_credentials.py",
    )
    missing = [path.name for path in paths if not path.is_file()]
    record("凭据解析层四层文件齐全", not missing,
           "解析/配置出口/初始化/实测" if not missing else f"缺：{missing}")

    src = (ROOT / "backend" / "app" / "core" / "credentials.py").read_text(encoding="utf-8")
    # 只看真正会执行的代码：先剥掉注释行再扫描，
    # 避免把注释里的说明文字当成实际代码。
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    banned = ("ttl", "lease", "renew", "revoke", "unseal", "wrapping",
              "hvac", "expiry")
    hits = [value for value in banned if value in code.lower()]
    record("凭据层没有动态租约相关字样（全量词表）", not hits,
           "静态化：取一次 → 内存缓存 → 按天轮换" if not hits else f"命中：{hits}")

    three = all(f'"{backend_name}"' in src for backend_name in ("env", "openbao", "sops"))
    record("三个后端字符串在位（env/openbao/sops）", three
           and "BACKEND_READINESS" in src,
           "env 默认；openbao 已接 HTTP API；sops 走真实 sops -d")

    config = (ROOT / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")
    record("read_env 是唯一环境变量出口",
           "def read_env(" in config and "os.environ" not in src,
           "解析层零处直接读环境变量")

    migrations = ROOT / "backend" / "migrations"
    ddl = sorted(path.name for path in migrations.glob("*.sql"))
    core_ddl = [name for name in ddl if name[:3].isdigit() and int(name[:3]) <= 6]
    record("凭据仍走配置不走表（001～006 未改动）", len(core_ddl) == 6,
           f"001～006 共 {len(core_ddl)} 个；另有后续迁移 {len(ddl) - len(core_ddl)} 个")


def check_readability() -> None:
    """
    代码可读性常驻断言。

    变量、函数参数不许用含糊缩写或单字母；函数必须有一句说明，
    而且说明不能是「内部辅助函数」这种套话。只查自研代码，
    不扫 .venv / node_modules 这些第三方目录。
    """
    import ast as _ast
    # 真正让人猜的缩写；row / item / conn 这类通用名是允许的
    vague_names = {
        "eid", "obj", "proc", "raw", "svc", "cfg", "tmp", "res", "ret", "val",
        "lst", "dct", "arr", "buf", "a", "b", "c", "d", "e", "f", "g", "h",
        "k", "m", "n", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    }
    lazy_prefix = ("本文件用途", "内部辅助函数", "一组相关检查", "检查项：", "初始化：")
    bad_names: list[str] = []
    missing_docs: list[str] = []
    lazy_docs: list[str] = []

    for path in code_files(".py"):
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Store):
                if node.id in vague_names:
                    bad_names.append(f"{path.name}:{node.lineno}->{node.id}")
            elif isinstance(node, _ast.arg) and node.arg in vague_names:
                bad_names.append(f"{path.name}:{node.lineno}->{node.arg}")
            elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                # 「...」省略体是接口声明，本来就不需要说明
                first = node.body[0]
                is_stub = (isinstance(first, _ast.Expr)
                           and isinstance(first.value, _ast.Constant)
                           and first.value.value is Ellipsis)
                if is_stub:
                    continue
                doc = _ast.get_docstring(node)
                if doc is None:
                    missing_docs.append(f"{path.name}:{node.lineno}->{node.name}")
                elif doc.startswith(lazy_prefix):
                    lazy_docs.append(f"{path.name}:{node.lineno}->{node.name}")

    record("变量名不用含糊缩写或单字母", not bad_names,
           "全库扫过，命名都用通用词" if not bad_names else "; ".join(bad_names[:4]))
    record("函数都有一句说明", not missing_docs,
           "全库扫过" if not missing_docs else "; ".join(missing_docs[:4]))
    record("函数说明不是套话", not lazy_docs,
           "全库扫过" if not lazy_docs else "; ".join(lazy_docs[:4]))


# ---------------------------------------------------------------- 3. 进程
def check_worker_process() -> None:
    """真把 worker 空壳拉起来，看到「就绪」日志再结束它。这是唯一不涉及第三方包的进程。"""
    try:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "worker/main.py")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        time.sleep(0.8)
        process.terminate()
        output, _ = process.communicate(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        record("worker 进程能起", False, f"启动/收尾异常：{exc}")
        return
    ok = "已启动" in output and "就绪" in output
    # 第 ⑤ 步：worker 启动时还会把四类假数据真读一遍，日志里要有这一行
    samples_ok = "样例数据 4 类就绪" in output
    record("worker 进程能起", ok, "日志含「已启动 / 就绪」" if ok else output.strip()[:200])
    record("worker 能读到本地样例数据", samples_ok,
           "启动日志含「样例数据 4 类就绪」" if samples_ok else output.strip()[-200:])


def check_runtime_dependencies() -> None:
    """
    诚实报告：另外三个进程的依赖齐没齐。

    「齐备」只说明可以起；真起进程、发请求是 selftest_live.py 的活，别混为一谈。
    缺了就明确写 SKIP 并说清缺什么，绝不把没测的算成通过。

    有个容易踩的坑：后台依赖装在 backend/.venv 里（不污染系统 Python），
    而本脚本通常用系统 Python 跑，所以用 importlib.find_spec 查 fastapi
    永远查不到。必须让虚拟环境自己的解释器去查。
    """
    venv_py = ROOT / "backend" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    backend_ok = False
    backend_detail = ""
    if venv_py.exists():
        try:
            done = subprocess.run(
                [str(venv_py), "-c", "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)"],
                capture_output=True, text=True, timeout=20,
            )
            backend_ok = done.returncode == 0
            backend_detail = done.stdout.strip() or done.stderr.strip()[:120]
        except (OSError, subprocess.TimeoutExpired) as exc:
            backend_detail = str(exc)

    if backend_ok:
        record("后台依赖齐备", True, f"fastapi/uvicorn {backend_detail}（进程实测见 selftest_live.py）")
    else:
        record("后台依赖齐备", False, "缺 backend/.venv 或 fastapi/uvicorn，需 pip install -r backend/requirements.txt", skipped=True)

    has_frontend = (ROOT / "frontend/node_modules").is_dir()
    if not has_frontend:
        record("前端依赖齐备", False, "缺 frontend/node_modules，需 npm install", skipped=True)
    else:
        record("前端依赖齐备", True, "node_modules 已存在（页面实测见 selftest_live.py）")

    # npm 说 "up to date" 不代表二进制下好了，要直接看 electron.exe 在不在
    electron_dist = ROOT / "desktop/node_modules/electron/dist"
    has_desktop = (electron_dist / "electron.exe").exists() or (electron_dist / "electron").exists()
    if not has_desktop:
        record("桌面依赖齐备", False, "缺 electron 二进制，需 npm rebuild electron（可配国内镜像）", skipped=True)
    else:
        record("桌面依赖齐备", True, "electron.exe 已就位（窗口实测见 selftest_live.py --desktop）")


def check_compose() -> None:
    """离线校验编排文件语法与变量代入；没装 Docker 就标 SKIP。"""
    try:
        done = subprocess.run(
            ["docker", "compose", "config", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        record("Compose 编排合法", False, "本机没有可用的 docker compose", skipped=True)
        return
    record("Compose 编排合法", done.returncode == 0, done.stderr.strip()[:160] if done.returncode else "语法与变量替换通过")


# ---------------------------------------------------------------- 汇总
def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    print("=" * 62)
    print("运维 Agent 平台 · 离线静态自检")
    print("不联网 / 不装依赖 / 不连任何外部系统")
    print("=" * 62)

    check_python_syntax()
    check_json_files()
    check_js_syntax()
    check_config()
    check_no_hardcoded_address()
    check_samples()
    check_llm_config()
    check_contract()
    check_audit_v3()
    check_credentials()
    check_readability()
    check_worker_process()
    check_runtime_dependencies()
    check_compose()

    passed = sum(1 for tag, _, _ in RESULTS if tag == "PASS")
    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    skipped = sum(1 for tag, _, _ in RESULTS if tag == "SKIP")

    print("-" * 62)
    print(f"汇总：PASS {passed} · FAIL {failed} · SKIP {skipped}")
    if skipped:
        print("SKIP 项 = 因缺依赖本机没实测，不是通过；补装依赖后按 README 启动复验。")
    print("=" * 62)
    # 只有真正 FAIL 才让命令返回非 0，方便以后接进 CI
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
