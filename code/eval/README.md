# 运维 Agent 评估

这是 `ops-agent-delivery` 的离线评估集，用来回答三个问题：

1. 排查任务在正常、无数据和日志注入下，是否稳定产出“有证据的结论”；
2. 部署任务在成功、precheck/verify/apply/rollback 失败时，是否按五阶段语义执行和收敛；
3. 安全校验（阶段 scope、只读阶段变更命令、命令前缀、退出码期望、plan_hash）是否真的拒绝。

评估不另造 Agent：排查走 `investigation_service.submit_task` + 队列 worker + `get_report`；部署走 `deployment_service.create_deployment` → `confirm_plan` → `approve_plan` → `get_deployment`。

## 运行

```powershell
cd code
.\backend\.venv\Scripts\python.exe eval\run_eval.py --suite smoke --runs 1
.\backend\.venv\Scripts\python.exe eval\run_eval.py --suite full --runs 3
```

默认输出 `eval/reports/baseline.json` 和同名 `.md`。smoke 套件是前 10 条，覆盖 5 条排查和 5 条部署主路径；full 套件 15 条，追加 5 条安全边界。

评估前会调用 `fixtures.ensure_eval_templates()`，按需创建并发布两个固定装置：

- `eval-five-stage-apply-fail`：apply 执行 `test -f /tmp/ops-agent-eval-apply-fail`，返回 1；
- `eval-five-stage-rollback-fail-v2`：verify 执行 `echo verify-bad` 但期望 `verify-ok`，rollback 执行 `test -f /tmp/ops-agent-eval-rollback-fail`，返回 1。

两个模板都只使用 `echo` / `test -f`，不修改目标机文件；评估任务标题带 `[eval]` 前缀，`target_scope.eval_run_id` 记录批次，不清理历史任务。

## 字段规则

- `status`：任务状态；排查最终 `report_ready`，部署最终 `closed`。
- `outcome` / `reason_code`：部署任务的业务结果和收敛失败码。
- `failure`：部署任务的结构化失败对象；`failure.rollback_ok` 表示回滚是否成功。
- `exec.precheck_results` / `results` / `verify_results` / `rollback_results` / `post_rollback_results`：五阶段逐条执行结果；`results` 是 apply。
- `expect_ok`：命令退出码成功且业务期望满足；`ok` 只表示退出码 0。
- `report.conclusions[].evidence_ids`：排查结论引用的证据；`report.data_quality` 区分有数据/无数据。

## 指标

- `pass@1`：每条用例第一次运行是否通过；
- `pass^k`：每条用例 k 次运行是否全部通过；
- `run_pass_rate`：所有运行中的通过比例；
- 报告保留每条失败的结构化原因，不保存模型原始上下文、命令 stdout/stderr 全量和凭据。

## RAG 检索评估

```powershell
cd code
.\backend\.venv\Scripts\python.exe eval\run_rag_eval.py
```

- 用例：`eval/rag_cases.json`，按 `expected_titles` 判断命中，不绑定条目 ID；
- 指标：`Recall@5`、`MRR`、`empty_retrieval_rate`、`dangerous_false_recall_rate`；
- `dangerous_false_recall_rate` 只在标注了 `dangerous_terms` 的用例上统计，无标签显示 `N/A`；
- 当前 30 条种子查询，覆盖端口、systemd、MySQL 复制、Redis、K8s、inode、DNS、防火墙、TLS、Nginx 502；真实故障分布的覆盖尚未验证。
- 报告：`eval/reports/rag-baseline.json` / `.md`。

## 基线结果

- `eval/reports/baseline-3x-final.json` / `.md`：15 条用例 × 3 次；
- `pass@1 = 1.0`、`pass^k = 1.0`、`run_pass_rate = 1.0`，中位耗时 13.863 s；
- 全绿只说明当前用例和边界通过，不代表 Agent 的结论事实正确率已经足够。

## 边界

- 评估依赖本地已启动的队列 worker；worker 未运行时排查用例会超时，不会伪造报告。
- 部署使用测试环境 + 本地无害命令；`eval-five-stage-*` 模板只在测试环境使用。
- 排查用例检查“有结论、有证据、无数据时承认不确定”，不检查结论与真实故障的语义一致；人工标注和事实核对尚未覆盖。
- 评估只读观测，不改业务主流程；运行器与验证器的改动有配套回归测试覆盖。
