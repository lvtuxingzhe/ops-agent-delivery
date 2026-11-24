"""
告警统一入口：Prometheus 拉取 → 归一化 → 去重 → 成本闸门 → 只读排查任务。

设计边界
--------
* 本模块只处理 Prometheus 拉取；Alertmanager Webhook 以后复用同一入口。
* 通知和任务创建都在这里，脚本只负责定时触发。
* 自动建单默认关闭；空主机/告警名白名单表示不建单，不是不限。
* 只建只读 investigation 任务；不产生部署计划、不申请执行许可。
* 告警正文是不可信文本，只作为描述字段，不进入系统指令或工具选择。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from ..core.config import Settings
from ..core.db import connect_app
from . import config_service, datasource_adapters, exec_hosts, investigation_service, notifications

logger = logging.getLogger(__name__)
MAX_ALERTS_PER_SOURCE = 50
SOURCE_NAME = "prometheus-pull"
RUNTIME_NAMESPACE = "alert_runtime"
STORM_UNTIL_KEY = "storm_suppressed_until"

_SEVERITY_LEVELS = {
    "critical": "P1",
    "warning": "P2",
    "info": "P3",
}


def _clip(value: Any, limit: int) -> str:
    """告警字段进库前截断，避免一条超长 annotation 撑大任务。"""
    return str(value or "").strip()[:limit]


def _split(value: str) -> list[str]:
    """逗号/换行清单；空项丢掉。"""
    return [item.strip() for item in str(value or "").replace("\n", ",").split(",")
            if item.strip()]


def _group_key(alertname: str, host: str) -> str:
    """稳定去重键：告警名 + 主机，不把 activeAt 混进来。"""
    raw = f"{alertname.strip().lower()}|{host.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _severity_level(severity: str) -> str:
    """Prometheus severity → 平台级别；未知按 P2，和 C0 决策一致。"""
    return _SEVERITY_LEVELS.get(str(severity or "").lower(), "P2")


def _format(item: dict, *, resolved: bool = False) -> tuple[str, str]:
    """把一条告警/恢复变成站内/外发短文本。"""
    name = item.get("alertname", "unknown")
    host = item.get("host") or "未知主机"
    title = (
        f"监控恢复：{name}（{host}）" if resolved
        else f"监控告警：{name}（{host}）"
    )[:120]
    annotations = item.get("annotations") or {}
    lines = [
        f"{'已恢复' if resolved else '告警'}：{name}",
        f"主机：{host}",
        f"级别：{item.get('severity') or '未标注'}",
        f"开始：{item.get('active_at') or item.get('first_seen') or '未知'}",
    ]
    if resolved:
        lines.append(f"恢复：{item.get('resolved_at') or datetime.now(UTC).isoformat()}")
    else:
        lines.extend([f"当前值：{item.get('value') or '-'}"])
        if annotations.get("summary"):
            lines.append(f"摘要：{annotations['summary']}")
        if annotations.get("description"):
            lines.append(f"说明：{annotations['description']}")
    return title, "\n".join(lines)[:500]


def _load_policy(settings: Settings) -> dict:
    """一次读取本轮告警决策需要的开关，避免每条告警都开一次连接。"""
    with connect_app(settings) as conn:
        def value(key: str) -> str:
            return config_service.get_setting(conn, key)

        return {
            "enabled": config_service.is_enabled(conn, "alert_auto_investigation_enabled"),
            "max_per_hour": int(value("alert_auto_max_per_hour")),
            "max_per_day": int(value("alert_auto_max_per_day")),
            "max_concurrent": int(value("alert_auto_max_concurrent")),
            "min_interval_minutes": int(value("alert_auto_min_interval_minutes")),
            "min_firing_seconds": int(value("alert_auto_min_firing_seconds")),
            "allow_severities": set(_split(value("alert_auto_allow_severities"))),
            "allow_hosts": set(_split(value("alert_auto_allow_hosts"))),
            "allow_alertnames": set(_split(value("alert_auto_allow_alertnames"))),
            "flap_window_minutes": int(value("alert_auto_flap_window_minutes")),
            "flap_transitions": int(value("alert_auto_flap_transitions")),
            "flap_cooldown_minutes": int(value("alert_auto_flap_cooldown_minutes")),
            "storm_window_minutes": int(value("alert_auto_storm_window_minutes")),
            "storm_threshold": int(value("alert_auto_storm_threshold")),
            "storm_cooldown_minutes": int(value("alert_auto_storm_cooldown_minutes")),
        }


def _remember_firing(settings: Settings, source_key: str, item: dict,
                     group_key: str, level: str,
                     *, source_name: str = SOURCE_NAME) -> None:
    """写/刷新一条 firing 状态；同源同指纹只刷新 last_seen。"""
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
    with connect_app(settings) as conn:
        conn.execute(
            """
            INSERT INTO v3_monitor_alert_state
                (source_key, fingerprint, alertname, host, severity,
                 state, first_seen, last_seen, resolved_at,
                 group_key, labels, annotations, severity_level, source)
            VALUES (%s, %s, %s, %s, %s, 'firing', now(), now(), NULL,
                    %s, %s, %s, %s, %s)
            ON CONFLICT (source_key, fingerprint) DO UPDATE SET
                alertname = EXCLUDED.alertname,
                host = EXCLUDED.host,
                severity = EXCLUDED.severity,
                state = 'firing',
                last_seen = now(),
                resolved_at = NULL,
                group_key = EXCLUDED.group_key,
                labels = EXCLUDED.labels,
                annotations = EXCLUDED.annotations,
                severity_level = EXCLUDED.severity_level,
                source = EXCLUDED.source
            """,
            (
                source_key, str(item.get("key") or ""),
                _clip(item.get("alertname"), 120), _clip(item.get("host"), 120),
                _clip(item.get("severity"), 40), group_key,
                Jsonb(labels), Jsonb(annotations), level, source_name,
            ),
        )


def _open_firing(settings: Settings, source_key: str) -> list[dict]:
    """读该数据源当前 firing 的状态行，用来判断哪些已经恢复。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT fingerprint, alertname, host, severity, first_seen,
                   group_key, task_id
            FROM v3_monitor_alert_state
            WHERE source_key = %s AND state = 'firing'
            ORDER BY last_seen DESC
            """,
            (source_key,),
        ).fetchall()
    return [
        {
            "key": str(row[0]),
            "alertname": str(row[1] or ""),
            "host": str(row[2] or ""),
            "severity": str(row[3] or ""),
            "first_seen": row[4].isoformat() if row[4] else "",
            "group_key": str(row[5] or ""),
            "task_id": str(row[6]) if row[6] else None,
        }
        for row in rows
    ]


def _mark_resolved(settings: Settings, source_key: str, fingerprint: str) -> None:
    """把一条告警从 firing 改为 resolved；只改状态，不删历史。"""
    with connect_app(settings) as conn:
        conn.execute(
            """
            UPDATE v3_monitor_alert_state
            SET state = 'resolved', last_seen = now(), resolved_at = now()
            WHERE source_key = %s AND fingerprint = %s AND state = 'firing'
            """,
            (source_key, fingerprint),
        )


def _group_has_firing(settings: Settings, group_key: str) -> bool:
    """同组还有任意一个数据源在 firing，就不能发恢复。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT 1 FROM v3_monitor_alert_state "
            "WHERE group_key = %s AND state = 'firing' LIMIT 1",
            (group_key,),
        ).fetchone()
    return row is not None


