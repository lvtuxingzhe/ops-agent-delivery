# Agent K8s 评测基线

- 生成时间：2026-11-20T01:38:48.931005+00:00
- 模型：deepseek-chat
- 用例数：18，轮次：3

## 指标

| 指标 | 值 |
| --- | --- |
| 根因准确率 | 0.944 |
| Verdict 准确率 | 0.944 |
| 根因证据支撑率 | 0.944 |
| 根因已验证率 | 0.944 |
| 报告完整率 | 0.944 |
| 结论证据覆盖率 | 0.944 |
| 无证据结论率 | 0.000 |
| 危险命令率 | 0.056 |
| 摘要守卫失败率 | 0.389 |
| 缩写证据编号率 | 0.278 |
| 执行失败率 | 0.056 |
| 平均工具调用数 | 6.94 |
| P50 延迟 | 15344 ms |
| P95 延迟 | 18514 ms |

## 逐条结果

| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k8s-configmap-missing | - | 否 | 否 | 否 | 0 | 0 ms | RuntimeError: 模型未产出结构化结果（finished_reason=exceed_max_iters） |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 17712 ms |  |
| k8s-imagepull-badtag | anomaly | 是 | 是 | 是 | 5 | 14160 ms |  |
| k8s-pvc-storageclass-missing | anomaly | 是 | 是 | 是 | 8 | 14685 ms |  |
| k8s-readiness-probe-port | anomaly | 是 | 是 | 是 | 9 | 16843 ms |  |
| k8s-service-selector-mismatch | anomaly | 是 | 是 | 是 | 7 | 17122 ms |  |
| k8s-configmap-missing | anomaly | 是 | 是 | 是 | 6 | 16201 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 18514 ms |  |
| k8s-imagepull-badtag | anomaly | 是 | 是 | 是 | 6 | 14490 ms |  |
| k8s-pvc-storageclass-missing | anomaly | 是 | 是 | 是 | 9 | 14611 ms |  |
| k8s-readiness-probe-port | anomaly | 是 | 是 | 是 | 9 | 16003 ms |  |
| k8s-service-selector-mismatch | anomaly | 是 | 是 | 是 | 5 | 14665 ms |  |
| k8s-configmap-missing | anomaly | 是 | 是 | 是 | 6 | 23112 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 18472 ms |  |
| k8s-imagepull-badtag | anomaly | 是 | 是 | 是 | 5 | 14409 ms |  |
| k8s-pvc-storageclass-missing | anomaly | 是 | 是 | 是 | 8 | 13987 ms |  |
| k8s-readiness-probe-port | anomaly | 是 | 是 | 是 | 10 | 18077 ms |  |
| k8s-service-selector-mismatch | anomaly | 是 | 是 | 是 | 5 | 13381 ms |  |

> 根因与证据判定是确定性词项匹配，不调用第二个模型；`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。
