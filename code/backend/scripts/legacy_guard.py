"""
旧模型脚本的退役校验。

008（删 20 张旧表）执行之后，一批「把旧表数据搬到 v3」的一次性脚本
（init_auth 旧种子 / init_contract / init_llm / init_samples / selftest_contract）
再跑必然撞 `UndefinedTable`。它们不该报错，也不该假装成功 —— 正确行为是
明确说明「已退役」，退出码 0，让调用方知道这是历史工具。

怎么用：
    from legacy_guard import legacy_present, retired

    with connect_admin(settings) as admin:
        if not legacy_present(admin):
            retired("init_contract")
            return 0
"""

LEGACY_PROBE_TABLE = "tasks"


def legacy_present(connection) -> bool:
    """旧模型是否还在（008 之前为 True，之后为 False）。只查元数据，不碰数据。"""
    return bool(
        connection.execute(
            "SELECT count(*) FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = %s",
            (LEGACY_PROBE_TABLE,),
        ).fetchone()[0]
    )


def retired(script: str) -> None:
    """打印统一的退役说明。不提「失败」二字，避免被监控误判。"""
    print(f"[INIT] {script}：旧模型表已按 008 删除，本脚本退役（不是失败）")
    print("       新环境引导用 init_db.py（001～009）；身份种子看 init_auth 的 v3 部分")