def _existing_task(settings: Settings, group_key: str) -> str | None:
    """同组已有未关闭任务时复用，不再建新任务。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT s.task_id
              FROM v3_monitor_alert_state s
              JOIN v3_tasks t ON t.id = s.task_id
             WHERE s.group_key = %s
               AND s.task_id IS NOT NULL
               AND t.status <> 'closed'
             ORDER BY s.task_id
             LIMIT 1
            """,
            (group_key,),
        ).fetchall()
    return str(rows[0][0]) if rows else None


def _set_group_task(settings: Settings, group_key: str, task_id: str) -> None:
    """把任务 id 回写到该组所有 firing 状态，后续同组直接复用。"""
    with connect_app(settings) as conn:
        conn.execute(
            "UPDATE v3_monitor_alert_state SET task_id = %s "
            "WHERE group_key = %s AND state = 'firing'",
            (task_id, group_key),
        )


def _last_task_at(settings: Settings, group_key: str) -> datetime | None:
    """该组最近一次自动建单时间，用于最小间隔。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT max(created_at) FROM v3_tasks "
            "WHERE plan->>'source' = %s AND plan->>'group_key' = %s",
            (SOURCE_NAME, group_key),
        ).fetchone()
    return row[0] if row and row[0] else None


def _task_count(settings: Settings, minutes: int) -> int:
    """最近 N 分钟自动建单数，用于每小时/每天上限。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT count(*) FROM v3_tasks "
            "WHERE plan->>'source' = %s "
            "AND created_at >= now() - (%s * interval '1 minute')",
            (SOURCE_NAME, minutes),
        ).fetchone()
    return int(row[0]) if row else 0


