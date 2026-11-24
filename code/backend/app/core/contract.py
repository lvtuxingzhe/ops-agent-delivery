"""任务状态流转契约。"""

from __future__ import annotations


class StateError(Exception):
    """状态流转不合法。消息可直接给用户看，不含内部细节。"""


# 状态（顺序即正常推进顺序，但不是所有任务都经过每一态：排查任务不经过 executing）
DRAFT = "draft"
RUNNING = "running"
REPORT_READY = "report_ready"
AWAITING_APPROVAL = "awaiting_approval"
EXECUTING = "executing"
CLOSED = "closed"

STATES = (DRAFT, RUNNING, REPORT_READY, AWAITING_APPROVAL, EXECUTING, CLOSED)

# 合法流转：报告完成不等于要执行；只有明确发起处置才进入待审批。
# 没有回头路：closed 是终态，要重来得新建任务 —— 这样审计和报告都不会被改写。
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    DRAFT: {RUNNING, CLOSED},
    RUNNING: {REPORT_READY, CLOSED},
    REPORT_READY: {AWAITING_APPROVAL, CLOSED},
    AWAITING_APPROVAL: {EXECUTING, CLOSED},
    EXECUTING: {CLOSED},
    CLOSED: set(),
}

# 结果标签。注意 outcome 只在 closed 时有值（数据库也有 CHECK 约束）
OUTCOMES = ("succeeded", "failed", "rejected", "cancelled", "partial", "adopted")

# 数据质量标签：不是状态，别塞进状态机
DATA_QUALITY = ("ok", "no_data", "unknown")

# 迁移脚本使用的状态词到当前状态的映射。
# 一个都不漏：漏了就会出现「老任务迁过来状态不明」，那比多几个状态更糟。
LEGACY_STATUS_MAP: dict[str, str] = {
    # tasks.status（8 个）
    "created": DRAFT,
    "running": RUNNING,
    "waiting_confirm": AWAITING_APPROVAL,
    "approved": AWAITING_APPROVAL,   # 旧语义是「已批准待执行」，新模型里归到待审批之后
    "rejected": CLOSED,
    "done": CLOSED,
    "failed": CLOSED,
    "cancelled": CLOSED,
    # task_steps.status
    "pending": DRAFT,
    # reports.status
    "draft": DRAFT,
    "adopted": CLOSED,
    # skills.status
    "enabled": CLOSED,
    "disabled": CLOSED,
    # evidences.data_status / health_status（这些本该是标签，迁移时归到 running 的产出物）
    "no_data": RUNNING,
    "unknown": RUNNING,
    "ok": RUNNING,
    # 注：health_status 里的 "failed" 与 tasks.failed 同词不同义；
    # 健康检查结果是配置属性，会折进 v3_configs.value，不会写进任务状态，
    # 所以这里不需要额外条目；下面 16 个键覆盖迁移脚本使用的状态词。
}

# 迁移时为 closed 任务补 outcome 的建议值。
LEGACY_OUTCOME_MAP: dict[str, str] = {
    "done": "succeeded",
    "adopted": "adopted",
    "enabled": "succeeded",
    "rejected": "rejected",
    "cancelled": "cancelled",
    "failed": "failed",
    "disabled": "cancelled",
}


def is_valid_state(state: str) -> bool:
    """判断状态是不是契约里的状态之一。"""
    return state in STATES


def can_transition(old: str, new: str) -> bool:
    """能不能从 old 走到 new。返回布尔，不抛异常（页面用来置灰按钮）。"""
    if old not in STATES or new not in STATES:
        return False
    return new in ALLOWED_TRANSITIONS[old]


def assert_transition(old: str, new: str, *, outcome: str | None = None) -> None:
    """
    真正推进状态前调一下：不合法就抛 StateError，别让脏流转写入数据库。

    两条附加规则：
      * 进入 closed 必须带 outcome；
      * 非 closed 不许带 outcome（避免"还在跑就标失败"）。
    数据库里也有同名 CHECK 约束 —— 应用层和数据层两头卡，不靠自觉。
    """
    if not can_transition(old, new):
        raise StateError(f"不允许从「{old}」推进到「{new}」")
    if new == CLOSED and outcome not in OUTCOMES:
        raise StateError("结束任务必须写明结果（succeeded/failed/rejected/cancelled/partial/adopted）")
    if new != CLOSED and outcome is not None:
        raise StateError("还没结束的任务不能先写结果")


def normalize_legacy_status(old_status: str) -> str:
    """
    把旧状态词翻成当前状态之一。遇到没登记的词【明确报错】，不悄悄降级成 draft ——
    静默降级会让历史任务的状态变得不可信。
    """
    key = (old_status or "").strip().lower()
    if key not in LEGACY_STATUS_MAP:
        raise StateError(f"没登记的旧状态：{old_status}")
    return LEGACY_STATUS_MAP[key]


def legacy_outcome(old_status: str) -> str | None:
    """旧状态对应的结果标签；翻成非 closed 的返回 None。"""
    key = (old_status or "").strip().lower()
    if normalize_legacy_status(key) != CLOSED:
        return None
    return LEGACY_OUTCOME_MAP.get(key, "succeeded")


def status_text(state: str) -> str:
    """页面上的人话。注意这里没有「正常」两个字 —— 那是状态判断，不是步骤。"""
    return {
        DRAFT: "已提交",
        RUNNING: "进行中",
        REPORT_READY: "报告已完成",
        AWAITING_APPROVAL: "待审批",
        EXECUTING: "执行中",
        CLOSED: "已结束",
    }.get(state, "未知状态")


def data_quality_text(quality: str) -> str:
    """数据质量标签的人话。no_data 必须是「无数据」，绝不能是「正常」。"""
    return {
        "ok": "有数据",
        "no_data": "无数据",
        "unknown": "未知存量",
    }.get(quality, "无数据")
