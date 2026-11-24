# Agent K8s 评测基线

- 生成时间：2026-11-20T02:11:11.554982+00:00
- 模型：deepseek-chat
- 用例数：6，轮次：1

## 指标

| 指标 | 值 |
| --- | --- |
| 根因准确率 | 1.000 |
| Verdict 准确率 | 1.000 |
| 根因证据支撑率 | 1.000 |
| 根因已验证率 | 0.833 |
| 报告完整率 | 0.833 |
| 结论证据覆盖率 | 1.000 |
| 无证据结论率 | 0.000 |
| 危险命令率 | 0.000 |
| 摘要守卫失败率 | 0.000 |
| 缩写证据编号率 | 0.667 |
| 执行失败率 | 0.000 |
| 平均工具调用数 | 7.17 |
| P50 延迟 | 14772 ms |
| P95 延迟 | 18709 ms |

## 逐条结果

| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k8s-configmap-missing | anomaly | 是 | 是 | 是 | 6 | 14979 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 否 | 9 | 15870 ms |  |
| k8s-imagepull-badtag | anomaly | 是 | 是 | 是 | 6 | 14566 ms |  |
| k8s-pvc-storageclass-missing | anomaly | 是 | 是 | 是 | 8 | 14077 ms |  |
| k8s-readiness-probe-port | anomaly | 是 | 是 | 是 | 9 | 18709 ms |  |
| k8s-service-selector-mismatch | anomaly | 是 | 是 | 是 | 5 | 13422 ms |  |

> 根因与证据判定是确定性词项匹配，不调用第二个模型；`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。
