"""
库引导（001～017 迁移）与 v3 审计自检。

怎么用（在 code/ 目录下）
-------------------------
    python backend/scripts/init_db.py             # 建表 + 建角色 + 授权 + 实测
    python backend/scripts/init_db.py --verify    # 只跑实测（表已建好时）

前置：`docker compose --profile infra up -d` 已把 PostgreSQL 起起来。
凭据全部来自 code/.env 或环境变量，代码里没有任何地址、账号、密码。

这个脚本做什么
-----------------------
旧版本建的是 `audit_events` 那套审计记录，还带哈希链自检；
审计统一到 `v3_audit_events` 之后，旧审计记录已不再被应用写入，
本脚本对应做这些事：

  * 迁移按文件名顺序跑 001→007（跳过 rollback 与 008 删表脚本）；
  * 审计自检对象改成 `v3_audit_events`（无哈希链，强一致仍靠同事务）；
  * 负向权限、整笔回滚两条约束照旧实测。

为什么「建角色 / 授权」放在 Python 里而不写进 SQL
------------------------------------------------
角色名和密码都是配置项（部署时要能改），不能写死在 .sql 文件里。
这里用 psycopg.sql 的 Identifier / Literal 安全拼装：
  * Identifier 转义标识符，防止角色名里夹带 SQL；
  * Literal 转义密码，防止引号或分号把语句截断。

数据库身份
----------
  管理账号        建表、建角色、授权；只有本脚本用
  ops_app         日常业务；对 v3_audit_events 【只有 INSERT】，连读都不给
  ops_audit_reader 只用来按 trace_id 查审计记录，别的一概不给

【本脚本会做什么】只操作本机数据库：建表、建角色、授权、写几条自检数据。
【不会做什么】不连任何真实环境、不连生产、不删表、不清历史数据。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# 让脚本能 import backend/app 下的模块（backend/ 加到 sys.path）
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import psycopg
from app.core import audit_v3
from app.core.audit import AuditEvent
from app.core.config import load_dotenv, load_settings
from app.core.contract import CLOSED, DRAFT, RUNNING
from app.core.db import connect_admin, connect_app, connect_audit_reader
from psycopg import sql

MIGRATIONS = BACKEND / "migrations"
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """和另外两个自检脚本保持一样的输出风格，方便对照。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def migration_files() -> list[Path]:
    """
    按文件名顺序取正向迁移：跳过 rollback。

    008（删旧表）已由负责人确认并执行：新环境引导同样要跑它，
    否则「001～007 建旧表 → 007 搬数据 → 008 删表」这条收敛链会停在半路，
    新库会永久留着 20 张空旧表，与已上线的库结构不一致。
    """
    return sorted(
        path for path in MIGRATIONS.glob("*.sql")
        if not path.name.endswith(".rollback.sql")
    )


# ---------------------------------------------------------------- 初始化
def ensure_roles(settings) -> None:
    """
    只建/同步登录角色，不碰任何表权限。

    为什么必须单独拆出来：
      031/033 迁移里有 `GRANT ... TO ops_app`，PostgreSQL 在角色不存在时
      会直接报 `role "ops_app" does not exist`，后续迁移全部中断。
      所以角色身份必须先于迁移创建；表权限仍等迁移建完表后再统一授。
    """
    if not settings.db_admin_url:
        raise RuntimeError("缺少 DB_ADMIN_URL，请检查 code/.env")
    roles = (
        (settings.db_app_role, settings.db_app_password),
        (settings.db_audit_reader_role, settings.db_audit_reader_password),
    )
    with connect_admin(settings) as admin:
        for role_name, password in roles:
            if not password:
                raise RuntimeError(f"缺少角色 {role_name} 的密码环境变量，拒绝建无密码账号")
            ident = sql.Identifier(role_name)
            exists = admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)
            ).fetchone()
            if exists:
                # 重复跑时把密码同步成 .env 里的值，避免「改了配置不生效」
                admin.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        ident, sql.Literal(password)
                    )
                )
            else:
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        ident, sql.Literal(password)
                    )
                )
    print(f"[INIT] 数据库角色已就位：{settings.db_app_role} / {settings.db_audit_reader_role}")


