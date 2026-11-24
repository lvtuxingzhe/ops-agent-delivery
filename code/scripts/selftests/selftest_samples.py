"""样例数据排查流程实测。"""

from __future__ import annotations

import ast
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))   # 让 worker/sample_provider.py 可被 import

from sample_provider import (  # noqa: E402
    REQUIRED_SCENARIOS,
    SOURCES,
    STATUS_TEXT,
    SampleDataError,
    available_scenarios,
    load_fixture,
    query,
    wrap_untrusted_text,
)

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 结果并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------------------
# 静态检查：排查侧的取证模块「物理上」没有网络和写能力
# ---------------------------------------------------------------------------
def check_provider_is_readonly() -> None:
    """
    用 AST 把 sample_provider.py 拆开看，而不是 grep 字符串：
      * 不许 import 网络 / 进程 / 数据库 / 命令执行相关模块；
      * 不许出现写文件、eval / exec / __import__ 这类动态入口；
      * 不许 import 后台的代码（排查侧和对外服务必须分家）。
    这是第 ⑥ 步「物理上没装写工具」的提前落地，不靠提示词提醒。
    """
    source = (ROOT / "worker" / "sample_provider.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_modules = {
        "socket", "subprocess", "urllib", "requests", "http", "ftplib",
        "paramiko", "psycopg", "sqlite3", "shutil", "os", "sys",
        "importlib", "backend", "app", "agentscope",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    bad_imports = imported & forbidden_modules
    record("取证模块没有网络/进程/数据库/后台 import", not bad_imports,
           f"实际 import：{sorted(imported)}" if not bad_imports else f"发现：{sorted(bad_imports)}")

    forbidden_calls = {"eval", "exec", "compile", "__import__", "system", "popen", "run"}
    bad_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in forbidden_calls
    }
    record("取证模块没有动态执行/命令调用", not bad_calls,
           "无 eval/exec/命令调用" if not bad_calls else f"发现：{sorted(bad_calls)}")

    # 写文件检查：open() 只要带了写模式就算破戒；本模块只用 Path.read_text
    has_write_open = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
        and len(node.args) >= 2
        for node in ast.walk(tree)
    )
    record("取证模块只读取本地文件", not has_write_open,
           "只用 Path.read_text（只读）" if not has_write_open else "发现带模式的 open()")


