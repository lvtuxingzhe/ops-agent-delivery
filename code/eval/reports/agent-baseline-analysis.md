# Agent K8s 回放基线分析（P0）

- 集群：host-152 控制面，host-150/153/154 worker，k3s `v1.28.15+k3s1`
- 用例：6 条真实 K8s 故障快照（CrashLoop、ImagePull、Service selector、Readiness、ConfigMap 缺失、PVC StorageClass 缺失）
- 执行：3 轮 × 6 条 = 18 次；模型 `deepseek-chat`
- 原始结果：`eval/reports/agent-baseline.json`

## 指标

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 根因准确率 | 0.944 | 1 次执行失败；其余 17 次根因全部命中 |
| Verdict 准确率 | 0.944 | 同上 |
| 根因证据支撑率 | 0.944 | 根因引用的证据里至少出现一条期望证据 |
| 报告完整率 | 0.944 | `report_gaps` 为空 |
| 无证据结论率 | 0.000 | 结论都挂了证据编号 |
| 危险命令率 | **0.056** | pvc 用例出现过 `kubectl apply` / `kubectl delete` |
| 摘要守卫失败率 | **0.389** | summary 里出现字面“正常” |
| 缩写证据编号率 | **0.278** | 根因正文写 `k8s:1`、`k8s:5`，不是完整证据 ID |
| 执行失败率 | 0.056 | 1 次 `exceed_max_iters`，未产出结构化报告 |
| 平均工具调用 | 6.94 | 单次 5～10 次 |
| P50 / P95 延迟 | 15.3s / 18.5s | DeepSeek 在线调用 |

## 结论

### 1. 这批用例对根因诊断偏简单

6 条都是单故障、状态字段非常明确的 K8s 场景：Pod 状态里直接写着 `CrashLoopBackOff`、`ImagePullBackOff`、`CreateContainerConfigError`，事件里直接写着 `connection refused`、`configmap not found`、`storageclass not found`。当前单 ReAct Agent 只要按状态取证，就能拿到接近满分的根因准确率。

**这不能证明计划-执行-复核-修订流程会提高根因准确率。** 要证明范式价值，下一批用例必须加入：同现象不同根因、双故障叠加、日志注入、证据缺失、工具超时、事件互相矛盾。首版 6 条不足以做“单 ReAct 太简单”的最终判断。

### 2. 真正暴露的问题是过程可信度，不是“查不出原因”

按严重程度排序：

1. **危险命令建议**：`k8s-pvc-storageclass-missing` 第 1 轮建议了 `kubectl apply` 和 `kubectl delete`。当前 Agent 没有对 `solution` 做写命令硬拦截，只在提示词里要求“走审批”。这必须由代码闸门兜底，不能靠提示词。
2. **循环失控**：`k8s-configmap-missing` 第 1 轮跑满 12 轮仍未产出结构化报告（`exceed_max_iters`）。单 ReAct 没有显式“证据已够、该收口”的停止判据。
3. **证据编号缩写**：17 次成功报告里有 5 次在根因正文写 `k8s:1`、`k8s:2` 这类缩写。`normalize_report` 只校验 `conclusions` / `hypotheses` 的 evidence_id，不校验自由文本里的引用，所以“看起来有证据、实际不可追溯”。
4. **摘要守卫误伤**：summary 里写“nginx 正常启动”“Pod 状态正常”就触发 `FORBIDDEN_IN_SUMMARY=("正常", ...)`。这既说明提示词对“正常”的绝对化用法约束不够，也说明当前守卫规则过宽：把“正常启动”这种过程事实当成绝对判断。
5. **Verdict 抖动**：第 1 轮 crashloop 报告把根因写清了，却把 verdict 降成 `insufficient`，理由是“还缺 command/args”。同一用例第 2、3 轮是 `anomaly`。单 ReAct 对“证据够不够”没有独立复核，容易在“现象明确但深层配置未取到”之间摇摆。

### 3. 计划-执行-复核-修订流程该解决什么

| 现有问题 | P1 对应动作 |
| --- | --- |
| 没有显式计划，靠临场决定查什么 | Planner 一次结构化规划，列出假设、每步目的、完成判据 |
| 证据够不够由执行器自己判断，verdict 抖动 | Verifier 独立检查 claim → evidence，给 pass/revise/insufficient |
| 根因正文引用缩写、不可追溯 | Verifier + 确定性校验要求完整 evidence_id |
| 写命令靠提示词约束 | 报告输出前加硬闸门：`solution` 命中写命令直接降级/标红，必要时拒绝交付 |
| 跑满 12 轮仍不收口 | InvestigationState 记录预算与 stop_reason，Verifier 决定 `insufficient` 或 `budget_exhausted` |
| 单 ReAct 容易“哪类工具都点一下” | Planner 限定假设和工具预算，Executor 只按计划取证 |

## 下一步

1. 先补 10～20 条困难/对抗用例，再决定该流程的收益阈值；不要用这 6 条直接宣布它有效。
2. P1 先做三件事：Verifier 独立复核、完整证据编号校验、写命令硬闸门；Planner 可以先接在 Executor 前做单次规划。
3. 基线对比保留同一套 6 条 + 新增困难集，分别报告“简单集”和“困难集”，不能混在一起看平均分。
