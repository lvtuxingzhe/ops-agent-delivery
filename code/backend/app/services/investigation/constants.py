"""排查域共享常量。

保持原 `investigation_service.py` 里的值不变；只把路径基准从
`app/services` 调整到 `app/services/investigation`。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AGENT_SCRIPT = ROOT / "worker" / "agent_live.py"
EXEC_SCRIPT = ROOT / "worker" / "exec_run.py"

# 子进程超时。真 Agent 一次约 5～7 秒，给足余量；超时就如实记无数据。
AGENT_TIMEOUT_SECONDS = 90
# 已批准命令的执行上限：单条命令自身最多 60s（exec_core 硬顶），
# 20 条留足余量；超时按失败处理，不无限挂着。
EXEC_TIMEOUT_SECONDS = 1200

RESULT_FIELDS = (
    "task_id", "headline", "verdict", "scope", "summary", "next_step",
    "hypotheses", "root_cause", "root_cause_verified", "root_cause_evidence_ids", "solution",
    "report_complete", "report_gaps",
    "conclusions", "dropped", "unknowns", "evidences", "tool_calls",
    "tools_called_count", "tool_names", "trace", "valid_evidence_ids", "elapsed_ms",
    "guard_ok", "restore_ok", "structured_output", "redaction", "redaction_map",
    "egress_host", "fake", "model", "framework", "finalized_without_tools",
    "token_usage", "error",
)

# 证据写入数据库时只保留这些字段，Agent 多给的字段一律不写入数据库。
EVIDENCE_FIELDS = (
    "id", "source", "observed_at", "target", "data_status", "fake", "excerpt",
)

# 证据快照给前端下钻时的长度上限。worker 已脱敏并截到 800，这里再兜一层。
EVIDENCE_EXCERPT_MAX = 800

TRACE_MAX_ITEMS = 80
TRACE_FIELD_MAX = 2000
