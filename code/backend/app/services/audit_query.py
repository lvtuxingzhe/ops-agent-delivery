"""
审计检索服务 —— 只读审计记录的查询层。

为什么单独一个模块
------------------
`ops_app` 对 `v3_audit_events` 只有 INSERT，没有 SELECT；查询必须走
`connect_audit_reader()` 的只读账号。把查询集中在这里，接口层只做参数
校验和角色门槛，避免以后有人图省事拿业务账号去查审计记录。

返回什么
--------
* 只回审计表本身有的列，不额外拼业务数据；
* `payload` 再递归剔除 password/token/secret 等键，虽然写入层已经脱敏，
  但查询层不假设“上游一定没写错”；
* 分页用 limit/offset，默认 50、上限 200，避免一次把整个审计记录拉给前端。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.config import Settings
from ..core.db import connect_audit_reader

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_TEXT = 4000
SENSITIVE_MARKERS = (
    "password", "passwd", "secret", "token", "credential",
    "authorization", "cookie", "api_key", "apikey", "private_key",
)


def _clip(value: str) -> str:
    """单字段截断：审计详情可以看，但不把超长请求体整段搬进浏览器。"""
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"


def sanitize_payload(value: Any, depth: int = 0) -> Any:
    """
    递归剔除敏感键并截断长字符串。

    键名命中 SENSITIVE_MARKERS 就整条丢弃，不做“部分打码”——审计详情里
    少一个字段比漏一个密钥安全。深度限制防止异常嵌套把递归打爆。
    """
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if any(marker in name.lower() for marker in SENSITIVE_MARKERS):
                continue
            cleaned[name] = sanitize_payload(item, depth + 1)
        return cleaned
    if isinstance(value, list):
        return [sanitize_payload(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _clip(value)
    return value


def _parse_time(name: str, raw: str) -> str:
    """校验时间参数；允许 `YYYY-MM-DD` 或完整 ISO，原样交给 SQL 的 timestamptz。"""
    text = raw.strip()
    if not text:
        return ""
    try:
        if len(text) == 10:
            datetime.fromisoformat(text)
        else:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} 不是合法时间：{text}") from exc
    return text


def normalize_filters(
    *, task_id: str = "", trace_id: str = "", event_type: str = "",
    actor: str = "", q: str = "", since: str = "", until: str = "",
    limit: int = DEFAULT_LIMIT, offset: int = 0,
) -> dict[str, Any]:
    """把 HTTP 参数收成统一结构：去空白、夹 limit、校验时间。"""
    if limit < 1:
        limit = DEFAULT_LIMIT
    if limit > MAX_LIMIT:
        limit = MAX_LIMIT
    if offset < 0:
        offset = 0
    return {
        "task_id": task_id.strip(),
        "trace_id": trace_id.strip(),
        "event_type": event_type.strip(),
        "actors": [item.strip() for item in actor.split(",") if item.strip()],
        "q": q.strip(),
        "since": _parse_time("since", since),
        "until": _parse_time("until", until),
        "limit": limit,
        "offset": offset,
    }


def build_where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """
    生成 WHERE 片段与参数列表。

    所有值都走 `%s` 绑定，不把用户输入拼进 SQL；`q` 只做 ILIKE 模糊匹配，
    不解析成 DSL，避免在审计接口上造一套查询语法。
    """
    clauses: list[str] = []
    params: list[Any] = []

    if filters["task_id"]:
        clauses.append("payload->>'task_id' = %s")
        params.append(filters["task_id"])
    if filters["trace_id"]:
        clauses.append("trace_id = %s")
        params.append(filters["trace_id"])
    if filters["event_type"]:
        clauses.append("event_type = %s")
        params.append(filters["event_type"])
    if filters["actors"]:
        placeholders = ", ".join(["%s"] * len(filters["actors"]))
        clauses.append(f"actor IN ({placeholders})")
        params.extend(filters["actors"])
    if filters["q"]:
        pattern = f"%{filters['q']}%"
        clauses.append(
            "(event_type ILIKE %s OR actor ILIKE %s OR trace_id ILIKE %s "
            "OR payload::text ILIKE %s)"
        )
        params.extend([pattern, pattern, pattern, pattern])
    if filters["since"]:
        clauses.append("created_at >= %s::timestamptz")
        params.append(filters["since"])
    if filters["until"]:
        clauses.append("created_at < %s::timestamptz")
        params.append(filters["until"])

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def list_actors(settings: Settings, *, limit: int = 100) -> dict[str, Any]:
    """取审计里出现过的操作人，供筛选页做可搜索多选。"""
    with connect_audit_reader(settings) as conn:
        rows = conn.execute(
            """
            SELECT actor, count(*) AS event_count
            FROM v3_audit_events
            WHERE actor <> ''
            GROUP BY actor
            ORDER BY event_count DESC, actor ASC
            LIMIT %s
            """,
            [max(1, min(limit, 500))],
        ).fetchall()
    return {
        "items": [
            {"actor": str(row[0]), "count": int(row[1])}
            for row in rows
        ]
    }


def query_events(settings: Settings, **raw: Any) -> dict[str, Any]:
    """查审计流水；返回白名单字段 + 总数 + 分页信息。"""
    filters = normalize_filters(**raw)
    where, params = build_where(filters)

    with connect_audit_reader(settings) as conn:
        total = conn.execute(
            f"SELECT count(*) FROM v3_audit_events {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT id, trace_id, event_type, actor, client_source, payload, created_at
            FROM v3_audit_events
            {where}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, filters["limit"], filters["offset"]],
        ).fetchall()

    items = [
        {
            "id": row[0],
            "trace_id": row[1],
            "event_type": row[2],
            "actor": row[3],
            "client_source": row[4],
            "task_id": (row[5] or {}).get("task_id", "") if isinstance(row[5], dict) else "",
            "payload": sanitize_payload(row[5] if isinstance(row[5], dict) else {}),
            "created_at": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": int(total),
        "limit": filters["limit"],
        "offset": filters["offset"],
        "has_more": filters["offset"] + len(items) < int(total),
    }


__all__ = [
    "DEFAULT_LIMIT", "MAX_LIMIT", "build_where", "list_actors", "normalize_filters",
    "query_events", "sanitize_payload",
]