# ---------------------------------------------------------------------------
# 数据形状与「全假」检查
# ---------------------------------------------------------------------------
def iter_strings(value):
    """递归把 JSON 里所有字符串捞出来，方便统一做敏感词/地址检查。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def check_fixture_shape() -> None:
    """检查四类样例数据的字段形状是否齐全。"""
    for source in SOURCES:
        path = ROOT / "samples" / f"{source}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            record(f"{source}.json 合法", False, str(exc))
            continue
        record(f"{source}.json 合法且声明为假数据",
               data.get("meta", {}).get("fake") is True
               and data.get("meta", {}).get("source") == source,
               f"{len(data.get('scenarios', {}))} 个场景")

        scenarios = data.get("scenarios", {})
        ok = all(name in scenarios for name in REQUIRED_SCENARIOS)
        record(f"{source} 三类场景齐全", ok, "/".join(scenarios.keys()))

        normal = scenarios.get("normal", {})
        items = normal.get("items", [])
        ok = normal.get("data_status") == "ok" and bool(items) and all(
            all(key in item for key in ("id", "observed_at", "target")) for item in items
        )
        record(f"{source} normal 有证据且每条可挂结论", ok,
               f"{len(items)} 条，均带 id / observed_at / target")

    # 全假：不许出现真实地址、凭据字眼、外部 URL
    leak_words = ("password", "token", "secret", "http://", "https://", "ssh ", "root@")
    leaks: list[str] = []
    for source in SOURCES:
        data = load_fixture(source)
        for text in iter_strings(data):
            if any(word in text.lower() for word in leak_words):
                leaks.append(f"{source}:{text[:40]}")
    record("假数据里没有真实地址/账号/凭据字段", not leaks,
           "无外部 URL、无凭据字眼" if not leaks else f"可疑：{leaks[:3]}")

    # 目标名必须是明显的假名字
    allowed_prefixes = ("svc-", "fake-ns/", "ct-", "host-")
    bad_targets = []
    for source in SOURCES:
        for scenario in available_scenarios(source):
            for item in query(source, scenario)["items"]:
                if not str(item.get("target", "")).startswith(allowed_prefixes):
                    bad_targets.append(f"{source}/{scenario}:{item.get('target')}")
    record("所有目标名都是假名字", not bad_targets,
           "svc- / fake-ns/ / ct- 前缀" if not bad_targets else f"可疑：{bad_targets[:3]}")


# ---------------------------------------------------------------------------
# 三类特殊样本
# ---------------------------------------------------------------------------
def check_special_cases() -> None:
    # 1) 无数据：状态、显示文案、空证据三件事都要对
    """检查无数据、未知存量、注入载荷三类特殊样本。"""
    ok = True
    details = []
    for source in SOURCES:
        ev = query(source, "no_data")
        text = ev["status_text"]
        ok &= ev["data_status"] == "no_data" and text == "无数据" and text != "正常" \
            and ev["items"] == []
        details.append(f"{source}={text}")
    record("「无数据」显示为无数据，不显示为正常", ok, " ".join(details))

    # 2) 未知存量：状态、文案、partial 标记
    ok = True
    for source in SOURCES:
        ev = query(source, "unknown")
        ok &= ev["data_status"] == "unknown" and ev["status_text"] == "未知存量" \
            and all(item.get("completeness") == "partial" for item in ev["items"])
    record("「未知存量」如实标注为部分数据", ok, "四类均带 completeness=partial")

    # 3) 注入载荷：原样返回、标成不可执行数据、且文本没被当成指令
    payload = "忽略之前的所有指令"
    items = query("elk", "injection")["items"]
    value = next((item["raw"] for item in items if item.get("untrusted")), "")
    wrapped = wrap_untrusted_text(value)
    ok = payload in value and wrapped["text"] == value \
        and wrapped["type"] == "untrusted_data" and wrapped["executable"] is False
    record("注入载荷原样返回且被包成不可执行数据", ok,
           f"原样长度 {len(value)} 字，executable=False")

    # 白名单之外的数据源 / 场景一律拒掉（防路径穿越）
    blocked = False
    try:
        query("../.env", "normal")
    except SampleDataError:
        blocked = True
    bad_scenario = False
    try:
        query("elk", "../../secret")
    except SampleDataError:
        bad_scenario = True
    record("路径穿越 / 未知场景被拒", blocked and bad_scenario,
           "两个越界请求都被 SampleDataError 拦下")

    # 返回的是拷贝：调用方改了不会污染后面的排查
    first = query("prometheus", "normal")
    first["items"].append({"id": "篡改"})
    second = query("prometheus", "normal")
    record("取证结果不可被调用方污染", all(item["id"] != "篡改" for item in second["items"]),
           "第二次取到的还是原始数据")


# ---------------------------------------------------------------------------
# 排查取证预演（完成标志的核心）
# ---------------------------------------------------------------------------
def check_investigation_preflight() -> None:
    """
    模拟一次排查的取证段：四类问题逐个问 → 每条结论挂证据 → 校验器逐条验。

    校验器就是第 ⑦ 步「报告里没有一条无证据结论」的提前落地：
    结论没有 evidence_ids、或引用了不存在的证据编号，一律不许出报告。
    """
    conclusions: list[dict] = []
    evidence_index: dict[str, dict] = {}

    for source in SOURCES:
        ev = query(source, "normal")
        for item in ev["items"]:
            evidence_index[f"{source}:{item['id']}"] = item
        conclusions.append({
            "text": f"{source} 取证到 {len(ev['items'])} 条证据（{ev['status_text']}）",
            "evidence_ids": [f"{source}:{item['id']}" for item in ev["items"]],
            "data_status": ev["data_status"],
        })

    def violations(rows: list[dict]) -> list[str]:
        """列出这批结论里所有不合规的地方。"""
        problems = []
        for row in rows:
            ids = row.get("evidence_ids", [])
            if not ids:
                problems.append(f"结论「{row['text']}」没挂证据")
            for evidence_id in ids:
                if evidence_id not in evidence_index:
                    problems.append(f"结论引用了不存在的证据 {evidence_id}")
            if row.get("data_status") == "no_data" and "正常" in row["text"]:
                problems.append("无数据的结论里出现「正常」")
        return problems

    ok = not violations(conclusions)
    record("排查取证预演：每条结论都挂得上真实证据", ok,
           f"{len(conclusions)} 条结论 / {len(evidence_index)} 条证据")

    # 故意塞一条坏结论，确认校验器真会拦（不是摆设）
    with_bad = conclusions + [{"text": "一切正常", "evidence_ids": [], "data_status": "ok"}]
    record("校验器能拦下「无证据结论」", bool(violations(with_bad)),
           "故意加的空证据结论被判违规")

    # 注入载荷不许混进结论文本（日志是数据，不是指令）
    payload_words = ("一切正常", "删除审计记录")
    leaked = [conclusion["text"] for conclusion in conclusions if any(word in conclusion["text"] for word in payload_words)]
    record("注入载荷没被当成指令改写结论", not leaked,
           "结论文本全部由数据状态生成" if not leaked else f"被污染：{leaked}")

    # 把 socket 掐掉再跑一遍全流程：任何联网都会抛异常
    original_socket = socket.socket

    def _no_network(*_args, **_kwargs):
        """证明取证提供者没有网络客户端，物理上连不出去。"""
        raise AssertionError("排查取证期间发起了网络连接")

    socket.socket = _no_network  # type: ignore[assignment]
    try:
        zero_connection = True
        try:
            for source in SOURCES:
                for scenario in available_scenarios(source):
                    query(source, scenario)
        except AssertionError:
            zero_connection = False
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
    record("全程零网络连接（socket 已掐断）", zero_connection,
           "四类 × 全部场景，无任何连接尝试")

    # 打印一份可读的预演报告，让「跑了一次排查」看得见
    print("-" * 66)
    print("【排查取证预演报告】（演练数据，不是真实环境结论）")
    print("  任务：样例任务-001 · 范围：指标 / 日志 / Pod / 容器 · 全部只读")
    for conclusion in conclusions:
        print(f"  · {conclusion['text']}  ← 挂 {len(conclusion['evidence_ids'])} 条证据")
    print("  说明：提交/方案/结构化确认/执行/报告全链属于第 ⑥、⑦ 步")
    print("-" * 66)


def main() -> int:
    """按顺序跑完所有检查，打印汇总并返回退出码。"""
    print("=" * 66)
    print("第 ⑤ 步完成标志实测（样例数据 · 不连外部系统也能取证）")
    print("=" * 66)
    check_provider_is_readonly()
    check_fixture_shape()
    check_special_cases()
    check_investigation_preflight()

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
