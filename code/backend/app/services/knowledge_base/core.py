"""知识库公共常量、文本归一化、任务摘要与错误类型。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..deployment_knowledge_service import build_item
from ..deployment_taxonomy import infer_tech_domain

logger = logging.getLogger(__name__)

# 知识条目类型：部署经验 / 排查报告 / 手工知识 / 上传文档。
KIND_LABELS: dict[str, str] = {
    "deployment": "部署经验",
    "investigation": "排查报告",
    "manual": "手工知识",
    "document": "上传文档",
}
KINDS: tuple[str, ...] = tuple(KIND_LABELS)

# 单次聚合最多读多少条任务；足够覆盖当前数据量，又不会把整库拉进内存。
MAX_CANDIDATES = 300
# 相似簇硬上限：无论怎么聚合，同簇都不允许超过 3 条。
MAX_PER_CLUSTER = 3
# 展示规则：同簇只露最新一条，其余折叠成“N 条相似”。
# 知识库是给人看结论的，不是任务流水；同主题的重复执行不应占多张卡。
KEEP_PER_CLUSTER = 1
# 对称 Jaccard 阈值；先做成常量，后续换 MinHash/SimHash 时只改这里。
SIMILARITY_THRESHOLD = 0.78
# 标题重叠系数：短标题被长标题覆盖到 0.9，视为同一主题（如“是否偏高”这种追问）。
# 标题太短（<6 个字符）时不用它，避免“部署 nginx”和“部署 mysql”被误并。
TITLE_OVERLAP_THRESHOLD = 0.9
# 同一对象的不同问法：标题重叠 0.7 以上、结论（摘要）也有一半以上重合，
# 才合并。用来收敛“查看150的负载”和“查看150主机的负载（实时轨迹验证）”。
SUBJECT_OVERLAP_THRESHOLD = 0.7
SUBJECT_SUMMARY_THRESHOLD = 0.5
MIN_TITLE_LEN = 6
MAX_TITLE = 60
MAX_SUMMARY = 160
# 知识正文不按 1000 字截断：正文要能承载完整根因、命令和回退步骤。
MAX_CONCLUSIONS = 3
MAX_TAGS = 8
MAX_TAG_LEN = 20
# 手工知识的结构化目标：主机/服务各最多 20 项，单项最多 64 字符。
MAX_TARGETS = 20
MAX_TARGET_LEN = 64

_TEST_TEXT_RE = re.compile(r"(\[eval\]|^E2E|演练|自检)", re.IGNORECASE)
# 知识正文的固定小标题。旧数据/AI 输出常把“现象、根因、方案”挤成一段，
# 读起来像流水账；读取时按标题和编号步骤补换行，不改原文措辞。
_BODY_LABEL_RE = re.compile(
    r"\s*(现象|判断|摘要|根因|处理步骤|初步方案|方案|结论|结果|目标|关键处理|原因码|原因)："
)
_BODY_STEP_RE = re.compile(r"[ \t]+(\d+[.、](?!\d))\s*")
# AI 规划任务的标题通常都叫“AI 规划部署”，直接展示会像重复；补上原因码或主机。
_GENERIC_AI_TITLES = {"AI 规划部署", "AI 规划"}
# 从摘要里认主机名；“host-150”“host-12”都统一到纯数字，便于跨问法比较。
_HOST_RE = re.compile(r"(?:vm|host)-?(\d{2,})", re.IGNORECASE)
# 有些排查报告只写 IP（192.0.2.150），取最后一段当主机号，和 host-150 对齐。
_IP_HOST_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.(\d{1,3})\b")
# 查询里的口语词不构成“知识主题”；去掉后才能把“查看150的负载”和
# “给我看看150机器的负载”认成同一件事。
_QUERY_WORDS = (
    "查看", "看看", "查一下", "查一查", "排查", "给我", "帮我", "请", "麻烦",
    "看一下", "检查", "确认", "是否", "有没有", "哪些", "什么", "情况",
    "机器", "主机", "服务器", "这台", "那台", "上的", "上", "的", "是", "有",
    "一下", "怎么样", "如何", "为什么", "偏高", "过低", "异常",
    "我想", "想", "我", "vm", "host",
)
_DIGITS_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")
# 括号里通常是“回归/验证/展示”这类测试限定语，不参与相似判断。
_PAREN_RE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]")
_NON_WORD_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


class KnowledgeError(Exception):
    """知识库业务错误；消息可直接展示给前端。"""


def _clean(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip()


def _clip(text: object, limit: int) -> str:
    """按字符截断，超长才加省略号；不破坏已读完的短文本。"""
    value = _clean(text)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def format_knowledge_body(value: object) -> str:
    """把“现象/根因/方案”挤成一行的正文整理成可读分段。

    只补换行和步骤分隔，不重写内容；上传文档等普通正文没有这些标签时
    原样返回，避免把正常段落拆坏。
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = _BODY_LABEL_RE.sub(r"\n\1：", text)
    text = _BODY_STEP_RE.sub(r"\n\1 ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_body(text: object) -> str:
    """正文只整理换行，不再按字数截断；完整内容由列表和详情原样返回。"""
    return format_knowledge_body(text)


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _clean(value)


def _is_test_task(title: str) -> bool:
    return bool(_TEST_TEXT_RE.search(title or ""))


def _short_reason(value: object) -> str:
    """`ai_plan_failed:invalid_response：不是合法 JSON` → `invalid_response`。

    原因码里可能带阶段前缀和长消息，标题只取中段的原因码，避免把整句
    失败信息塞进标题。
    """
    parts = [part.strip() for part in _clean(value).replace("：", ":").split(":")
             if part.strip()]
    if not parts:
        return ""
    return parts[1] if len(parts) > 1 else parts[0]


def _cluster_key(*parts: object) -> str:
    """去重用的归一化文本：去括号限定语、标点、空白，只留内容词。

    “排查 host-150 CPU 使用率（多可见人回归）”和
    “排查 host-150 CPU 使用率（可见范围回归）”对人来说是同一件事；
    括号里的词不该把相似度拉到阈值以下。
    """
    text = _PAREN_RE.sub(" ", " ".join(_clean(part) for part in parts))
    return _NON_WORD_RE.sub("", text.lower())


def _topic_key(title: object, summary: object) -> str:
    """把同一对象的不同问法压成同一个主题键。

    “查看150的负载”“查一下150分负载情况”“给我看看150机器的负载”
    去掉口语词、数字和标点后都是“负载”，再拼上摘要里的主机号，得到
    `150负载`。主题键只用于聚类，不参与展示。
    """
    summary_text = _clean(summary)
    host_match = _HOST_RE.search(summary_text)
    host = host_match.group(1) if host_match else ""
    if not host:
        ip_match = _IP_HOST_RE.search(summary_text)
        host = ip_match.group(1) if ip_match else ""
    if not host:
        return ""

    text = _PAREN_RE.sub(" ", _clean(title)).lower()
    text = text.replace("分负载", "负载")
    for word in _QUERY_WORDS:
        text = text.replace(word, " ")
    text = _DIGITS_RE.sub("", text)
    text = _NON_WORD_RE.sub("", text)
    return f"{host}{text}" if text else ""


def _summarize_deployment(row: dict[str, Any]) -> dict[str, Any] | None:
    """一条部署任务 → 一条精炼经验；不合格返回 None。"""
    base = build_item(row)
    if base is None:
        return None
    title = _clip(base["title"], MAX_TITLE)
    if title in _GENERIC_AI_TITLES:
        suffix = _short_reason(base["reason_code"]) or base["host"]
        if suffix:
            title = _clip(f"{title} · {suffix}", MAX_TITLE)
    plan = row.get("plan") if isinstance(row.get("plan"), dict) else {}
    operations = plan.get("operations") if isinstance(plan.get("operations"), list) else []
    verify = plan.get("verify") if isinstance(plan.get("verify"), list) else []
    rollback = plan.get("rollback") if isinstance(plan.get("rollback"), list) else []
    outcome = base["outcome"] or "unknown"
    success = outcome == "succeeded"
    reason_full = str(base["failure_summary"] or "").strip() if not success else ""
    reason = _clip(reason_full, MAX_SUMMARY)
    plan_summary_full = str(base["plan_summary"] or "").strip()
    plan_summary = _clip(plan_summary_full, MAX_SUMMARY)
    summary = reason or plan_summary
    if not summary:
        return None

    lines = [f"结果：{'成功' if success else '失败'}"]
    target = " / ".join(part for part in (base["host"], base["os_family"]) if part)
    if target:
        lines.append(f"目标：{target}")
    if plan_summary_full:
        lines.append(f"方案：{plan_summary_full}")
    if reason_full:
        lines.append(f"原因：{reason_full}")
    steps = f"关键处理：变更 {len(operations)} 条、验证 {len(verify)} 条、回滚 {len(rollback)} 条"
    lines.append(steps)
    if base["reason_code"]:
        lines.append(f"原因码：{base['reason_code']}")
    body = _format_body("\n".join(lines))

    return {
        "id": f"dep-{base['task_id']}",
        "kind": "deployment",
        "kind_label": KIND_LABELS["deployment"],
        "title": title,
        "summary": summary,
        "body": body,
        "tech_domain": infer_tech_domain(title, summary, plan_summary),
        "tags": ["成功" if success else "失败"],
        # 部署任务本身有明确目标主机，直接派生结构化字段；服务不做猜测。
        "target_hosts": [base["host"]] if base["host"] else [],
        "target_services": [],
        "source": "auto",
        "source_task_id": base["task_id"],
        "created_at": _iso(row.get("created_at") or base["updated_at"]),
        "updated_at": base["updated_at"],
        "_cluster_key": _cluster_key(title, summary),
        "_title_key": _cluster_key(title),
        "_topic_key": _topic_key(title, summary),
    }


def _summarize_investigation(row: dict[str, Any]) -> dict[str, Any] | None:
    """一条排查报告 → 一条精炼经验；只留标题、判断、摘要、结论。"""
    title = _clean(row.get("title"))
    if not title or _is_test_task(title):
        return None
    report = row.get("report") if isinstance(row.get("report"), dict) else {}
    if report.get("fake") is True:
        return None
    # 根因未闭环的报告不进知识库：只有“服务没起来”这类现象，没有可复用结论。
    if report.get("report_complete") is False:
        return None

    verdict = _clean(report.get("verdict"))
    headline = _clean(report.get("headline"))
    report_summary = _clean(report.get("summary"))
    summary = _clip(headline or report_summary or verdict, MAX_SUMMARY)
    if not summary:
        return None

    conclusions: list[str] = []
    for item in report.get("conclusions") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            conclusions.append(text)
        if len(conclusions) >= MAX_CONCLUSIONS:
            break

    lines = []
    if verdict:
        lines.append(f"判断：{verdict}")
    if report_summary and report_summary != summary:
        lines.append(f"摘要：{report_summary}")
    root_cause = str(report.get("root_cause") or "").strip()
    if root_cause:
        lines.append(f"根因：{root_cause}")
    solution = str(report.get("solution") or "").strip()
    if solution:
        lines.append(f"初步方案：{solution}")
    if conclusions:
        lines.append("结论：")
        lines.extend(f"- {item}" for item in conclusions)
    body = _format_body("\n".join(lines))

    return {
        "id": f"inv-{row.get('id')}",
        "kind": "investigation",
        "kind_label": KIND_LABELS["investigation"],
        "title": _clip(title, MAX_TITLE),
        "summary": summary,
        "body": body,
        "tech_domain": infer_tech_domain(title, summary, " ".join(conclusions) + " " + root_cause + " " + solution),
        "tags": [verdict] if verdict else [],
        # 排查报告没有稳定的结构化主机/服务输入，先留空走正文回退。
        "target_hosts": [],
        "target_services": [],
        "source": "auto",
        "source_task_id": str(row.get("id") or ""),
        "created_at": _iso(row.get("created_at") or row.get("updated_at")),
        "updated_at": _iso(row.get("updated_at")),
        "_cluster_key": _cluster_key(_clip(title, MAX_TITLE), summary),
        "_title_key": _cluster_key(_clip(title, MAX_TITLE)),
        "_topic_key": _topic_key(_clip(title, MAX_TITLE), summary),
    }
