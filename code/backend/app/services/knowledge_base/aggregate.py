"""自动/手工条目聚合、相似聚类与去重。"""

from __future__ import annotations

import logging
from typing import Any

from ...core.config import Settings
from ...core.db import connect_app
from .. import task_access
from ..deployment_taxonomy import normalize_tech_domain
from ..knowledge_service import jaccard, overlap
from .core import (
    KEEP_PER_CLUSTER,
    KIND_LABELS,
    MAX_CANDIDATES,
    MAX_SUMMARY,
    MAX_TAGS,
    MIN_TITLE_LEN,
    SIMILARITY_THRESHOLD,
    SUBJECT_OVERLAP_THRESHOLD,
    SUBJECT_SUMMARY_THRESHOLD,
    TITLE_OVERLAP_THRESHOLD,
    _clip,
    _cluster_key,
    _iso,
    _summarize_deployment,
    _summarize_investigation,
    format_knowledge_body,
)

logger = logging.getLogger(__name__)

def _visible_tasks(settings: Settings, user: dict, sql: str,
                   params: list[Any], limit: int) -> list[tuple]:
    """带任务可见范围的查询；admin/auditor 不加条件，其他人只看自己的。

    `sql` 只写到 WHERE 条件为止，不自己带 LIMIT —— 可见范围条件必须
    拼在 LIMIT 之前，否则会变成 `LIMIT 300 AND ...` 的语法错误。
    """
    clause = task_access.visible_clause(user)
    where = sql
    args = list(params)
    if clause:
        if clause[0] == "FALSE":
            return []
        where = f"{where} AND {clause[0]}"
        args.extend(clause[1])
    args.append(limit)
    # 可见范围条件必须在 ORDER BY / LIMIT 之前：调用方的 sql 只写到 WHERE，
    # 排序固定在这里补，避免非 admin 用户拼出 `DESC AND ...` 的语法错误。
    with connect_app(settings) as conn:
        return conn.execute(
            f"{where} ORDER BY updated_at DESC LIMIT %s", tuple(args)
        ).fetchall()


def _auto_entries(settings: Settings, user: dict) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    investigation_sql = """
        SELECT id::text, title, description, report, created_at, updated_at
          FROM v3_tasks
         WHERE task_type = 'investigation'
           AND status = 'report_ready'
           AND report <> '{}'::jsonb
    """
    for row in _visible_tasks(settings, user, investigation_sql, [], MAX_CANDIDATES):
        item = _summarize_investigation({
            "id": row[0], "title": row[1], "description": row[2],
            "report": row[3] or {}, "created_at": row[4], "updated_at": row[5],
        })
        if item:
            entries.append(item)

    deployment_sql = """
        SELECT id::text, title, description, outcome, reason_code, stage,
               plan, approval, created_at, updated_at
          FROM v3_tasks
         WHERE task_type = 'deployment'
           AND status = 'closed'
           AND outcome IN ('succeeded', 'failed')
    """
    for row in _visible_tasks(settings, user, deployment_sql, [], MAX_CANDIDATES):
        item = _summarize_deployment({
            "id": row[0], "title": row[1], "description": row[2],
            "outcome": row[3], "reason_code": row[4], "stage": row[5],
            "plan": row[6] or {}, "approval": row[7] or {},
            "created_at": row[8], "updated_at": row[9],
        })
        if item:
            entries.append(item)
    # 准入决策在这里生效：只把 admit 的自动条目交给列表/RAG；旧条目
    # 没有决策时按配置先显示，避免升级当天知识库突然空掉。
    from .. import knowledge_curation_service

    return knowledge_curation_service.apply_visibility(settings, user, entries)


def _manual_entries(
    settings: Settings,
    user: dict,
    *,
    host: str = "",
    service: str = "",
    tech_domain: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
    days: int = 0,
) -> list[dict[str, Any]]:
    """手工知识/上传文档；过滤条件必须先于 LIMIT，避免老条目被候选上限截掉。"""
    clauses = ["deleted_at IS NULL"]
    params: list[Any] = []
    if tech_domain:
        clauses.append("tech_domain = %s")
        params.append(normalize_tech_domain(tech_domain))
    if tags:
        clauses.append("tags && %s::text[]")
        params.append([str(tag) for tag in tags])
    if int(days or 0) > 0:
        clauses.append("updated_at >= now() - make_interval(days => %s)")
        params.append(int(days))
    # 结构化目标非空时精确比较；空数组才回退正文包含匹配，保证旧数据兼容。
    for column, value in (("target_hosts", host), ("target_services", service)):
        text = str(value or "").strip()
        if not text:
            continue
        clauses.append(
            f"""(
                EXISTS (
                    SELECT 1 FROM unnest({column}) AS target
                     WHERE lower(target) = lower(%s)
                )
                OR (
                    cardinality({column}) = 0
                    AND (
                        POSITION(lower(%s) IN lower(title)) > 0
                        OR POSITION(lower(%s) IN lower(summary)) > 0
                        OR POSITION(lower(%s) IN lower(body)) > 0
                        OR POSITION(lower(%s) IN lower(coalesce(source_text, ''))) > 0
                    )
                )
            )"""
        )
        params.extend([text] * 5)
    where = " AND ".join(clauses)
    with connect_app(settings) as conn:
        rows = conn.execute(
            f"""
            SELECT id::text, title, summary, body, tech_domain, tags,
                   created_by, updated_by, created_at, updated_at,
                   source_file_name, source_file_type, source_file_size, source_text,
                   target_hosts, target_services
              FROM v3_knowledge_entries
             WHERE {where}
             ORDER BY updated_at DESC
             LIMIT %s
            """,
            (*params, MAX_CANDIDATES),
        ).fetchall()
    uid = int(user["id"]) if user.get("id") is not None else None
    is_privileged = bool(set(user.get("roles") or []) & {"admin"})
    entries: list[dict[str, Any]] = []
    for row in rows:
        entry_id, title, summary, body, domain, tags = row[0], row[1], row[2], row[3], row[4], row[5]
        created_by, _updated_by, created_at, updated_at = row[6], row[7], row[8], row[9]
        file_name, file_type, file_size, source_text = row[10], row[11], row[12], row[13]
        target_hosts, target_services = row[14], row[15]
        kind = "document" if file_type else "manual"
        entries.append({
            "id": f"manual-{entry_id}",
            "kind": kind,
            "kind_label": KIND_LABELS[kind],
            "title": _clip(title, 120),
            "summary": _clip(summary, MAX_SUMMARY),
            "body": format_knowledge_body(body),
            "tech_domain": normalize_tech_domain(domain),
            "tags": [str(tag) for tag in (tags or [])][:MAX_TAGS],
            "target_hosts": [str(item) for item in (target_hosts or [])],
            "target_services": [str(item) for item in (target_services or [])],
            "source": "upload" if file_type else "manual",
            "source_task_id": "",
            "file_name": str(file_name or ""),
            "file_type": str(file_type or ""),
            "file_size": int(file_size or 0),
            "created_at": _iso(created_at),
            "updated_at": _iso(updated_at),
            "editable": bool(is_privileged or (uid is not None and created_by == uid)),
            "_cluster_key": "",
            "_search_text": str(source_text or ""),
        })
    return entries