def _concurrent_count(settings: Settings) -> int:
    """当前排队/运行的自动任务数，用于并发上限。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT count(*) FROM v3_tasks "
            "WHERE plan->>'source' = %s "
            "AND status IN ('draft', 'running', 'awaiting_approval', 'executing')",
            (SOURCE_NAME,),
        ).fetchone()
    return int(row[0]) if row else 0


def _group_starts(settings: Settings, minutes: int) -> int:
    """最近 N 分钟新出现的不同告警组数，用于风暴判定。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT count(DISTINCT group_key) FROM v3_monitor_alert_state "
            "WHERE first_seen >= now() - (%s * interval '1 minute')",
            (minutes,),
        ).fetchone()
    return int(row[0]) if row else 0


def _group_start_count(settings: Settings, group_key: str, minutes: int) -> int:
    """同一组在窗口内出现的次数，用于波动判定。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT count(DISTINCT date_trunc('minute', first_seen)) "
            "FROM v3_monitor_alert_state "
            "WHERE group_key = %s "
            "AND first_seen >= now() - (%s * interval '1 minute')",
            (group_key, minutes),
        ).fetchone()
    return int(row[0]) if row else 0


def _suppressed_until(settings: Settings, group_key: str) -> datetime | None:
    """该组当前的抑制截止时间。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT max(suppressed_until) FROM v3_monitor_alert_state "
            "WHERE group_key = %s",
            (group_key,),
        ).fetchone()
    return row[0] if row and row[0] else None


def _suppress_group(settings: Settings, group_key: str, minutes: int) -> None:
    """波动冷却：只压制这一组，不影响其他告警。"""
    with connect_app(settings) as conn:
        conn.execute(
            "UPDATE v3_monitor_alert_state "
            "SET suppressed_until = now() + (%s * interval '1 minute') "
            "WHERE group_key = %s AND state = 'firing'",
            (minutes, group_key),
        )


