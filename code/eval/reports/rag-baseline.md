# RAG 检索基线

- 生成时间：2026-11-20T00:09:06.771303+00:00
- 用例数：30，Top-K：5

## 指标

| 指标 | 值 |
| --- | --- |
| Recall@5 | 1.000 |
| MRR | 0.983 |
| 空召回率 | 0.000 |
| 危险误召回率 | 0.000 |
| 有危险标签的用例 | 30 |

## 逐条结果

| 用例 | 命中 | 排名 | 模式 | Top-1 |
| --- | --- | --- | --- | --- |
| nginx-port-1 | 是 | 1 | hybrid | 【RAG验收】Nginx 启动失败：监听端口被占用 |
| nginx-port-2 | 是 | 1 | hybrid | 【RAG验收】Nginx 启动失败：监听端口被占用 |
| nginx-port-3 | 是 | 1 | hybrid | 【RAG验收】Nginx 启动失败：监听端口被占用 |
| systemd-exit-1 | 是 | 1 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 |
| systemd-exit-2 | 是 | 1 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 |
| systemd-exit-3 | 是 | 1 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 |
| mysql-replica-1 | 是 | 1 | hybrid | 【RAG验收】MySQL 从库复制中断处理 |
| mysql-replica-2 | 是 | 1 | hybrid | 【RAG验收】MySQL 从库复制中断处理 |
| mysql-replica-3 | 是 | 1 | hybrid | 【RAG验收】MySQL 从库复制中断处理 |
| redis-timeout-1 | 是 | 1 | hybrid | 【RAG验收】Redis 连接超时与连接数打满 |
| redis-timeout-2 | 是 | 1 | hybrid | 【RAG验收】Redis 连接超时与连接数打满 |
| redis-timeout-3 | 是 | 1 | hybrid | 【RAG验收】Redis 连接超时与连接数打满 |
| k8s-crashloop-1 | 是 | 1 | hybrid | 【RAG验收】Kubernetes Pod CrashLoopBackOff |
| k8s-crashloop-2 | 是 | 1 | hybrid | 【RAG验收】Kubernetes Pod CrashLoopBackOff |
| k8s-crashloop-3 | 是 | 2 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 |
| disk-inode-1 | 是 | 1 | hybrid | 【RAG验收】磁盘 inode 耗尽 |
| disk-inode-2 | 是 | 1 | hybrid | 【RAG验收】磁盘 inode 耗尽 |
| disk-inode-3 | 是 | 1 | hybrid | 【RAG验收】磁盘 inode 耗尽 |
| dns-fail-1 | 是 | 1 | hybrid | 【RAG验收】DNS 解析失败 |
| dns-fail-2 | 是 | 1 | hybrid | 【RAG验收】DNS 解析失败 |
| dns-fail-3 | 是 | 1 | hybrid | 【RAG验收】DNS 解析失败 |
| firewall-1 | 是 | 1 | hybrid | 【RAG验收】防火墙未放行端口 |
| firewall-2 | 是 | 1 | hybrid | 【RAG验收】防火墙未放行端口 |
| firewall-3 | 是 | 1 | hybrid | 【RAG验收】防火墙未放行端口 |
| tls-expired-1 | 是 | 1 | hybrid | 【RAG验收】TLS 证书过期 |
| tls-expired-2 | 是 | 1 | hybrid | 【RAG验收】TLS 证书过期 |
| tls-expired-3 | 是 | 1 | hybrid | 【RAG验收】TLS 证书过期 |
| nginx-502-1 | 是 | 1 | hybrid | 【RAG验收】Nginx 502 Bad Gateway |
| nginx-502-2 | 是 | 1 | hybrid | 【RAG验收】Nginx 502 Bad Gateway |
| nginx-502-3 | 是 | 1 | hybrid | 【RAG验收】Nginx 502 Bad Gateway |

> `dangerous_false_recall_rate` 只在有 `dangerous_terms` 标签的用例上统计；无标签时显示 N/A，不能把 N/A 当作 0。