def _same_topic(left: dict[str, Any], right: dict[str, Any],
                threshold: float) -> bool:
    """两条自动知识是否属于同一相似簇。

    判断顺序：
      1. 结论不同（tags 里的 verdict/结果不一致）就不合并，避免把
         “已安装未运行”和“证据不足无法确认”并成一条；
      2. 标题 + 摘要的对称 Jaccard 达到阈值；
      3. 标题高度重叠，且结论一致；
      4. 标题是同一对象的两种问法（重叠 ≥ 0.7），摘要也有一半以上重合。

    只看 Jaccard 时，“CPU 使用率（权限验证）”和“CPU 使用率是否偏高”
    只有 0.37，会被当成两条不同知识；但对人来说它们就是同一个问题的
    重复记录。
    """
    left_tags = tuple(left.get("tags") or [])
    right_tags = tuple(right.get("tags") or [])
    if left_tags and right_tags and left_tags != right_tags:
        return False

    # 同一主机 + 同一对象，哪怕问法完全不同，也先认成同主题。
    left_topic = left.get("_topic_key") or ""
    right_topic = right.get("_topic_key") or ""
    if left_topic and left_topic == right_topic:
        return True

    if jaccard(left["_cluster_key"], right["_cluster_key"]) >= threshold:
        return True

    left_title = left.get("_title_key") or ""
    right_title = right.get("_title_key") or ""
    if len(left_title) < MIN_TITLE_LEN or len(right_title) < MIN_TITLE_LEN:
        return False

    title_overlap = overlap(left_title, right_title)
    if title_overlap >= TITLE_OVERLAP_THRESHOLD:
        return True

    left_summary = _cluster_key(left.get("summary"))
    right_summary = _cluster_key(right.get("summary"))
    return (
        title_overlap >= SUBJECT_OVERLAP_THRESHOLD
        and jaccard(left_summary, right_summary) >= SUBJECT_SUMMARY_THRESHOLD
    )


def dedupe(entries: list[dict[str, Any]], *,
           threshold: float = SIMILARITY_THRESHOLD,
           per_cluster: int = KEEP_PER_CLUSTER) -> list[dict[str, Any]]:
    """同一相似簇只展示最新一条，其余折叠。

    三步收敛，顺序不能反：
      1. 簇内先合并**完全相同**的条目（标题、摘要归一化后一致），只留最新一条；
      2. 再按 `per_cluster` 截断，默认 1 条，硬上限 `MAX_PER_CLUSTER = 3`；
      3. 被折叠的数量写进 `deduped`，前端显示“折叠 N 条相似”。

    先合并完全相同，避免三条一模一样的历史任务占满名额，把真正有差异的
    记录挤掉。入参应先按更新时间倒序。聚类只和簇里**第一条**（最新）比较，
    不追求传递闭包——知识库是给人看的，稳定、可解释比数学完美重要。
    手工条目和上传文档不做相似收敛：它们是人给的，重复也应由人自己决定。
    """
    clusters: list[list[dict[str, Any]]] = []
    for entry in entries:
        if entry["kind"] not in ("deployment", "investigation") or not entry.get("_cluster_key"):
            clusters.append([entry])
            continue
        for cluster in clusters:
            head = cluster[0]
            if head["kind"] != entry["kind"] or not head.get("_cluster_key"):
                continue
            if _same_topic(entry, head, threshold):
                cluster.append(entry)
                break
        else:
            clusters.append([entry])

    kept: list[dict[str, Any]] = []
    for cluster in clusters:
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for entry in cluster:
            # 标题和摘要都相同，才算“完全相同”；只是同主题不算。
            fingerprint = (
                str(entry.get("_title_key") or ""),
                str(entry.get("_cluster_key") or ""),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(entry)

        kept.extend(unique[:per_cluster])
        dropped = len(cluster) - min(len(unique), per_cluster)
        if dropped > 0:
            for entry in unique[:per_cluster]:
                entry["deduped"] = dropped
            logger.info("知识库同簇裁掉 %s 条重复/相似内容", dropped)
    kept.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return kept
