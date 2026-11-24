# Agent K8s PEV 评测

- 生成时间：2026-11-20T02:23:30.457689+00:00
- 模型：deepseek-chat
- 用例数：6，轮次：1

## 指标

| 指标 | 值 |
| --- | --- |
| 根因准确率 | 0.833 |
| Verdict 准确率 | 0.833 |
| 根因证据支撑率 | 0.833 |
| 根因已验证率 | 0.833 |
| 报告完整率 | 0.500 |
| 结论证据覆盖率 | 0.833 |
| 无证据结论率 | 0.000 |
| 危险命令率 | 0.000 |
| 摘要守卫失败率 | 0.167 |
| 缩写证据编号率 | 0.333 |
| 执行失败率 | 0.167 |
| 平均工具调用数 | 13.33 |
| P50 延迟 | 41211 ms |
| P95 延迟 | 76214 ms |

## 逐条结果

| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k8s-configmap-missing | - | 否 | 否 | 否 | 0 | 0 ms | RuntimeError: Executor 未产出结构化报告（attempt=1，finished_reason=exceed_max_iters） |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 否 | 19 | 71263 ms |  |
| k8s-imagepull-badtag | anomaly | 是 | 是 | 否 | 22 | 76214 ms |  |
| k8s-pvc-storageclass-missing | anomaly | 是 | 是 | 是 | 10 | 21455 ms |  |
| k8s-readiness-probe-port | anomaly | 是 | 是 | 是 | 21 | 59664 ms |  |
| k8s-service-selector-mismatch | anomaly | 是 | 是 | 是 | 8 | 22758 ms |  |

> 根因与证据判定是确定性词项匹配，不调用第二个模型；`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。
