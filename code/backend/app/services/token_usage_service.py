"""Token 用量汇总与预算提醒。

数据来源只有一种：排查任务写进 `v3_tasks.report` 的 `token_usage`。
旧报告没有这个字段时按「未知」处理，不把缺失当成 0 来展示，避免面板
把历史免费任务也算成一次真实调用。

金额是估算，不是账单：
* 单价来自模型配置里的 `input_price_per_1k` / `output_price_per_1k`；
* 任务报告会保存发生当时的单价快照，改价不会反过来改写历史估算；
* 没填单价就只显示 token，不显示金额。
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

from ..core.config import Settings
from ..core.db import connect_app
from . import config_service, notifications
from . import llm_config_service as llm

TZ = timezone(timedelta(hours=8))
PRICE_FIELDS = ("input_price_per_1k", "output_price_per_1k")
MONEY_ROLES = {"admin", "auditor"}


def _now_local() -> datetime:
    """统一按北京时间分日；数据库里存的是带时区时间，换日不能跟着机器时区漂。"""
    return datetime.now(TZ)


def _as_int(value: Any) -> int:
    """JSONB 里的值可能缺失或是字符串；只接受能转成非负整数的内容。"""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _as_dt(value: Any) -> datetime | None:
    """把数据库时间转成带时区 datetime；测试里给字符串也能处理。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _local_day(value: Any) -> str:
    """时间 → 北京时间自然日（YYYY-MM-DD）。"""
    parsed = _as_dt(value)
    return parsed.astimezone(TZ).date().isoformat() if parsed else ""


def _usage(report: dict) -> dict[str, int]:
    usage = report.get("token_usage") if isinstance(report, dict) else {}
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = _as_int(usage.get("input_tokens"))
    output_tokens = _as_int(usage.get("output_tokens"))
    total_tokens = _as_int(usage.get("total_tokens")) or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _price_from_config(config: dict | None) -> dict[str, float] | None:
    """模型配置里两个单价都有，才认为可以算金额；0 是合法价格。"""
    params = (config or {}).get("params")
    if not isinstance(params, dict):
        return None
    prices: dict[str, float] = {}
    for field in PRICE_FIELDS:
        value = params.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            prices[field] = float(value)
    return prices if len(prices) == len(PRICE_FIELDS) else None


def _price_for_row(
    report: dict, configs: dict[str, dict], default_config: dict | None,
) -> dict[str, float] | None:
    """先信报告里的快照，再按模型 key 找配置，最后回退到当前默认模型。"""
    meta = report.get("llm") if isinstance(report, dict) else None
    if isinstance(meta, dict):
        snapshot = _price_from_config({"params": meta})
        if snapshot:
            return snapshot
        key = str(meta.get("key") or "")
        if key and key in configs:
            found = _price_from_config(configs[key])
            if found:
                return found
    return _price_from_config(default_config)


def _cost(usage: dict[str, int], prices: dict[str, float] | None) -> float | None:
    """人民币估算；单价按「元 / 千 token」计价。"""
    if not prices:
        return None
    value = (
        usage["input_tokens"] / 1000.0 * prices["input_price_per_1k"]
        + usage["output_tokens"] / 1000.0 * prices["output_price_per_1k"]
    )
    return round(value, 4)


def _empty_bucket() -> dict[str, Any]:
    """没有数据也返回完整字段，前端不用为 null 到处判空。"""
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "tasks": 0,
        "priced_tasks": 0,
        "unpriced_tasks": 0,
        "cost": None,
    }


def _aggregate(items: list[dict]) -> dict[str, Any]:
    bucket = _empty_bucket()
    for item in items:
        usage = item["usage"]
        bucket["input_tokens"] += usage["input_tokens"]
        bucket["output_tokens"] += usage["output_tokens"]
        bucket["total_tokens"] += usage["total_tokens"]
        bucket["tasks"] += 1
        if item.get("cost") is None:
            bucket["unpriced_tasks"] += 1
        else:
            bucket["priced_tasks"] += 1
            bucket["cost"] = round((bucket["cost"] or 0.0) + float(item["cost"]), 4)
    return bucket


