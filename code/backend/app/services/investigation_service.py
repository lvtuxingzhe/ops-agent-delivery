"""排查任务服务。"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from ..core.config import Settings
from ..core.contract import DRAFT, REPORT_READY, RUNNING, assert_transition, status_text
from ..core.db import connect_app
from ..core.llm_gateway import load_rules
from ..core.redaction import RedactionSession, redact
from . import (
    config_service,
    exec_hosts,
    knowledge_service,
    notifications,
    screenshot_storage,
    task_trace,
    token_usage_service,
)
from .investigation.agent_runner import (
    _agent_env,
    _agent_result_fields,
    _kill_process_tree,
    _llm_allowlist,
    _resolve_llm_spec,
    _run_agent_stream,
    run_agent,
)
from .investigation.answer_mode import classify_answer_mode
from .investigation.audit_events import _write_audit_event
from .investigation.constants import (
    AGENT_SCRIPT,
    AGENT_TIMEOUT_SECONDS,
    EVIDENCE_EXCERPT_MAX,
    EVIDENCE_FIELDS,
    EXEC_SCRIPT,
    EXEC_TIMEOUT_SECONDS,
    RESULT_FIELDS,
    ROOT,
    TRACE_FIELD_MAX,
    TRACE_MAX_ITEMS,
)
from .investigation.errors import InvestigationError
from .investigation.execution import (
    _command_text,
    _expect_ok,
    _failure_item,
    _failure_summary,
    _mark_expect_results,
    _result_code,
    _run_exec_batch,
    run_exec_phase,
)
from .investigation.public import _safe_float, public_evidence, public_knowledge, public_trace
from .investigation.report_assembly import assemble_report
from .investigation.repository import (
    _TASK_COLUMNS,
    _load_screenshot_evidence,
    _task_row_to_response,
    _task_status,
    get_report,
    get_task,
)
from .task_queue import enqueue

logger = logging.getLogger(__name__)

__all__ = [
    "AGENT_RUNNER",
    "AGENT_SCRIPT",
    "AGENT_TIMEOUT_SECONDS",
    "EVIDENCE_EXCERPT_MAX",
    "EVIDENCE_FIELDS",
    "EXEC_SCRIPT",
    "EXEC_TIMEOUT_SECONDS",
    "InvestigationError",
    "RESULT_FIELDS",
    "ROOT",
    "TRACE_FIELD_MAX",
    "TRACE_MAX_ITEMS",
    "_TASK_COLUMNS",
    "_agent_env",
    "_agent_result_fields",
    "_command_text",
    "_expect_ok",
    "_failure_item",
    "_failure_summary",
    "_kill_process_tree",
    "_llm_allowlist",
    "_load_screenshot_evidence",
    "_mark_expect_results",
    "_resolve_llm_spec",
    "_result_code",
    "_run_agent_stream",
    "_run_exec_batch",
    "_safe_float",
    "_ssh_hosts_for_task",
    "_task_row_to_response",
    "_task_status",
    "_write_audit_event",
    "get_report",
    "get_task",
    "public_evidence",
    "public_knowledge",
    "public_trace",
    "run_agent",
    "run_exec_phase",
    "run_task",
    "submit_task",
]

def _redact_knowledge(settings: Settings, task_id: str, knowledge: list[dict]) -> list[dict]:
    """历史参考写入数据库前统一脱敏，避免旧报告里的密钥被新任务原样复制。"""
    if not knowledge:
        return []
    session = RedactionSession(task_id=f"knowledge-{task_id}")
    raw = json.dumps(knowledge, ensure_ascii=False)
    redacted = redact(raw, load_rules(settings), session, task_id=f"knowledge-{task_id}")
    try:
        result = json.loads(redacted.text)
    except (TypeError, ValueError):
        return []
    return result if isinstance(result, list) else []


def _host_sort_key(host_id: str) -> tuple:
    """按主机编号自然排序，避免 vm-150 排在 vm-15 后面。"""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", host_id)
    )


def _host_ids_from_text(candidates: list[dict], text: str) -> list[str]:
    """
    从任务标题/描述里解析所有命中的已登记主机。

    这是“全量命中”版本：不再要求唯一命中，也不限制台数。匹配顺序是
    完整 ID/完整地址/名称 -> 地址末段数字 -> 地址范围。裸数字只在
    命中已登记主机的地址末段时才算，端口、百分比、日期这类数字会被排除。
    """
    normalized = str(text or "").casefold()
    if not normalized:
        return []

    hosts: list[dict] = []
    last_octet_map: dict[str, list[str]] = {}
    for item in candidates:
        host_id = str(item.get("id") or "").strip()
        if not host_id:
            continue
        address = str(item.get("address") or "").strip()
        name = str(item.get("name") or "").strip()
        last_octet = address.rsplit(".", 1)[-1] if address.count(".") == 3 else ""
        hosts.append({
            "id": host_id,
            "address": address,
            "name": name,
            "last_octet": last_octet if last_octet.isdigit() else "",
        })
        if last_octet.isdigit():
            last_octet_map.setdefault(last_octet, []).append(host_id)

    matched: set[str] = set()
    id_boundary = "A-Za-z0-9_.-"

    # ① 明确写法：完整 ID、完整地址、主机名称。
    for host in hosts:
        host_id = host["id"]
        if re.search(
            rf"(?<![{id_boundary}]){re.escape(host_id.casefold())}(?![{id_boundary}])",
            normalized,
        ):
            matched.add(host_id)
            continue
        address = host["address"]
        if address and re.search(
            rf"(?<![0-9.]){re.escape(address.casefold())}(?![0-9.])",
            normalized,
        ):
            matched.add(host_id)
            continue
        name = host["name"]
        if name and len(name) >= 2 and name.casefold() in normalized:
            matched.add(host_id)

    # ② 裸数字：必须是某台启用主机地址最后一段，且在清单里唯一。
    #    这样“150和151主机”里的 150 也能命中，不依赖后面是否写“主机”。
    numeric_re = re.compile(r"(?<![0-9.])(\d{1,3})(?![0-9.])")
    for match in numeric_re.finditer(normalized):
        token = match.group(1)
        ids = last_octet_map.get(token, [])
        if len(ids) != 1:
            continue
        before = normalized[max(0, match.start() - 12):match.start()]
        after = normalized[match.end():match.end() + 12]
        # 端口、百分比、时长、数量单位不是主机。
        if re.search(r"(?:端口|port|端口号|:)\s*$", before):
            continue
        if re.match(
            r"\s*(?:%|ms|毫秒|秒|分钟|小时|台|个|次|元|万|k|m|g|gb|mb|核)",
            after,
        ):
            continue
        matched.add(ids[0])

    # ③ 地址范围：150-154、150到154、150~154，以及完整 IP 范围。
    range_re = re.compile(
        r"(?<![0-9.])(\d{1,3})\s*(?:-|~|到|至)\s*(\d{1,3})(?![0-9.])"
    )
    for match in range_re.finditer(normalized):
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            start, end = end, start
        for host in hosts:
            octet = host["last_octet"]
            if octet and start <= int(octet) <= end:
                matched.add(host["id"])

    ip_range_re = re.compile(
        r"(\d{1,3}(?:\.[0-9]{1,3}){3})\s*(?:-|~|到|至)\s*"
        r"(\d{1,3}(?:\.[0-9]{1,3}){3})"
    )
    for match in ip_range_re.finditer(normalized):
        start_ip, end_ip = match.group(1), match.group(2)
        start_prefix, start_last = start_ip.rsplit(".", 1)
        end_prefix, end_last = end_ip.rsplit(".", 1)
        if start_prefix != end_prefix or not start_last.isdigit() or not end_last.isdigit():
            continue
        start, end = int(start_last), int(end_last)
        if start > end:
            start, end = end, start
        for host in hosts:
            if host["address"].startswith(f"{start_prefix}.") and host["last_octet"]:
                if start <= int(host["last_octet"]) <= end:
                    matched.add(host["id"])

    # vm-150 到 vm-154 这类带前缀的 ID 范围。
    id_range_re = re.compile(
        r"([a-z][a-z0-9_.-]*?)-(\d{1,4})\s*(?:-|~|到|至)\s*\1-(\d{1,4})"
    )
    for match in id_range_re.finditer(normalized):
        prefix = match.group(1)
        start, end = int(match.group(2)), int(match.group(3))
        if start > end:
            start, end = end, start
        for host in hosts:
            host_match = re.fullmatch(
                rf"{re.escape(prefix)}-(\d+)", host["id"].casefold()
            )
            if host_match and start <= int(host_match.group(1)) <= end:
                matched.add(host["id"])

    return sorted(matched, key=_host_sort_key)


def _ssh_hosts_for_task(settings: Settings, target_scope: dict,
                        *, hint: str = "") -> list[dict]:
    """
    选出本次任务允许做只读 SSH 取证的主机。

    默认只放一台：任务范围显式写了 hosts/host/host_id 才按清单过滤；
    没写且只有一台启用主机时自动使用它，避免每个任务都要重复配主机。
    多台启用但任务没说范围时，从标题/描述里解析所有命中的已登记主机；
    只有明确写“所有主机/全部主机”才扩展到全部启用主机，不默认全量。
    """
    scope = target_scope if isinstance(target_scope, dict) else {}
    # 告警映射不到已登记主机时必须显式拒绝 SSH，不能走“只有一台启用主机”
    # 的兜底，也不能因为告警正文写了“所有主机”就扩大范围。
    if scope.get("no_ssh") is True:
        return []
    requested = scope.get("hosts") or scope.get("ssh_hosts") or scope.get("host_ids")
    if not requested and scope.get("host_id"):
        requested = [scope["host_id"]]
    if not requested and scope.get("host"):
        requested = [scope["host"]]
    if isinstance(requested, str):
        requested = [requested]

    try:
        candidates = [
            item for item in exec_hosts.list_hosts(settings)
            if item.get("enabled") and str(item.get("platform") or "").lower() == "linux"
        ]
    except Exception:  # noqa: BLE001 - 主机清单读不到不能阻断主链路；只是不给 SSH 工具
        return []

    requested_ids = {
        str(item).strip() for item in (requested or []) if str(item).strip()
    }
    if not requested_ids and hint:
        if re.search(r"(?:所有|全部|每台)\s*(?:已启用)?\s*主机", str(hint)):
            requested_ids = {
                str(item.get("id") or "").strip()
                for item in candidates
                if str(item.get("id") or "").strip()
            }
        else:
            requested_ids = set(_host_ids_from_text(candidates, hint))

    if requested_ids:
        candidates = [
            item for item in candidates
            if any(
                str(item.get(field) or "") in requested_ids
                for field in ("id", "name", "address")
            )
        ]
    elif len(candidates) != 1:
        return []

    result: list[dict] = []
    for item in candidates:
        host = exec_hosts.get_host(settings, str(item.get("id") or ""))
        if not host or host.get("local"):
            continue
        target = exec_hosts.target_of(host, settings)
        if not target.get("address") or not target.get("user"):
            continue
        if not target.get("password_enc"):
            continue
        result.append({
            "id": str(host.get("id") or item.get("id") or ""),
            "platform": str(host.get("platform") or "linux"),
            "os_family": str(host.get("os_family") or "unknown"),
            "package_manager": str(host.get("package_manager") or "unknown"),
            "service_manager": str(host.get("service_manager") or ""),
            **target,
        })
    return result

def submit_task(settings: Settings, *, title: str, description: str = "",
                target_scope: dict | None = None, actor_id: int = 0,
                client_source: str = "api",
                screenshots: list[dict] | None = None,
                origin: dict | None = None) -> dict:
    """
    提交一个排查任务：**只写入数据库 + 入队，立刻返回**，不在请求里跑 Agent。

    为什么改成异步：真 Agent 一次 5～7 秒，慢起来几十秒，同步接口会
    把 HTTP 连接和前端都拖住；接口超时后任务状态还留在「进行中」，
    重试又会重复提交。现在提交只做两件原子事（建任务 + 入队），
    执行交给 `run_task`，由队列 worker 领取。

    是否真实只读由运行环境和平台开关共同决定，task_type 固定 investigation：
    生产环境固定真实只读；测试环境由 `investigation_real_enabled` 决定。
    调用方传不进来，从根上断掉「提交一个能改生产的任务」这条路。
    真实执行时也只写 read_only=true，Agent 只提供取证工具，不提供执行命令。
    `origin` 只给内部服务（例如告警入口）用，写进 plan 的来源/只读标记；
    公开提交 API 不暴露这个参数，调用方不能借它绕过只读约束或改任务类型。
    """
    title = (title or "").strip()
    if not title:
        raise InvestigationError("任务标题不能为空")
    # 生产环境不再允许回退样例数据；测试环境是否用真实只读，由平台开关决定。
    with connect_app(settings) as conn:
        real_enabled = config_service.is_enabled(conn, "investigation_real_enabled")
    effective_real = settings.is_production or real_enabled
    task_id = str(uuid.uuid4())
    trace_id = f"inv-{uuid.uuid4()}"
    screenshots = screenshots or []
    if screenshots:
        from .screenshot_service import ocr_context

        description = (description or "").strip()
        description = (
            f"{description}\n\n{ocr_context(screenshots)}"
        ).strip()
    plan = {
        "sources": ["prometheus", "elk", "k8s", "docker"],
        "scenario": "normal", "fake": not effective_real,
        "note": (
            "排查只读；真实执行只开放只读取证，仍不允许执行命令"
            if effective_real else
            "排查只读；数据源为本地样例，等真实只读账号到位后替换"
        ),
    }
    # 只认这几个来源字段；任务类型、只读标记、审批令牌一律不允许由调用方覆盖。
    if origin:
        for key in ("source", "read_only", "actor_label", "alert",
                    "fake", "note", "group_key"):
            if key in origin:
                plan[key] = origin[key]
    # 原图先写对象存储；失败就不建任务。数据库写失败时再补偿删对象。
    try:
        screenshot_storage.store_screenshots(settings, task_id, screenshots)
    except screenshot_storage.ScreenshotStorageError as exc:
        raise InvestigationError(str(exc)) from exc

    try:
        with connect_app(settings) as conn:
            # 进入任务前把配置快照写进 plan：之后管理员改配置，不会影响已建任务，
            # 审计也能还原当时到底允许哪些只读命令。
            plan["readonly_policy"] = config_service.split_command_list(
                config_service.get_setting(
                    conn, "investigation_readonly_command_allowlist",
                )
            )
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO v3_tasks
                        (id, trace_id, task_type, status, stage, title,
                         description, target_scope, submitted_by, plan)
                    VALUES (%s, %s, 'investigation', %s, 'investigation',
                            %s, %s, %s::jsonb, %s, %s::jsonb)
                    """,
                    (task_id, trace_id, DRAFT, title, description,
                     json.dumps(target_scope or {}, ensure_ascii=False),
                     actor_id, json.dumps(plan, ensure_ascii=False)),
                )
                for shot in screenshots:
                    conn.execute(
                        """
                        INSERT INTO v3_task_screenshots
                            (id, task_id, filename, mime, size_bytes, sha256,
                             ocr_text, ocr_confidence, data_status, thumbnail_uri,
                             image, storage_backend, object_key)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            shot["id"], task_id, shot["filename"], shot["mime"],
                            shot["size_bytes"], shot["sha256"], shot["ocr_text"],
                            shot["ocr_confidence"], shot["data_status"],
                            shot["thumbnail"], shot.get("image"),
                            shot.get("storage_backend") or "db",
                            shot.get("object_key"),
                        ),
                    )
                # 入队必须和建任务同事务：否则会出现「任务建了但没入队」的孤儿任务
                enqueue(conn, task_id, "investigation")
    except Exception:
        screenshot_storage.remove_objects(settings, screenshots)
        raise
    _write_audit_event(settings, trace_id, "task.submitted",
                       {"task_id": task_id, "reason": "submitted"})
    if screenshots:
        _write_audit_event(settings, trace_id, "task.screenshots_uploaded", {
            "task_id": task_id,
            "count": len(screenshots),
            "bytes": sum(int(item.get("size_bytes", 0)) for item in screenshots),
        })

    return {
        "id": task_id,
        "trace_id": trace_id,
        "status": DRAFT,
        "status_text": status_text(DRAFT),
        "queue": "queued",
        "screenshot_count": len(screenshots),
    }

# 模块级可注入点。自检换成桩，不改这个函数以外的任何代码。
AGENT_RUNNER: Callable[[dict], dict] = run_agent

def run_task(settings: Settings, *, task_id: str, trace_id: str | None = None) -> dict:
    """
    真正执行一个排查任务：`draft → running → report_ready`。

    报告是结果，不是审批对象。排查到这里就结束；只有用户明确要执行命令，
    才由 `request_execution` 把同一条任务推进到 `awaiting_approval`。
    由队列 worker 调用（也可以手工补跑）。幂等：任务已经不是 `draft`
    就直接返回当前状态，不会重复跑一遍 Agent。
    任务标题/描述/目标范围从库里读 —— worker 是独立进程，拿不到提交时的局部变量。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT trace_id, title, description, target_scope, status,
                   submitted_by, assignee_id, plan
              FROM v3_tasks WHERE id = %s
            """,
            (task_id,),
        ).fetchone()
    if not row:
        raise InvestigationError("任务不存在，或已被清理")
    trace_id = trace_id or row[0]
    if row[4] != DRAFT:
        return get_task(settings, task_id) or {"id": task_id, "status": row[4]}
    title, description, target_scope = row[1], row[2], row[3] or {}
    target_scope = target_scope if isinstance(target_scope, dict) else {}
    submitter_id = int(row[5]) if row[5] is not None else None
    assignee_id = int(row[6]) if row[6] is not None else None
    task_plan = row[7] if isinstance(row[7], dict) else {}
    readonly_policy = task_plan.get("readonly_policy")
    if not isinstance(readonly_policy, list):
        readonly_policy = None
    scope_hint = f"{title or ''}\n{description or ''}"
    answer_decision = classify_answer_mode(title or "", description or "")
    answer_mode = answer_decision.mode
    ssh_hosts = _ssh_hosts_for_task(settings, target_scope, hint=scope_hint)
    # 老任务/自然语言提交没有显式主机范围：把解析出的全部主机补回范围并落库，
    # 让报告和后续审批看到“实际连的是哪些机器”，不是只在内存里临时生效。
    has_host_scope = any(
        target_scope.get(key)
        for key in ("hosts", "ssh_hosts", "host_ids", "host_id", "host")
    )
    if ssh_hosts and not has_host_scope:
        resolved_ids = sorted(
            {str(item.get("id") or "") for item in ssh_hosts if item.get("id")},
            key=_host_sort_key,
        )
        patch: dict[str, Any] = {"host_ids": resolved_ids}
        if len(resolved_ids) == 1:
            patch["host_id"] = resolved_ids[0]
        target_scope = {**target_scope, **patch}
        with connect_app(settings) as conn:
            conn.execute(
                "UPDATE v3_tasks SET target_scope = target_scope || %s::jsonb, "
                "updated_at = now() WHERE id = %s",
                (json.dumps(patch, ensure_ascii=False), task_id),
            )
        _write_audit_event(
            settings, trace_id, "task.host_resolved",
            {"task_id": task_id, "host_ids": resolved_ids, "count": len(resolved_ids),
             "host_id": patch.get("host_id"), "reason": "resolved_from_text"},
        )

    # ① 已提交 → 进行中
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status FROM v3_tasks WHERE id = %s FOR UPDATE", (task_id,)
            ).fetchone()
            if not row:
                raise InvestigationError("任务写入后没找到")
            assert_transition(row[0], RUNNING)
            conn.execute(
                "UPDATE v3_tasks SET status=%s, updated_at=now() WHERE id=%s",
                (RUNNING, task_id),
            )
    _write_audit_event(settings, trace_id, "task.started",
                       {"task_id": task_id, "reason": "started"})
    _write_audit_event(
        settings, trace_id, "task.answer_mode_resolved",
        {"task_id": task_id, "answer_mode": answer_mode,
         "reason": answer_decision.reason},
    )

    # ② 驱动真 Agent。出网白名单为空时【不启动子进程】——宁可记 no_data，
    #    也不让一次未脱敏、未校验目标的调用溜出去。
    allowlist = _llm_allowlist(settings)
    llm_spec, llm_reason = _resolve_llm_spec(settings)
    trace_seq = 0
    # 先占位：模型未启动、检索失败或评估任务时，报告里也要有明确的空列表。
    knowledge: list[dict] = []
    knowledge_for_prompt: list[dict] = []

    def record_trace(item: dict) -> None:
        """实时过程事件只做观测：失败不阻断排查，索引按实际写入顺序重排。"""
        nonlocal trace_seq
        trace_seq += 1
        stored = dict(item)
        stored["index"] = trace_seq
        task_trace.append_event(
            settings,
            subject_type=task_trace.SUBJECT_INVESTIGATION,
            subject_id=task_id,
            phase="investigate",
            kind=str(item.get("kind") or "step"),
            message=str(
                item.get("text") or item.get("name") or item.get("message") or ""
            ),
            payload={"item": stored},
            host_id="agent",
            local_seq=trace_seq,
        )

    rag = None
    if not allowlist:
        agent_result: dict = {"error": "no_llm_allowlist"}
        _write_audit_event(settings, trace_id, "llm.call.rejected",
                           {"task_id": task_id, "reason": "no_llm_allowlist"})
        record_trace({
            "kind": "error",
            "text": "排查未启动：模型出网白名单为空",
        })
    elif llm_reason:
        agent_result = {"error": llm_reason}
        _write_audit_event(settings, trace_id, "llm.call.rejected",
                           {"task_id": task_id, "reason": llm_reason})
        record_trace({
            "kind": "error",
            "text": f"排查未启动：{llm_reason}",
        })
    else:
        record_trace({
            "kind": "step",
            "text": "启动排查 Agent，准备读取任务与工具上下文",
        })
        try:
            rag = None
            eval_task = False
            knowledge_intent = False
            knowledge: list[dict[str, Any]] = []
            try:
                datasource_specs = config_service.datasource_specs(settings)
            except Exception:  # noqa: BLE001 - 数据源读失败不能导致排查主链路失败
                datasource_specs = []
            try:
                viewer_ids: list[int] = []
                for candidate in (submitter_id, assignee_id):
                    if candidate is not None and candidate not in viewer_ids:
                        viewer_ids.append(candidate)
                viewers: list[dict] = []
                for viewer_id in viewer_ids:
                    with connect_app(settings) as conn:
                        role_row = conn.execute(
                            "SELECT roles FROM v3_users WHERE id = %s",
                            (viewer_id,),
                        ).fetchone()
                    roles = role_row[0] if role_row and isinstance(role_row[0], list) else []
                    viewers.append({"id": viewer_id, "roles": [str(role) for role in roles]})
                # 评估任务不读历史参考，避免评估集被已有真实报告“喂答案”。
                eval_task = str(title or "").strip().startswith("[eval]")
                knowledge_intent = False
                if not eval_task:
                    from .knowledge_base.retrieval.workflow import (
                        has_knowledge_fingerprint,
                        has_knowledge_intent,
                    )

                    knowledge_intent = has_knowledge_intent(
                        title or "", description or "",
                        fingerprint_check=lambda: has_knowledge_fingerprint(
                            settings, title or "", description or "",
                            exclude_task_id=task_id,
                        ),
                    )
                knowledge = [] if not knowledge_intent else (
                    knowledge_service.search_similar(
                        settings,
                        query=" ".join((
                            title or "",
                            description or "",
                            json.dumps(target_scope or {}, ensure_ascii=False),
                        )),
                        exclude_task_id=task_id,
                        limit=2,
                        viewers=viewers,
                    )
                )
                # 受控 RAG：只在任务明确问历史/类似/手册/经验时启用；最多 1 轮、
                # 最多 2 条，避免普通实时排查也付检索与向量成本。
                rag = None
                if knowledge_intent:
                    try:
                        from .knowledge_base.retrieval.workflow import (
                            controlled_retrieve_for_viewers,
                        )

                        rag = controlled_retrieve_for_viewers(
                            settings, viewers, title=title or "",
                            description=description or "", limit=2, max_rounds=1,
                        )
                    except Exception:  # noqa: BLE001 - 知识检索失败不能阻断排查
                        logger.exception("受控 RAG 检索失败")
                        rag = None
            except Exception:  # noqa: BLE001 - 历史检索失败不能阻断本次排查
                logger.exception("历史排查报告检索失败")
                knowledge = []
                rag = None
            if not eval_task and not knowledge_intent:
                record_trace({
                    "kind": "step",
                    "text": "未检索知识库：任务没有历史/类似/手册/经验类意图",
                })
            if knowledge:
                record_trace({
                    "kind": "step",
                    "text": f"检索到 {len(knowledge)} 条历史相似案例，作为只读参考",
                })
            if rag and rag.documents:
                record_trace({
                    "kind": "step",
                    "text": (
                        f"受控 RAG 检索到 {len(rag.documents)} 条知识，"
                        f"最多 {len(rag.rounds)} 轮，只读参考"
                    ),
                })
            elif knowledge_intent and (rag is None or not rag.documents):
                record_trace({
                    "kind": "step",
                    "text": "知识库没有可用条目，已跳过知识注入",
                })
            knowledge_for_prompt = _redact_knowledge(settings, task_id, knowledge)
            task_payload = {
                "task_id": task_id,
                "trace_id": trace_id,
                "title": title,
                "description": description,
                "target_scope": target_scope or {},
                "answer_mode": answer_mode,
                "allowed_llm_hosts": allowlist,
                # 只传启用的 Prometheus/ELK 端点与默认查询；token 只传环境变量名
                "datasources": datasource_specs,
                # 只读 SSH 取证主机：只含连接元数据，密钥/密码不在任务里
                "ssh_hosts": ssh_hosts,
                # 任务提交时的只读命令启用快照；None = 旧任务，按代码支持全集
                "readonly_policy": readonly_policy,
                # 模型名 / 地址 / 参数由配置决定，worker 不写死任何厂商常量
                "llm": llm_spec,
                # 历史报告只作参考，不是本次证据；worker 侧会明确标注不可信
                "knowledge": knowledge_for_prompt,
                # 受控 RAG 的只读知识；最多 2 条、2000 字，避免把 prompt 撑爆
                "rag_context": (rag.context[:2000] if rag else ""),
            }
            if AGENT_RUNNER is run_agent:
                agent_result = run_agent(task_payload, on_trace=record_trace)
            else:
                agent_result = AGENT_RUNNER(task_payload)
        except Exception as exc:  # noqa: BLE001 - 子进程失败要降级，不能导致接口失败
            agent_result = {"error": type(exc).__name__}
            record_trace({
                "kind": "error",
                "text": f"排查 Agent 执行失败：{type(exc).__name__}",
            })

    report, evidences = assemble_report(
        settings=settings,
        task_id=task_id,
        target_scope=target_scope,
        answer_mode=answer_mode,
        agent_result=agent_result,
        knowledge_for_prompt=knowledge_for_prompt,
        rag=rag,
        llm_spec=llm_spec,
    )
    redaction_map = agent_result.get("redaction_map") or {}

    # ③ 写证据与报告，任务进入「报告已完成」。不在这里等审批：
    #    报告是结果，只有用户明确要执行命令时才进入 awaiting_approval。
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT status FROM v3_tasks WHERE id = %s FOR UPDATE", (task_id,)
            ).fetchone()
            assert_transition(row[0], REPORT_READY)
            conn.execute(
                """
                UPDATE v3_tasks
                   SET status=%s, report=%s::jsonb,
                       evidences=%s::jsonb,
                       redaction_map=%s::jsonb, updated_at=now()
                 WHERE id=%s
                """,
                (REPORT_READY,
                 json.dumps(report, ensure_ascii=False),
                 json.dumps(evidences, ensure_ascii=False),
                 json.dumps(redaction_map, ensure_ascii=False), task_id),
            )
            # 报告写入数据库即入队判知识价值；和状态推进同一个事务，避免任务完成
            # 但永远没人判的孤儿行。worker 不可用时队列重试，不阻断排查。
            enqueue(conn, task_id, "knowledge_curation")

    # 出网留痕：谁、去了哪个域名、脱敏替换了几处（只记数量，不记原文）
    if agent_result.get("egress_host"):
        _write_audit_event(settings, trace_id, "llm.egress", {
            "task_id": task_id,
            "host": agent_result.get("egress_host"),
            "redaction": agent_result.get("redaction", {}),
        })
    _write_audit_event(settings, trace_id, "task.report_ready", {
        "task_id": task_id, "reason": "report_ready",
        "conclusions": len(report["conclusions"]),
        "token_usage": report.get("token_usage") or {},
    })
    _write_audit_event(settings, trace_id, "task.answer_quality", {
        "task_id": task_id,
        "status": report["answer_quality"]["status"],
        "reasons": report["answer_quality"]["reasons"],
        "answer_mode": report["answer_quality"]["answer_mode"],
        "inventory_count": report["answer_quality"]["inventory_count"],
        "host_gap_count": report["answer_quality"]["host_gap_count"],
    })
    notifications.notify_report_ready(settings, task_id=task_id)
    # 预算检查是旁路：通知失败、配置读不到都不影响报告已经完成。
    try:
        token_usage_service.evaluate_after_task(settings, task_id)
    except Exception:  # noqa: BLE001 - 成本提醒不能把主流程拖挂
        logger.exception("Token 预算检查失败 task_id=%s", task_id)
    return get_task(settings, task_id)  # type: ignore[return-value]