def init_schema(settings) -> None:
    """先建角色，再按文件名顺序执行 001～033 迁移（含 008 删旧表、031/032 评论、033 知识指标）。"""
    if not settings.db_admin_url:
        raise RuntimeError("缺少 DB_ADMIN_URL，请检查 code/.env")
    # 必须先建角色：031/033 的 GRANT 在空库里会引用 ops_app。
    ensure_roles(settings)
    files = migration_files()
    with connect_admin(settings) as admin:
        for path in files:
            # 一份文件可能含多条语句；无参数时走简单查询协议，可整份执行
            admin.execute(path.read_text(encoding="utf-8"))
            print(f"[INIT] 已应用 {path.name}")
    print(f"[INIT] 迁移就位：{len(files)} 份")


def init_roles(settings) -> None:
    """建两个数据库角色并授权。幂等：角色已存在就只更新密码。"""
    ensure_roles(settings)
    with connect_admin(settings) as admin:
        db_name = admin.execute("SELECT current_database()").fetchone()[0]
        app = sql.Identifier(settings.db_app_role)
        reader = sql.Identifier(settings.db_audit_reader_role)

        # 授权顺序很关键，别调换：
        #   先给业务表全套权限 → 再把审计记录全部收回 → 只还回 INSERT。
        grants = [
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(db_name), app, reader
            ),
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(app, reader),
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(app),
            sql.SQL("REVOKE ALL ON public.v3_audit_events FROM {}").format(app),
            sql.SQL("GRANT INSERT ON public.v3_audit_events TO {}").format(app),
            # bigserial 要 nextval，序列得给 USAGE
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(app),
            # 评论正文不可改、不可物理删除；只允许写软删除两列。
            # 先收回 ALL TABLES 带来的表级 UPDATE/DELETE，再按列授权。
            sql.SQL("REVOKE UPDATE, DELETE ON public.v3_task_comments FROM {}").format(app),
            sql.SQL(
                "GRANT UPDATE (deleted_at, deleted_by) ON public.v3_task_comments TO {}"
            ).format(app),
            # 审计只读账号：只给审计记录的 SELECT，别的表一概不给
            sql.SQL("GRANT SELECT ON public.v3_audit_events TO {}").format(reader),
        ]
        for statement in grants:
            admin.execute(statement)
    print(f"[INIT] 角色与权限已就位：{settings.db_app_role} / {settings.db_audit_reader_role}")


# ---------------------------------------------------------------- 实测
def audit_rows(settings, where: str = "", params: tuple = ()) -> list[dict]:
    """用【审计只读账号】查 v3 审计记录，返回字典列表。"""
    query = (
        "SELECT id, trace_id, event_type, actor, client_source, payload "
        "FROM v3_audit_events"
    )
    if where:
        query += " " + where
    query += " ORDER BY id"
    with connect_audit_reader(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            columns = [desc.name for desc in cur.description]
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]