def _strip_cost(bucket: dict[str, Any]) -> dict[str, Any]:
    """非管理员/审计员不返回金额；token 仍可看，避免把成本信息带出权限边界。"""
    clean = dict(bucket)
    clean.pop("cost", None)
    return clean


def _public_bucket(bucket: dict[str, Any], money_visible: bool) -> dict[str, Any]:
    return bucket if money_visible else _strip_cost(bucket)


def _rows_for_since(conn: Any, since: datetime) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id::text, title, updated_at, report
          FROM v3_tasks
         WHERE task_type = 'investigation'
           AND report IS NOT NULL
           AND updated_at >= %s
         ORDER BY updated_at DESC
        """,
        (since,),
    ).fetchall()
    return [
        {
            "id": str(row[0]),
            "title": str(row[1] or ""),
            "updated_at": row[2],
            "report": row[3] if isinstance(row[3], dict) else {},
        }
        for row in rows
    ]


def _rows_with_metrics(
    rows: list[dict], configs: dict[str, dict], default_config: dict | None,
) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        usage = _usage(row["report"])
        if usage["total_tokens"] <= 0:
            # 旧报告或未记账任务不进入用量统计，绝不把 0 当成一次真实调用。
            continue
        items.append({
            **row,
            "day": _local_day(row["updated_at"]),
            "usage": usage,
            "cost": _cost(usage, _price_for_row(row["report"], configs, default_config)),
        })
    return items


def _budget_values(conn: Any) -> dict[str, int]:
    return {
        "per_task_tokens": int(
            config_service.get_setting(conn, "investigation_token_budget_per_task")
        ),
        "per_day_tokens": int(
            config_service.get_setting(conn, "investigation_token_budget_per_day")
        ),
    }


def build_token_summary(
    settings: Settings, user: dict, *, days: int = 30,
) -> dict:
    """概览页 Token 面板：今日、本月、最近 7 天和最近任务。"""
    days = max(1, min(int(days or 30), 90))
    now = _now_local()
    since = now - timedelta(days=max(days, 32))
    with connect_app(settings) as conn:
        rows = _rows_for_since(conn, since)
        budgets = _budget_values(conn)
    configs = {item["key"]: item for item in llm.list_configs(settings)}
    default_config = llm.get_default_config(settings)
    items = _rows_with_metrics(rows, configs, default_config)

    today = now.date().isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seven_start = (now - timedelta(days=6)).date().isoformat()

    month_items = [
        item for item in items
        if _as_dt(item["updated_at"]) and _as_dt(item["updated_at"]) >= month_start
    ]
    today_bucket = _aggregate([item for item in items if item["day"] == today])
    month_bucket = _aggregate(month_items)
    seven_items = [item for item in items if item["day"] >= seven_start]
    seven_bucket = _aggregate(seven_items)

    day_series = []
    for offset in range(6, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        day_bucket = _aggregate([item for item in items if item["day"] == day])
        day_series.append({
            "date": day,
            "total_tokens": day_bucket["total_tokens"],
            "tasks": day_bucket["tasks"],
        })

    per_task = budgets["per_task_tokens"]
    per_day = budgets["per_day_tokens"]
    exceeded_tasks = (
        sum(1 for item in month_items if item["usage"]["total_tokens"] > per_task)
        if per_task > 0 else 0
    )
    daily_exceeded = bool(per_day > 0 and today_bucket["total_tokens"] > per_day)
    money_visible = bool(MONEY_ROLES & set(user.get("roles") or []))
    recent = [
        {
            "id": item["id"],
            "title": item["title"],
            "updated_at": (
                _as_dt(item["updated_at"]).isoformat() if _as_dt(item["updated_at"]) else None
            ),
            **_public_bucket({
                **item["usage"],
                "tasks": 1,
                "priced_tasks": 0 if item["cost"] is None else 1,
                "unpriced_tasks": 1 if item["cost"] is None else 0,
                "cost": item["cost"],
            }, money_visible),
        }
        for item in items[:5]
    ]

    result = {
        "available": True,
        "amount_visible": money_visible,
        "today": _public_bucket(today_bucket, money_visible),
        "month": _public_bucket(month_bucket, money_visible),
        "last_7_days": _public_bucket(seven_bucket, money_visible),
        "days": day_series,
        "budget": {
            **budgets,
            "per_task_exceeded": exceeded_tasks,
            "daily_exceeded": daily_exceeded,
        },
        "recent": recent,
        "generated_at": now.isoformat(),
    }
    if money_visible:
        prices = _price_from_config(default_config)
        result["pricing_source"] = {
            "model_key": (default_config or {}).get("key", ""),
            "model_name": (default_config or {}).get("model_name", ""),
            "configured": bool(prices),
            "input_price_per_1k": (prices or {}).get("input_price_per_1k"),
            "output_price_per_1k": (prices or {}).get("output_price_per_1k"),
        }
    return result


def evaluate_after_task(settings: Settings, task_id: str) -> dict:
    """报告写完后检查单任务/当日预算。只发通知，不暂停或回滚任何任务。"""
    now = _now_local()
    day_start = datetime.combine(now.date(), time.min).replace(tzinfo=TZ)
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT title, report, submitted_by FROM v3_tasks "
            "WHERE id = %s AND task_type = 'investigation'",
            (task_id,),
        ).fetchone()
        if not row:
            return {"checked": False, "reason": "task_not_found"}
        report = row[1] if isinstance(row[1], dict) else {}
        usage = _usage(report)
        if usage["total_tokens"] <= 0:
            return {"checked": False, "reason": "no_token_usage"}
        budgets = _budget_values(conn)
        today_rows = conn.execute(
            """
            SELECT id::text, title, updated_at, report
              FROM v3_tasks
             WHERE task_type = 'investigation'
               AND report IS NOT NULL
               AND updated_at >= %s
            """,
            (day_start.astimezone(UTC),),
        ).fetchall()
    today_items = _rows_with_metrics(
        [
            {
                "id": str(item[0]),
                "title": str(item[1] or ""),
                "updated_at": item[2],
                "report": item[3] if isinstance(item[3], dict) else {},
            }
            for item in today_rows
        ],
        {},
        None,
    )
    daily_total = sum(item["usage"]["total_tokens"] for item in today_items)
    title = str(row[0] or "排查任务")
    notified: list[str] = []

    if budgets["per_task_tokens"] > 0 and usage["total_tokens"] > budgets["per_task_tokens"]:
        body = (
            f"排查「{title}」本次使用 {usage['total_tokens']} token，"
            f"超过单任务预算 {budgets['per_task_tokens']}。预算只提醒，任务未停止。"
        )
        if notifications.notify_token_budget(
            settings, task_id=task_id, title="单任务 Token 预算超限",
            body=body, dedupe_key=f"token_budget_task:{task_id}",
        ):
            notified.append("per_task")

    if budgets["per_day_tokens"] > 0 and daily_total > budgets["per_day_tokens"]:
        body = (
            f"今日排查累计使用 {daily_total} token，"
            f"超过每日预算 {budgets['per_day_tokens']}。预算只提醒，任务未停止。"
        )
        if notifications.notify_token_budget(
            settings, task_id=task_id, title="今日 Token 预算已超限",
            body=body, dedupe_key=f"token_budget_day:{now.date().isoformat()}",
        ):
            notified.append("per_day")

    return {
        "checked": True,
        "task_tokens": usage["total_tokens"],
        "today_tokens": daily_total,
        "budgets": budgets,
        "notified": notified,
    }
