# RAG 检索评测报告

- 生成时间：2026-11-21T11:46:47.158292+00:00
- 用例数：30，Top-K：5

## 指标

| 指标 | 值 |
| --- | --- |
| Recall@5 | 1.000 |
| Top-1 准确率 | 0.933 |
| MRR | 0.967 |
| 空召回率 | 0.000 |
| 危险误召回率 | 0.000 |
| 有危险标签的用例 | 30 |

## 分组指标

| 分组 | 用例数 | Recall@5 | Top-1 | 空召回率 |
| --- | --- | --- | --- | --- |
| unknown | 30 | 1.000 | 0.933 | 0.000 |

## 逐条结果

| 用例 | 命中 | 排名 | 模式 | 命中条目 | Top-1 |
| --- | --- | --- | --- | --- | --- |
| nginx-port-1 | 是 | 1 | hybrid | 【RAG验收】Nginx 启动失败：监听端口被占用 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| nginx-port-2 | 是 | 1 | hybrid | 【RAG验收】Nginx 启动失败：监听端口被占用 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| nginx-port-3 | 是 | 1 | hybrid | 【RAG验收】Nginx 启动失败：监听端口被占用 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| systemd-exit-1 | 是 | 1 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| systemd-exit-2 | 是 | 1 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| systemd-exit-3 | 是 | 1 | hybrid | 【RAG验收】systemd 服务启动失败：ExecStart 非零退出 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| mysql-replica-1 | 是 | 1 | hybrid | 【RAG验收】MySQL 从库复制中断处理 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| mysql-replica-2 | 是 | 1 | hybrid | 【RAG验收】MySQL 从库复制中断处理 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| mysql-replica-3 | 是 | 1 | hybrid | 【RAG验收】MySQL 从库复制中断处理 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| redis-timeout-1 | 是 | 1 | hybrid | 【RAG验收】Redis 连接超时与连接数打满 | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| redis-timeout-2 | 是 | 1 | hybrid | 【RAG验收】Redis 连接超时与连接数打满 | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| redis-timeout-3 | 是 | 1 | hybrid | 【RAG验收】Redis 连接超时与连接数打满 | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| k8s-crashloop-1 | 是 | 1 | hybrid | 【RAG验收】Kubernetes Pod CrashLoopBackOff | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a |
| k8s-crashloop-2 | 是 | 1 | hybrid | 【RAG验收】Kubernetes Pod CrashLoopBackOff | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a |
| k8s-crashloop-3 | 是 | 2 | hybrid | 【RAG验收】Kubernetes Pod CrashLoopBackOff | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| disk-inode-1 | 是 | 1 | hybrid | 【RAG验收】磁盘 inode 耗尽 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| disk-inode-2 | 是 | 1 | hybrid | 【RAG验收】磁盘 inode 耗尽 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| disk-inode-3 | 是 | 1 | hybrid | 【RAG验收】磁盘 inode 耗尽 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| dns-fail-1 | 是 | 1 | hybrid | 【RAG验收】DNS 解析失败 | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| dns-fail-2 | 是 | 1 | hybrid | 【RAG验收】DNS 解析失败 | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| dns-fail-3 | 是 | 1 | hybrid | 【RAG验收】DNS 解析失败 | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| firewall-1 | 是 | 2 | hybrid | 【RAG验收】防火墙未放行端口 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| firewall-2 | 是 | 1 | hybrid | 【RAG验收】防火墙未放行端口 | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 |
| firewall-3 | 是 | 1 | hybrid | 【RAG验收】防火墙未放行端口 | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 |
| tls-expired-1 | 是 | 1 | hybrid | 【RAG验收】TLS 证书过期 | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| tls-expired-2 | 是 | 1 | hybrid | 【RAG验收】TLS 证书过期 | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| tls-expired-3 | 是 | 1 | hybrid | 【RAG验收】TLS 证书过期 | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| nginx-502-1 | 是 | 1 | hybrid | 【RAG验收】Nginx 502 Bad Gateway | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |
| nginx-502-2 | 是 | 1 | hybrid | 【RAG验收】Nginx 502 Bad Gateway | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |
| nginx-502-3 | 是 | 1 | hybrid | 【RAG验收】Nginx 502 Bad Gateway | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |

> `dangerous_false_recall_rate` 只在有 `dangerous_terms` 标签的用例上统计；无标签时显示 N/A，不能把 N/A 当作 0。