def verify(settings) -> int:
    """跑三条完成标志：状态能写入数据库、事件能按 trace 查、越权被数据库拒绝。"""
    print("=" * 66)
    print("审计写路径自检（v3 审计记录，只连本机数据库）")
    print("=" * 66)

    trace_id = f"step2-{uuid.uuid4()}"

    # --- 准备一个任务，交给应用账号插入 --------------------------------
    task_id = uuid.uuid4()
    with connect_app(settings) as conn:
        conn.execute(
            """INSERT INTO v3_tasks (id, trace_id, title, task_type, status)
               VALUES (%s, %s, %s, 'investigation', %s)""",
            (task_id, trace_id, "审计自检任务", DRAFT),
        )
        conn.commit()

    # --- 完成标志 1：任务状态能写入数据库，且审计在同一事务里 ----------------
    # 走 audit_v3.commit_transition()：审计 + 状态 + 许可同事务。
    result = audit_v3.commit_transition(
        settings,
        task_id=str(task_id),
        event=AuditEvent(
            trace_id=trace_id,
            event_type="task.running",
            actor="selftest",
            client_source="web",
            payload={"step": "audit-unify", "note": "完成标志实测"},
        ),
        new_status=RUNNING,
    )
    record("任务状态能写入数据库（v3_tasks）", result["status"] == RUNNING,
           f"status={result['status']}")

    # --- 完成标志 2：写一条事件能按 trace_id 查出来 ---------------------
    matched = audit_rows(settings, "WHERE trace_id = %s", (trace_id,))
    record(
        "写一条事件能按 trace_id 查出来",
        len(matched) == 1 and matched[0]["event_type"] == "task.running",
        f"trace_id={trace_id[:18]}… 查到 {len(matched)} 条",
    )

    # --- 完成标志 3：应用账号改不了审计表 -------------------------------
    attacks = [
        ("SELECT", "SELECT * FROM public.v3_audit_events LIMIT 1"),
        ("UPDATE", "UPDATE public.v3_audit_events SET actor = 'hacked'"),
        ("DELETE", "DELETE FROM public.v3_audit_events"),
        ("TRUNCATE", "TRUNCATE public.v3_audit_events"),
    ]
    for label, statement in attacks:
        denied = False
        with connect_app(settings) as conn:
            try:
                conn.execute(statement)
                conn.rollback()  # 万一真让它执行了，立刻回滚，不污染审计记录
            except psycopg.Error:
                denied = True
                conn.rollback()
        record(f"应用账号不能 {label} 审计表", denied, "数据库拒绝" if denied else "竟然成功了")

    # --- 审计写入失败时不执行（整笔回滚） -------------------------
    # 做法：用管理账号临时撤掉应用账号对 v3 审计表的 INSERT，
    #       再走一遍正常的业务动作，看状态有没有偷偷写入数据库。
    #       finally 里一定会把权限还回去，不会把数据库留在坏状态。
    revoke = sql.SQL("REVOKE INSERT ON public.v3_audit_events FROM {}").format(
        sql.Identifier(settings.db_app_role)
    )
    restore = sql.SQL("GRANT INSERT ON public.v3_audit_events TO {}").format(
        sql.Identifier(settings.db_app_role)
    )
    blocked = False
    with connect_admin(settings) as admin:
        admin.execute(revoke)
    try:
        try:
            audit_v3.commit_transition(
                settings,
                task_id=str(task_id),
                event=AuditEvent(
                    trace_id=f"{trace_id}-blocked",
                    event_type="task.closed",
                    actor="selftest",
                ),
                new_status=CLOSED,
                outcome="succeeded",
                permit={"permit_fingerprint": "should-not-exist"},
            )
        except Exception:  # noqa: BLE001 - 被数据库拒绝就是期望结果
            blocked = True
    finally:
        with connect_admin(settings) as admin:
            admin.execute(restore)

    with connect_app(settings) as conn:
        after = conn.execute(
            "SELECT status, approval FROM v3_tasks WHERE id = %s", (task_id,)
        ).fetchone()
    blocked_events = audit_rows(settings, "WHERE trace_id = %s", (f"{trace_id}-blocked",))
    rollback_ok = (
        blocked
        and after[0] == RUNNING                     # 状态没被偷偷推进
        and not (after[1] or {}).get("permit")       # 执行许可没落
        and not blocked_events                       # 审计也没留下半条
    )
    record(
        "审计写不进去就不执行（整笔回滚）",
        rollback_ok,
        f"审计被拒={blocked} · 状态仍为 {after[0]} · 许可={bool((after[1] or {}).get('permit'))}"
        f" · 残留事件 {len(blocked_events)}",
    )

    # --- 收尾：删掉自检任务（审计事件按设计只追加，保留留痕） -----------
    with connect_app(settings) as conn:
        conn.execute("DELETE FROM v3_tasks WHERE id = %s", (task_id,))
        conn.commit()

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


def main() -> int:
    """按顺序跑完检查，打印汇总并返回退出码。"""
    load_dotenv()
    settings = load_settings()
    verify_only = "--verify" in sys.argv
    seed_only = "--seed-only" in sys.argv
    if not verify_only:
        init_schema(settings)
        init_roles(settings)
    if seed_only:
        print("[INIT] --seed-only：只建表和角色，不写自检任务与审计事件")
        return 0
    return verify(settings)


if __name__ == "__main__":
    sys.exit(main())

