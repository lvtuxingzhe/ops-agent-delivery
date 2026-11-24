"""
把现有凭据从当前后端迁移到数据库加密存储。

用法（在 code/ 目录下）：
    python backend/scripts/migrate_credentials_to_db.py --dry-run
    python backend/scripts/migrate_credentials_to_db.py --apply
    python backend/scripts/migrate_credentials_to_db.py --verify
    python backend/scripts/migrate_credentials_to_db.py --switch

行为：
    * --dry-run  只列引用名、源后端是否有值、数据库是否已有；不写任何东西。
    * --apply    把源后端能取到的值加密写进 v3_credential_secrets。
    * --verify   逐条解密回读，和源后端比对，只打印 PASS/FAIL，不打印密码。
    * --switch   先 verify，再把 v3_configs 的 backend 从当前后端改成 db。
    * --rollback 把 backend 改回 openbao（数据库里的密文保留，不删除）。

安全：
    * 全程不打印任何凭据值；
    * 迁移脚本只写密文，明文只在进程内存里出现；
    * CREDENTIAL_ENCRYPTION_KEY 必须已在 .env / 环境变量里配置。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CODE = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.core.audit import AuditEvent, insert_audit_event  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.credentials import (  # noqa: E402
    CONFIG_KEY,
    NAMESPACE,
    _fetch_db,
    clear_cache,
    load_config,
    resolve,
    write_db_secret,
)
from app.core.db import connect_app  # noqa: E402

TABLE = "v3_credential_secrets"


def _refs(settings) -> list[str]:
    with connect_app(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT credential_ref FROM v3_configs "
            "WHERE credential_ref IS NOT NULL AND credential_ref <> '' "
            "ORDER BY credential_ref",
        ).fetchall()
    return [str(row[0]) for row in rows]


def _db_refs(settings) -> set[str]:
    with connect_app(settings) as conn:
        rows = conn.execute(f"SELECT ref FROM {TABLE}").fetchall()
    return {str(row[0]) for row in rows}


def _source_values(settings, refs: list[str]) -> dict[str, tuple[str | None, str, str]]:
    """返回 ref -> (值, status, detail)，只在本进程内存里用。"""
    config = load_config(settings)
    values: dict[str, tuple[str | None, str, str]] = {}
    for ref in refs:
        result = resolve(ref, config=config)
        value = result.value.reveal() if result.value is not None else None
        values[ref] = (value, result.status, result.detail)
    return values


def _source_error(status: str, detail: str) -> bool:
    """取数失败/未接入不能当成「原本就没有值」，否则会跳过迁移并误切 db。"""
    return status != "ok" and (
        "取数失败" in detail or "未接入" in detail or status not in ("no_data",)
    )


def _print_inventory(settings, refs: list[str],
                     values: dict[str, tuple[str | None, str, str]]) -> None:
    existing = _db_refs(settings)
    config = load_config(settings)
    print(f"source_backend={config.backend}")
    for ref in refs:
        value, status, detail = values[ref]
        print(
            f"{ref} | source_status={status} | source_has_value={value is not None} "
            f"| db_has_value={ref in existing} | detail={detail}"
        )


def _apply(settings, refs: list[str],
           values: dict[str, tuple[str | None, str, str]]) -> int:
    written = 0
    failed: list[str] = []
    for ref in refs:
        value, status, detail = values[ref]
        if value is None:
            if _source_error(status, detail):
                failed.append(ref)
                print(f"[ERROR] {ref} | 源后端取数失败：{detail}")
            else:
                print(f"[SKIP] {ref} | 源后端确实没有值")
            continue
        result = write_db_secret(ref, value, settings, actor_id="migrate")
        print(f"[APPLY] {ref} | key_version={result['key_version']}")
        written += 1
    clear_cache()
    print(f"[APPLY] written={written} failed={len(failed)}")
    if failed:
        raise RuntimeError("有引用取数失败，先修源后端再迁移：" + ", ".join(failed))
    return written


def _verify(settings, refs: list[str],
            source: dict[str, tuple[str | None, str, str]]) -> bool:
    ok = True
    for ref in refs:
        expected, status, detail = source[ref]
        actual = _fetch_db(ref, load_config(settings), settings)
        if expected is None and _source_error(status, detail):
            ok = False
            print(f"[VERIFY] {ref} | FAIL（源后端取数失败：{detail}）")
            continue
        if expected is None:
            print(f"[VERIFY] {ref} | SKIP（源后端确实没有值）")
            continue
        if actual == expected:
            print(f"[VERIFY] {ref} | PASS")
        else:
            ok = False
            print(f"[VERIFY] {ref} | FAIL")
    return ok


def _switch(settings, source_backend: str, refs: list[str]) -> None:
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s FOR UPDATE",
                (NAMESPACE, CONFIG_KEY),
            ).fetchone()
            if not row or not isinstance(row[0], dict):
                raise RuntimeError("凭据配置行不存在，先跑 init_credentials.py")
            value = dict(row[0])
            value["backend"] = "db"
            value["db_migrated_from"] = source_backend
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb, updated_by=%s, updated_at=now() "
                "WHERE namespace=%s AND key=%s",
                (json.dumps(value, ensure_ascii=False, sort_keys=True),
                 None, NAMESPACE, CONFIG_KEY),
            )
            insert_audit_event(conn, AuditEvent(
                trace_id="credentials-migrate-to-db",
                event_type="config.credentials.migrated_to_db",
                actor="migrate",
                client_source="script",
                payload={"from": source_backend, "to": "db", "refs": refs},
            ))
    clear_cache()
    print(f"[SWITCH] backend {source_backend} -> db")


def _rollback(settings) -> None:
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s FOR UPDATE",
                (NAMESPACE, CONFIG_KEY),
            ).fetchone()
            if not row or not isinstance(row[0], dict):
                raise RuntimeError("凭据配置行不存在")
            value = dict(row[0])
            previous = value.get("db_migrated_from") or "openbao"
            value["backend"] = previous
            conn.execute(
                "UPDATE v3_configs SET value=%s::jsonb, updated_by=%s, updated_at=now() "
                "WHERE namespace=%s AND key=%s",
                (json.dumps(value, ensure_ascii=False, sort_keys=True),
                 None, NAMESPACE, CONFIG_KEY),
            )
    clear_cache()
    print(f"[ROLLBACK] backend db -> {previous}")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="只盘点，不写")
    group.add_argument("--apply", action="store_true", help="写入数据库")
    group.add_argument("--verify", action="store_true", help="解密回读比对")
    group.add_argument("--switch", action="store_true", help="切换到 db")
    group.add_argument("--rollback", action="store_true", help="回滚到 openbao")
    args = parser.parse_args()

    load_dotenv(CODE / ".env")
    settings = load_settings()
    if not getattr(settings, "credential_encryption_key", ""):
        print("FAIL：没有配置 CREDENTIAL_ENCRYPTION_KEY，先补到 .env / .env.production")
        return 2

    refs = _refs(settings)
    source = _source_values(settings, refs)
    source_backend = load_config(settings).backend

    if args.apply:
        if source_backend == "db":
            print("[APPLY] 已经是 db，无需重复迁移")
            return 0
        _apply(settings, refs, source)
    if args.verify:
        return 0 if _verify(settings, refs, source) else 1
    if args.switch:
        if source_backend == "db":
            print("[SWITCH] 已经是 db，不重复切换")
            return 0
        if not _verify(settings, refs, source):
            print("FAIL：回读不一致，拒绝切换")
            return 1
        _switch(settings, source_backend, refs)
        return 0
    if args.rollback:
        _rollback(settings)
        return 0
    _print_inventory(settings, refs, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
