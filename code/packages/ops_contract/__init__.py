"""报告结论契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 数据质量三态，与证据表、报告页保持一致
DATA_STATUSES = ("ok", "no_data", "unknown")

# 无数据却写成「正常」这类判定词，一律视为违规
FORBIDDEN_WHEN_NO_DATA = ("正常", "没问题", "一切正常")


class Conclusion(BaseModel):
    """一条结论：只复述观察到的事实，必须能追到证据。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400, description="只复述观察到的事实，不做根因判断")
    evidence_ids: list[str] = Field(min_length=1, description="必须来自工具真实返回的证据编号")
    data_status: Literal["ok", "no_data", "unknown"] = Field(
        default="ok", description="这条结论所依据数据的质量：ok/no_data/unknown",
    )


class Hypothesis(BaseModel):
    """一条可检验的假设，以及它在本次排查中的验证状态。"""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(
        min_length=1, max_length=300,
        description="一个具体、可验证的原因假设，不要写现象或泛泛的“可能有问题”",
    )
    status: Literal["supported", "rejected", "inconclusive"] = Field(
        default="inconclusive",
        description="supported=证据支持；rejected=已被证据排除；inconclusive=证据不足以下结论",
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="支持或排除这个假设的证据编号；可以留空，但 supported 至少要有一个",
    )
    note: str = Field(
        default="", max_length=300,
        description="一句话说明验证过程和为什么得出这个状态",
    )


class InventoryItem(BaseModel):
    """一条可核验的服务/进程清单条目。"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=80, description="主机 ID，必须在本次范围内")
    service: str = Field(min_length=1, max_length=120, description="服务名、进程名或容器名")
    kind: Literal["systemd", "process", "container", "port"] = Field(
        description="清单来源：systemd 单元 / 进程 / 容器 / 监听端口",
    )
    ports: list[str] = Field(default_factory=list, description="监听端口，例如 3306、9200")
    status: Literal["running", "failed", "unknown", "no_data"] = Field(
        default="unknown", description="运行状态；no_data=没有取到该主机数据",
    )
    evidence_ids: list[str] = Field(min_length=1, description="必须来自本次真实证据")


class IncidentalFinding(BaseModel):
    """本次顺带发现的异常，不是用户要的主答案。"""

    model_config = ConfigDict(extra="forbid")

    object: str = Field(min_length=1, max_length=120, description="异常对象，例如 host-150 nginx")
    finding: str = Field(min_length=1, max_length=300, description="现象和原因摘要")
    severity: Literal["info", "warning", "critical"] = Field(
        default="warning", description="影响程度；info=记录，warning=需关注，critical=影响业务"
    )
    evidence_ids: list[str] = Field(min_length=1, description="必须来自本次真实证据")
    impact: str = Field(default="", max_length=300, description="影响范围或已排除结论")


class HostCoverage(BaseModel):
    """逐台主机的数据覆盖状态，防止把“没连上”写成“没有服务”。"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=80)
    status: Literal["ok", "no_data", "unreachable"] = Field(default="unreachable")
    reason: str = Field(default="", max_length=200)
    evidence_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    """一次排查的归纳结果；写给一线运维看，不是审计流水。"""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(
        default="",
        max_length=120,
        description="一句话直接回答标题里的问题。清单/盘点类先给对象数量和关键清单，"
                    "不要为了带范围把主语写成“在……范围内”；范围写在 scope。故障类写直接判断。",
    )
    primary_answer: str = Field(
        default="",
        max_length=800,
        description="直接回答标题的正文。清单类先给清单或数量；诊断类写直接判断。",
    )
    inventory: list[InventoryItem] = Field(
        default_factory=list,
        description="结构化服务/进程清单；每条必须挂真实证据，host 必须在本次范围内",
    )
    incidental_findings: list[IncidentalFinding] = Field(
        default_factory=list,
        description="本次顺带发现的异常，不是用户要的主答案；最多 6 条",
    )
    host_coverage: list[HostCoverage] = Field(
        default_factory=list,
        description="逐台主机覆盖状态；没有取到数据的主机不能写成“没有服务”",
    )
    verdict: Literal["no_anomaly", "anomaly", "insufficient"] = Field(
        default="insufficient",
        description="no_anomaly=范围内未发现异常；anomaly=发现异常；insufficient=证据不足",
    )
    scope: str = Field(
        default="",
        max_length=300,
        description="1-2 句话写清这次判断覆盖的数据源、目标、时间段；没覆盖的也要写",
    )
    summary: str = Field(
        min_length=1,
        max_length=1500,
        description="先结论、后依据、再缺口，3-6 句；不要按时间线复述流水账",
    )
    hypotheses: list[Hypothesis] = Field(
        default_factory=list,
        description="按“假设 → 验证”列出本次排查考虑过的原因，最多 6 条；没有假设时留空",
    )
    root_cause: str = Field(
        default="", max_length=800,
        description="基于证据的根因判断；证据不足时写“根因尚未确定：还缺什么”，不要编造",
    )
    solution: str = Field(
        default="", max_length=3000,
        description="初步解决方案：3-6 条，每条先写目的与预期结果，再给可直接复制执行的命令；"
                    "命令使用真实路径/服务名/端口，未知值用占位符；"
                    "高风险动作注明需人工确认/走审批，方案不自动执行",
    )
    conclusions: list[Conclusion] = Field(default_factory=list, description="每条都要挂证据编号")
    unknowns: list[str] = Field(default_factory=list, description="具体缺什么数据，不写「待补证」这类黑话")
    next_step: str = Field(
        default="",
        max_length=300,
        description="建议人先做什么，例如按方案第几步执行、补数据或重查；可直接指到方案中的命令",
    )


def split_report(payload: dict, valid_evidence_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """
    把模型给的结论分成「可采纳」和「丢弃」两堆。

    采纳条件（三条全过）：
      1. 至少挂一个证据编号；
      2. 编号全部来自本次真的查回来的证据（valid_evidence_ids）；
      3. data_status 不是 ok 时，文本里不许出现「正常 / 没问题」这类判定词。

    返回 (accepted, dropped)，都是可以直接入库的普通 dict。
    """
    accepted: list[dict] = []
    dropped: list[dict] = []

    for row in payload.get("conclusions") or []:
        if not isinstance(row, dict):
            continue
        evidence_ids = [str(item) for item in (row.get("evidence_ids") or [])]
        status = str(row.get("data_status") or "ok")
        text = str(row.get("text") or "").strip()

        violation = ""
        if not evidence_ids:
            violation = "no_evidence_id"
        elif not set(evidence_ids).issubset(valid_evidence_ids):
            violation = "unknown_evidence_id"
        elif status != "ok" and any(word in text for word in FORBIDDEN_WHEN_NO_DATA):
            violation = "no_data_claimed_normal"

        if violation:
            dropped.append({**row, "reason": violation})
        else:
            accepted.append({"text": text, "evidence_ids": evidence_ids, "data_status": status})

    return accepted, dropped


def report_to_dict(report: Report) -> dict:
    """把已校验的 Report 转成可入库的普通 dict。"""
    return report.model_dump(mode="json")


from .answers import (  # noqa: E402, F401  # 放在末尾，避免和上面的契约类型抢导入顺序
    filter_incidental_findings,
    filter_inventory,
)