def _storm_until(settings: Settings) -> datetime | None:
    """读全局风暴冷却截止时间。用 v3_configs 存运行态，不新增表。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace = %s AND key = %s",
            (RUNTIME_NAMESPACE, STORM_UNTIL_KEY),
        ).fetchone()
        if not row or not isinstance(row[0], dict) or not row[0].get("until"):
            return None
        try:
            return datetime.fromisoformat(str(row[0]["until"]))
        except ValueError:
            return None


def _set_storm_until(settings: Settings, until: datetime) -> None:
    """写全局风暴冷却；键值表已有，不建第二套运行态表。"""
    with connect_app(settings) as conn:
        conn.execute(
            """
            INSERT INTO v3_configs (namespace, key, value, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (namespace, key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now()
            """,
            (RUNTIME_NAMESPACE, STORM_UNTIL_KEY,
             Jsonb({"until": until.isoformat()})),
        )


def _system_actor(settings: Settings) -> int | None:
    """system-alert 用户 id；没建账号就不自动建单，只发通知。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT id FROM v3_users WHERE username = 'system-alert' "
            "AND is_active LIMIT 1"
        ).fetchone()
    return int(row[0]) if row else None


def _active_age_seconds(item: dict, now: datetime) -> float | None:
    """activeAt 到现在的秒数；无法解析时返回 None，不因此误杀。"""
    raw = str(item.get("active_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (now - parsed).total_seconds()


def _normalize_host_token(value: str) -> str:
    """instance 可能是 192.0.2.153:9100，去掉端口再匹配。"""
    token = str(value or "").strip()
    if token.count(":") == 1:
        host, port = token.rsplit(":", 1)
        if port.isdigit():
            return host
    return token


def _match_host(settings: Settings, item: dict) -> tuple[str, str]:
    """返回 (host_id, 匹配到的展示名)；匹配不到返回空。"""
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    token = _normalize_host_token(
        str(item.get("host") or labels.get("host") or labels.get("instance") or "")
    )
    if not token:
        return "", ""
    try:
        hosts = exec_hosts.list_hosts(settings)
    except Exception:  # noqa: BLE001 - 主机清单读不到就不建 SSH 任务
        return "", token
    wanted = token.lower()
    for host in hosts:
        if not host.get("enabled"):
            continue
        fields = {
            str(host.get(field) or "").lower()
            for field in ("id", "name", "address")
            if host.get(field)
        }
        if wanted in fields:
            return str(host.get("id") or ""), token
    return "", token


def _gate_reason(settings: Settings, policy: dict, item: dict, group_key: str,
                 host_id: str, host_token: str, level: str,
                 actor_id: int | None, now: datetime,
                 storm_active: bool) -> str:
    """返回拒绝原因；空字符串表示可以建单。"""
    if not policy["enabled"]:
        return "disabled"
    severity = str(item.get("severity") or "").lower()
    if severity not in policy["allow_severities"]:
        return "severity_not_allowed"
    allowed_hosts = {value.lower() for value in policy["allow_hosts"]}
    if not host_id:
        # 匹配不到已登记主机：只有管理员显式写 "*" 才允许建单；
        # 这时任务走 no_ssh，报告只能写“主机未登记”，绝不猜主机。
        if "*" not in allowed_hosts:
            return "host_unmapped"
    elif "*" not in allowed_hosts and \
            host_id.lower() not in allowed_hosts and \
            (host_token or "").lower() not in allowed_hosts:
        return "host_not_allowlisted"
    alertname = _clip(item.get("alertname"), 120)
    if not alertname or alertname.lower() not in {
        value.lower() for value in policy["allow_alertnames"]
    }:
        return "alertname_not_allowlisted"
    if actor_id is None:
        return "system_account_missing"
    if storm_active:
        return "storm_cooldown"
    age = _active_age_seconds(item, now)
    if age is not None and age < policy["min_firing_seconds"]:
        return "too_short"
    if _group_start_count(settings, group_key, policy["flap_window_minutes"]) >= \
            policy["flap_transitions"]:
        _suppress_group(settings, group_key, policy["flap_cooldown_minutes"])
        return "flapping"
    until = _suppressed_until(settings, group_key)
    if until and until > now:
        return "group_suppressed"
    if _existing_task(settings, group_key):
        return "existing_task"
    last = _last_task_at(settings, group_key)
    if last and last > now - timedelta(minutes=policy["min_interval_minutes"]):
        return "min_interval"
    if _task_count(settings, 60) >= policy["max_per_hour"]:
        return "hourly_limit"
    if _task_count(settings, 1440) >= policy["max_per_day"]:
        return "daily_limit"
    if _concurrent_count(settings) >= policy["max_concurrent"]:
        return "concurrency_limit"
    return ""


def _create_task(settings: Settings, item: dict, group_key: str, level: str,
                 host_id: str, host_token: str, actor_id: int) -> str | None:
    """调用排查服务建只读任务；失败只返回 None，不影响通知链路。"""
    alertname = _clip(item.get("alertname"), 120) or "unknown"
    title = f"[告警] {alertname} @ {host_token or '未知主机'}"[:120]
    annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
    description = "\n".join([
        f"告警名：{alertname}",
        f"级别：{item.get('severity') or '未标注'}（{level}）",
        f"主机：{host_token or '未知主机'}",
        f"当前值：{_clip(item.get('value'), 200) or '-'}",
        f"摘要：{_clip(annotations.get('summary'), 200) or '-'}",
        f"开始：{item.get('active_at') or '未知'}",
        f"去重键：{group_key}",
    ])[:500]
    scope = {"host_id": host_id} if host_id else {"no_ssh": True, "alert_host": host_token}
    try:
        result = investigation_service.submit_task(
            settings,
            title=title,
            description=description,
            target_scope=scope,
            actor_id=actor_id,
            client_source="alert",
            origin={
                "source": SOURCE_NAME,
                "read_only": True,
                "actor_label": "system-alert",
                "group_key": group_key,
                "fake": False,
                "note": "告警触发；只读取证",
                "alert": {
                    "alertname": alertname,
                    "host": host_token,
                    "severity": _clip(item.get("severity"), 40),
                    "level": level,
                    "active_at": _clip(item.get("active_at"), 40),
                    "summary": _clip(annotations.get("summary"), 200),
                },
            },
        )
        task_id = str(result.get("id") or "")
    except Exception:  # noqa: BLE001 - 建单失败不影响告警通知
        logger.exception("告警自动建单失败 group=%s", group_key)
        return None
    if not task_id:
        return None
    _set_group_task(settings, group_key, task_id)
    return task_id


def _notify_item(settings: Settings, item: dict, group_key: str,
                 task_id: str | None) -> int:
    """发一条告警通知；event_key 保证同一 firing 事件只发一次。"""
    alert_key = group_key
    event_key = (
        f"{group_key}:{item.get('active_at')}"
        if item.get("active_at") else f"{group_key}:{item.get('key') or 'unknown'}"
    )
    title, body = _format(item)
    return notifications.notify_monitor_alert(
        settings, alert_key=alert_key, title=title, body=body,
        task_id=task_id, event_key=event_key,
    )


def _notify_storm(settings: Settings, count: int, until: datetime) -> int:
    """风暴只发一条汇总通知；事件键用冷却截止时间，避免每轮重复。"""
    title = "监控告警风暴，已暂停自动排查"
    body = (
        f"最近出现 {count} 个不同告警组，平台已暂停自动建单，"
        f"截止 {until.astimezone(UTC).isoformat()}。"
        "告警仍会继续接收，恢复后按闸门逐步放开。"
    )[:500]
    return notifications.notify_monitor_alert(
        settings, alert_key="storm", title=title, body=body,
        event_key=f"storm:{until.isoformat()}",
    )


def _maybe_create_task(settings: Settings, policy: dict, item: dict,
                       group_key: str, level: str, host_id: str, host_token: str,
                       actor_id: int | None, now: datetime,
                       storm_active: bool) -> str | None:
    """通过所有闸门才建单；拒绝原因只记日志，不写敏感内容。"""
    reason = _gate_reason(
        settings, policy, item, group_key, host_id, host_token,
        level, actor_id, now, storm_active,
    )
    if reason:
        logger.info("告警未建单 group=%s reason=%s", group_key, reason)
        return None
    assert actor_id is not None
    return _create_task(settings, item, group_key, level, host_id, host_token, actor_id)


def sync_once(settings: Settings, *, limit: int = MAX_ALERTS_PER_SOURCE) -> dict:
    """
    同步一轮所有启用中的真实 Prometheus 数据源。

    两阶段：
      1. 拉取并写状态；失败源整源跳过，不误发恢复；
      2. 全局风暴判定后，再逐条决定是否建单和通知。
    """
    result = {
        "datasources": 0,
        "alerts": 0,
        "firing": 0,
        "resolved": 0,
        "notifications": 0,
        "tasks": 0,
        "skipped": 0,
        "errors": [],
        "storm": False,
    }
    policy = _load_policy(settings)
    now = datetime.now(UTC)
    observations: list[dict] = []

    for ds in config_service.list_datasources(settings):
        if (
            not ds.get("enabled")
            or ds.get("kind") != "prometheus"
            or str(ds.get("key", "")).startswith("样例-")
        ):
            continue
        result["datasources"] += 1
        try:
            payload = datasource_adapters.alerts(
                settings,
                "prometheus",
                ds["endpoint"],
                credential_ref=ds.get("credential_ref", ""),
                limit=limit,
            )
        except datasource_adapters.AdapterError as exc:
            result["errors"].append({"source": ds["key"], "detail": str(exc)})
            logger.warning("拉取 Prometheus 告警失败 source=%s detail=%s", ds["key"], exc)
            continue

        visible_keys: set[str] = set()
        items: list[dict] = []
        for raw in payload.get("items", []):
            result["alerts"] += 1
            item = dict(raw)
            key = str(item.get("key") or "")
            if key:
                visible_keys.add(key)
            if item.get("state") != "firing":
                result["skipped"] += 1
                continue
            result["firing"] += 1
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            host = _normalize_host_token(
                str(item.get("host") or labels.get("host") or labels.get("instance") or "")
            )
            item["host"] = host
            item["group_key"] = _group_key(_clip(item.get("alertname"), 120), host)
            level = _severity_level(str(item.get("severity") or ""))
            try:
                _remember_firing(settings, ds["key"], item, item["group_key"], level)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append({
                    "source": ds["key"],
                    "detail": f"state_write:{type(exc).__name__}",
                })
                logger.exception("写告警状态失败 source=%s", ds["key"])
            items.append(item)

        observations.append({
            "source": ds["key"],
            "visible": visible_keys,
            "items": items,
        })

    # 全局风暴：最近窗口内新出现的不同 group 达到阈值，暂停自动建单。
    storm_active = False
    if policy["enabled"]:
        storm_until = _storm_until(settings)
        if storm_until and storm_until > now:
            storm_active = True
            result["storm"] = True
        else:
            count = _group_starts(settings, policy["storm_window_minutes"])
            if count >= policy["storm_threshold"]:
                until = now + timedelta(minutes=policy["storm_cooldown_minutes"])
                _set_storm_until(settings, until)
                storm_active = True
                result["storm"] = True
                inserted = _notify_storm(settings, count, until)
                result["notifications"] += inserted

    actor_id = _system_actor(settings)
    for observation in observations:
        for item in observation["items"]:
            group_key = str(item["group_key"])
            level = _severity_level(str(item.get("severity") or ""))
            host_id, host_token = _match_host(settings, item)
            task_id = _existing_task(settings, group_key)
            if task_id is None:
                task_id = _maybe_create_task(
                    settings, policy, item, group_key, level,
                    host_id, host_token, actor_id, now, storm_active,
                )
            if task_id:
                result["tasks"] += 1
            # 风暴期间不逐条通知，只保留一条汇总，避免通知和页面被刷屏。
            if storm_active:
                result["skipped"] += 1
                continue
            inserted = _notify_item(settings, item, group_key, task_id)
            if inserted:
                result["notifications"] += inserted
            else:
                result["skipped"] += 1

    # 每个源本轮完全没出现的旧 firing → 恢复；同组还有 firing 时不发恢复。
    for observation in observations:
        source_key = observation["source"]
        try:
            open_rows = _open_firing(settings, source_key)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append({
                "source": source_key,
                "detail": f"state_read:{type(exc).__name__}",
            })
            logger.exception("读告警状态失败 source=%s", source_key)
            continue
        for row in open_rows:
            if row["key"] in observation["visible"]:
                continue
            group_key = row["group_key"] or _group_key(row["alertname"], row["host"])
            try:
                _mark_resolved(settings, source_key, row["key"])
            except Exception as exc:  # noqa: BLE001
                result["errors"].append({
                    "source": source_key,
                    "detail": f"state_resolve:{type(exc).__name__}",
                })
                logger.exception("标记告警恢复失败 source=%s", source_key)
                continue
            if _group_has_firing(settings, group_key):
                continue
            result["resolved"] += 1
            title, body = _format(row, resolved=True)
            inserted = notifications.notify_monitor_alert(
                settings, alert_key=group_key, title=title, body=body,
                resolved=True, task_id=row.get("task_id"),
                event_key=f"{group_key}:{row.get('first_seen') or row['key']}",
            )
            if inserted:
                result["notifications"] += inserted
            else:
                result["skipped"] += 1

    return result


WEBHOOK_SOURCE = "alertmanager-webhook"


def _webhook_item(alert: dict[str, Any]) -> dict[str, Any]:
    """Alertmanager 的一条 alert → 与 Prometheus 拉取一致的字段。"""
    labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
    annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
    alertname = _clip(labels.get("alertname") or annotations.get("title"), 120) or "unknown"
    host = _normalize_host_token(
        str(labels.get("host") or labels.get("instance") or labels.get("node") or "")
    )
    fingerprint = _clip(
        alert.get("fingerprint")
        or hashlib.sha256(
            "|".join(
                f"{key}={labels[key]}"
                for key in sorted(labels)
            ).encode("utf-8")
        ).hexdigest()[:32],
        120,
    )
    status = str(alert.get("status") or "firing").lower()
    return {
        "key": fingerprint,
        "alertname": alertname,
        "host": host,
        "severity": _clip(labels.get("severity"), 40),
        "value": _clip(annotations.get("value") or labels.get("value"), 200),
        "active_at": _clip(alert.get("startsAt") or alert.get("starts_at"), 40),
        "resolved_at": _clip(alert.get("endsAt") or alert.get("ends_at"), 40),
        "state": "firing" if status == "firing" else "resolved",
        "labels": labels,
        "annotations": annotations,
    }


def _group_task(settings: Settings, group_key: str) -> str | None:
    """该组最近一次自动建单，回复恢复通知时带任务链接。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT task_id FROM v3_monitor_alert_state "
            "WHERE group_key = %s AND task_id IS NOT NULL "
            "ORDER BY last_seen DESC LIMIT 1",
            (group_key,),
        ).fetchone()
    return str(row[0]) if row else None


def ingest_webhook(settings: Settings, payload: dict[str, Any]) -> dict:
    """Alertmanager Webhook：与拉取入口共用去重、分级、白名单和只读建单。"""
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        raise ValueError("alerts_required")
    if len(alerts) > MAX_ALERTS_PER_SOURCE:
        raise ValueError(f"alerts_too_many：一次最多 {MAX_ALERTS_PER_SOURCE} 条")

    result = {
        "received": len(alerts),
        "firing": 0,
        "resolved": 0,
        "notifications": 0,
        "tasks": 0,
        "skipped": 0,
    }
    policy = _load_policy(settings)
    actor_id = _system_actor(settings)
    now = datetime.now(UTC)
    storm_active = False
    if policy["enabled"]:
        storm_until = _storm_until(settings)
        if storm_until and storm_until > now:
            storm_active = True

    for raw in alerts:
        if not isinstance(raw, dict):
            result["skipped"] += 1
            continue
        item = _webhook_item(raw)
        group_key = _group_key(item["alertname"], item["host"])
        item["group_key"] = group_key
        level = _severity_level(item["severity"])

        if item["state"] == "resolved":
            result["resolved"] += 1
            _mark_resolved(settings, WEBHOOK_SOURCE, item["key"])
            if _group_has_firing(settings, group_key):
                result["skipped"] += 1
                continue
            title, body = _format(item, resolved=True)
            inserted = notifications.notify_monitor_alert(
                settings, alert_key=group_key, title=title, body=body,
                resolved=True, task_id=_group_task(settings, group_key),
                event_key=f"{group_key}:{item['active_at'] or item['key']}",
            )
            result["notifications"] += inserted
            if not inserted:
                result["skipped"] += 1
            continue

        result["firing"] += 1
        try:
            _remember_firing(
                settings, WEBHOOK_SOURCE, item, group_key, level,
                source_name=WEBHOOK_SOURCE,
            )
        except Exception:  # noqa: BLE001 - 单条状态写失败不丢整批
            logger.exception("写 Webhook 告警状态失败 group=%s", group_key)
            result["skipped"] += 1
            continue
        host_id, host_token = _match_host(settings, item)
        task_id = _existing_task(settings, group_key)
        if task_id is None:
            task_id = _maybe_create_task(
                settings, policy, item, group_key, level,
                host_id, host_token, actor_id, now, storm_active,
            )
        if task_id:
            result["tasks"] += 1
        if storm_active:
            result["skipped"] += 1
            continue
        inserted = _notify_item(settings, item, group_key, task_id)
        result["notifications"] += inserted
        if not inserted:
            result["skipped"] += 1

    return result


__all__ = [
    "ingest_webhook", "sync_once", "MAX_ALERTS_PER_SOURCE", "SOURCE_NAME",
    "WEBHOOK_SOURCE",
]
