"""排查任务 API 的请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class SubmitBody(BaseModel):
    title: str
    description: str = ""
    target_scope: dict = {}


class ExecOperation(BaseModel):
    """一条待批准的命令：argv 必须是数组，scope 只能是 read/dangerous。"""

    argv: list[str]
    scope: str = "read"
    # 部署任务用：apply / verify / rollback；排查任务留空。
    role: str = ""
    # 业务验证期望：exit_code / stdout_contains / stderr_contains / output_contains
    expect: dict = {}


class ExecPermitBody(BaseModel):
    """审批签发请求：平台 + 主机 + 操作清单。不收整行命令字符串。"""

    platform: str
    operations: list[ExecOperation]
    ttl_s: int = 600
    host: str = "local"
    # 审批理由（代批/接管时填），进审计和 approval.exec，不参与命令校验。
    reason: str = ""
    # 含 dangerous 操作时必填：当前审批人自己的 6 位 TOTP。
    step_up_code: str = ""


class RejectBody(BaseModel):
    """驳回/取消的理由，最多 200 字（服务层会再截一次）。"""

    reason: str = ""


class AssignBody(BaseModel):
    """转派目标用户；只收用户 ID，不收用户名，避免前端拼错身份。"""

    assignee_id: int


class RequestExecutionBody(BaseModel):
    """发起处置：命令由提交人先填好，审批页只审这份方案。"""

    reason: str = ""
    platform: str = ""
    host: str = "local"
    operations: list[ExecOperation]


class AiReviewBody(BaseModel):
    """AI 预审请求：只给建议，不签发许可。"""

    platform: str
    operations: list[ExecOperation]
    host: str = "local"
