"""把框架的结构化输出整理成后台字段。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from shared_paths import ensure_shared_packages

ensure_shared_packages()

from ops_contract import split_report  # noqa: E402
from ops_guard import RedactionSession, restore_text  # noqa: E402

from runtime.report_answers import extract_answer_sections, inventory_gaps  # noqa: E402

# 归纳正文里不该出现的「整体判定」：只拦“一切/全部/系统正常”这类无证据下结论，
# 不再把“调度环节正常”“镜像已正常拉取”这种带具体对象的观察误判成结论。
FORBIDDEN_IN_SUMMARY = ("一切正常", "全部正常", "系统正常", "删除审计记录")

# 引用日志、复述诱导文本时出现这些词不算下结论；只有“自己下结论”才算。
_SUMMARY_QUOTE_MARKERS = (
    "日志", "echo", "文本", "忽略", "不可信", "不是", "声称", "注入", "引用",
)


def summary_guard_ok(summary: str) -> bool:
    """按分句判断整体性判定词；复述并否定日志内容时不误伤。"""
    clauses = [part.strip() for part in re.split(r"[\n。；;]+", summary) if part.strip()]
    for clause in clauses:
        if any(marker in clause for marker in _SUMMARY_QUOTE_MARKERS):
            continue
        if any(word in clause for word in FORBIDDEN_IN_SUMMARY):
            return False
    return True


# 初步方案必须带可直接执行的命令；只写“建议检查/重启”不算闭环。
_COMMAND_RE = re.compile(
    r"^\s*(?:sudo\s+)?(?:"
    r"systemctl|journalctl|systemd-analyze|hostnamectl|"
    r"grep|egrep|cat|less|tail|head|sed|awk|"
    r"ip|ss|netstat|lsof|ps|top|df|du|free|uptime|dmesg|find|stat|"
    r"chmod|chown|cp|mv|rm|mkdir|tar|unzip|curl|wget|"
    r"yum|dnf|apt|apt-get|rpm|docker|kubectl|nginx|mysql|psql|redis-cli|keepalived|"
    r"vi|vim|nano|tee|echo|install|ln|kill|pkill|python|python3|bash|sh"
    r")\b|^\s*```",
    re.MULTILINE,
)


@dataclass(frozen=True)
class EvidenceLedger:
    """
    结果整理需要的三样东西，只有三样。

    刻意不接收 EvidenceTools 实例：那个类在 tools/evidence.py 里，顶部 import
    了 agentscope。一旦这里依赖具体类，单测校验逻辑就得把整个框架拖进来。
    只认这个结构，本模块的依赖就只剩 ops_contract 与 ops_guard（都是纯逻辑）。
    """

    evidences: Mapping[str, dict]
    calls: Mapping[str, int]
    redaction_counts: Mapping[str, int]


CITATION_TOKEN_RE = re.compile(r"k8s:\d{1,3}(?:-[A-Za-z0-9_.:/-]+)?")


def normalize_citations(text: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """只保留证据清单里存在的引用；唯一前缀补全，歧义或伪造的整段移除。"""
    invalid: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = match.group(0).rstrip(".")
        if token in valid_ids:
            return token
        matches = [item for item in valid_ids if item.startswith(token)]
        if len(matches) == 1:
            return matches[0]
        invalid.append(token)
        return ""

    return CITATION_TOKEN_RE.sub(replace, text), invalid


WRITE_COMMAND_RE = re.compile(
    r"^\s*(?:sudo\s+)?(?:"
    r"(?:kubectl|oc)\s+(?:delete|apply|create|replace|patch|edit|scale|exec|run|"
    r"rollout|set|drain|cordon|uncordon|taint|label|annotate)\b"
    r"|systemctl\s+(?:restart|stop|disable|mask)\b"
    r"|rm\s+-rf\b|mkfs\b|dd\s+if=\b|reboot\b|shutdown\b|poweroff\b"
    r")"
)


def demote_write_commands(solution: str) -> str:
    """把方案里的写命令降级成需审批的注释，避免报告看起来可直接执行。"""
    lines = []
    for line in solution.splitlines():
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and WRITE_COMMAND_RE.search(stripped):
            indent = line[: len(line) - len(stripped)]
            lines.append(f"{indent}# 需人工确认/走审批后再执行： {stripped}")
        else:
            lines.append(line)
    return "\n".join(lines)


def normalize_report(
    *,
    payload: dict,
    ledger: EvidenceLedger,
    session: RedactionSession,
    target_host: str,
    elapsed_ms: int,
    task_redaction: dict[str, int],
    tool_names: list[str],
    structured_used: bool,
    framework: str,
    model_name: str,
    trace: list[dict] | None = None,
    task_id: str = "",
    answer_mode: str = "general",
    target_hosts: list[str] | None = None,
) -> dict:
    """把模型输出整理成入库结果；校验失败只降级，不编造。"""
    answer_mode = answer_mode if answer_mode in ("inventory", "diagnosis", "general") else "general"
    allowed_hosts = {str(item) for item in (target_hosts or []) if str(item)}
    accepted, dropped = split_report(payload, set(ledger.evidences))
    valid_ids = set(ledger.evidences)
    invalid_citations: set[str] = set()

    def clean(text: str) -> str:
        cleaned, bad = normalize_citations(text, valid_ids)
        invalid_citations.update(bad)
        return cleaned

    restore_ok = True

    def raw_field(name: str) -> str:
        return str(payload.get(name, "") or "").strip()

    def check_restore(raw: str) -> str:
        """只做还原校验，不把还原后的原文写进报告。

        报告写入数据库后保留 KEY-1 / IP-1 占位符；查看接口按 redaction_map 还原，
        导出接口保持占位符，避免密钥随 Markdown/PDF/Word 二次扩散。
        """
        nonlocal restore_ok
        restored = restore_text(raw, session)
        if restored is None:
            restore_ok = False
            return raw
        return restored

    summary_raw = raw_field("summary")
    summary_clean = clean(summary_raw)
    summary_restored = check_restore(summary_raw)
    guard_ok = summary_guard_ok(summary_restored)
    verdict = str(payload.get("verdict") or "insufficient").strip().lower()
    if verdict not in ("no_anomaly", "anomaly", "insufficient"):
        verdict = "insufficient"
    hypotheses: list[dict] = []
    for row in payload.get("hypotheses") or []:
        if not isinstance(row, dict):
            continue
        statement = clean(str(row.get("statement") or "").strip())
        if not statement:
            continue
        status = str(row.get("status") or "inconclusive").strip().lower()
        if status not in ("supported", "rejected", "inconclusive"):
            status = "inconclusive"
        evidence_ids = [
            str(eid) for eid in (row.get("evidence_ids") or [])
            if str(eid) in ledger.evidences
        ]
        # supported 必须挂真实证据；没有证据就降为 inconclusive，不让空口假设过关。
        if status == "supported" and not evidence_ids:
            status = "inconclusive"
        note_raw = str(row.get("note") or "").strip()
        check_restore(note_raw)
        check_restore(statement)
        hypotheses.append({
            "statement": statement,
            "status": status,
            "evidence_ids": evidence_ids,
            "note": note_raw[:300],
        })
        if len(hypotheses) >= 6:
            break
    for conclusion in accepted:
        # 结论的数据状态以它引用的证据快照为准：证据都是 ok 就写 ok；
        # 有任何一条 no_data 就写 no_data，其余保持 unknown。避免模型把
        # 一条真实 up=1 的结论标成 unknown，导出和页面出现两套状态。
        evidence_statuses = {
            str(ledger.evidences[eid].get("data_status") or "unknown")
            for eid in conclusion.get("evidence_ids", [])
            if eid in ledger.evidences
        }
        if evidence_statuses:
            if all(status == "ok" for status in evidence_statuses):
                conclusion["data_status"] = "ok"
            elif "no_data" in evidence_statuses:
                conclusion["data_status"] = "no_data"
            else:
                conclusion["data_status"] = "unknown"
        conclusion["text"] = clean(str(conclusion["text"]))
        check_restore(str(conclusion["text"]))

    redaction_counts = dict(task_redaction)
    for placeholder, count in ledger.redaction_counts.items():
        redaction_counts[placeholder] = redaction_counts.get(placeholder, 0) + count

    answer_sections = extract_answer_sections(
        payload=payload,
        valid_evidence_ids=valid_ids,
        allowed_hosts=allowed_hosts,
        clean_text=clean,
    )
    check_restore(answer_sections["primary_raw"])

    root_cause = clean(raw_field("root_cause")[:800])
    solution = demote_write_commands(clean(raw_field("solution")[:3000]))
    supported = [
        item for item in hypotheses
        if item["status"] == "supported" and item["evidence_ids"]
    ]
    report_gaps: list[str] = []
    if answer_mode == "inventory":
        report_gaps.extend(
            inventory_gaps(answer_sections["inventory"], answer_sections["primary_answer"])
        )
    if verdict == "anomaly":
        if not root_cause or "根因尚未确定" in root_cause:
            report_gaps.append("缺少根因判断")
        if not solution:
            report_gaps.append("缺少初步方案")
        elif not _COMMAND_RE.search(solution):
            report_gaps.append("初步方案缺少可执行命令")
        if not supported:
            report_gaps.append("没有一条被证据支持的假设")
    report_complete = not report_gaps
    supported_evidence_ids = sorted({
        evidence_id
        for item in supported
        for evidence_id in item["evidence_ids"]
    })
    root_cause_verified = bool(verdict == "anomaly" and root_cause and supported)

    evidence_rows = sorted(
        (dict(row) for row in ledger.evidences.values()),
        key=lambda row: row["id"],
    )
    # 只要有一条证据来自真实数据源，整份报告就不能再标成演练数据；
    # 没有证据时维持 fake=true，避免空报告被误读成真实现场。
    all_fake = all(bool(row.get("fake", True)) for row in evidence_rows)

    return {
        "task_id": task_id,
        "answer_mode": answer_mode,
        "primary_answer": answer_sections["primary_answer"],
        "inventory": answer_sections["inventory"],
        "dropped_inventory": answer_sections["dropped_inventory"],
        "incidental_findings": answer_sections["incidental_findings"],
        "dropped_incidental_findings": answer_sections["dropped_incidental_findings"],
        "headline": raw_field("headline")[:120],
        "verdict": verdict,
        "scope": raw_field("scope")[:300],
        "summary": summary_clean[:800],
        "hypotheses": hypotheses,
        "root_cause": root_cause,
        "root_cause_verified": root_cause_verified,
        "root_cause_evidence_ids": supported_evidence_ids,
        "solution": solution,
        "report_complete": report_complete,
        "report_gaps": report_gaps,
        "next_step": clean(raw_field("next_step")[:300]),
        "conclusions": accepted,
        "dropped": dropped,
        "unknowns": [
            str(item)
            for item in (payload.get("unknowns") or [])
        ],
        "evidences": evidence_rows,
        "tool_calls": dict(ledger.calls),
        "tools_called_count": sum(ledger.calls.values()),
        "tool_names": tool_names,
        "trace": trace or [],
        "valid_evidence_ids": sorted(ledger.evidences),
        "invalid_citation_ids": sorted(invalid_citations),
        "elapsed_ms": elapsed_ms,
        "guard_ok": guard_ok,
        "restore_ok": restore_ok,
        "structured_output": structured_used,
        "redaction": redaction_counts,
        "redaction_map": session.to_local_map(),
        "egress_host": target_host,
        "fake": all_fake,
        "model": model_name,
        "framework": framework,
    }


__all__ = ["FORBIDDEN_IN_SUMMARY", "EvidenceLedger", "normalize_report"]
