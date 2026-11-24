"""部署类型与技术域枚举。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

DEPLOY_TYPE_LABELS: dict[str, str] = {
    "middleware": "中间件",
    "database": "数据库",
    "kubernetes": "K8S",
    "docker": "Docker",
    "dependency": "依赖组件",
    "network": "网络",
    "security": "安全",
    "monitor": "监控",
}

# 顺序也决定前端下拉顺序，和用户的固定列表保持一致。
DEPLOY_TYPES: tuple[str, ...] = tuple(DEPLOY_TYPE_LABELS)

TECH_DOMAIN_LABELS: dict[str, str] = {
    "middleware": "中间件",
    "database": "数据库",
    "kubernetes": "K8S",
    "docker": "Docker",
    "dependency": "依赖组件",
    "network": "网络",
    "security": "安全",
    "monitor": "监控",
    "aliyun": "阿里云",
    "tencent": "腾讯云",
    "huawei": "华为云",
    "aws": "AWS",
    "azure": "Azure",
    "private_cloud": "私有云",
    "other": "其他",
}

TECH_DOMAINS: tuple[str, ...] = tuple(TECH_DOMAIN_LABELS)

# 关键词表：列表顺序就是匹配优先级。部署类型比技术域窄，只认 8 类；
# 技术域多了云厂商，云放在业务类型之后，避免“阿里云上的 nginx”被归成“阿里云”。
_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kubernetes", (
        "k8s", "kubernetes", "kubectl", "kubeadm", "helm", "kustomize",
        "ingress", "statefulset", "daemonset", "configmap", "kubelet",
        "pod", "namespace", "集群节点",
    )),
    ("docker", (
        "docker", "docker-compose", "compose", "容器", "container",
        "containerd", "podman", "镜像", "image",
    )),
    ("database", (
        "mysql", "mariadb", "postgres", "postgresql", "pgsql", "oracle",
        "sqlserver", "sql server", "mongodb", "mongo", "redis", "elasticsearch",
        "opensearch", "clickhouse", "tidb", "dameng", "达梦", "人大金仓",
        "数据库", "建库", "建表", "主从", "慢查询",
    )),
    ("middleware", (
        "nginx", "apache", "httpd", "tomcat", "jetty", "weblogic", "jboss",
        "kafka", "rabbitmq", "activemq", "rocketmq", "zookeeper", "nacos",
        "consul", "etcd", "haproxy", "lvs", "keepalived", "keepalive",
        "openresty", "中间件", "负载均衡", "消息队列",
    )),
    ("network", (
        "firewall", "firewalld", "iptables", "nftables", "firewall-cmd",
        "route", "router", "dns", "bind", "ntp", "chrony", "vlan", "bond",
        "network", "网络", "端口", "交换机", "网关", "网卡", "域名解析",
    )),
    ("security", (
        "selinux", "getenforce", "setenforce", "auditd", "fail2ban",
        "security", "安全", "加固", "漏洞", "基线", "等保", "权限收紧",
    )),
    ("monitor", (
        "prometheus", "grafana", "zabbix", "node_exporter", "exporter",
        "alertmanager", "nagios", "监控", "告警", "采集器", "指标",
    )),
    # 依赖组件放最后：`yum install nginx` 同时命中 dependency 和 middleware，
    # 业务语义应该归中间件，只有纯包管理动作才落到依赖组件。
    ("dependency", (
        "yum", "dnf", "apt", "apt-get", "rpm", "dpkg", "pip", "npm",
        "依赖", "组件", "安装包", "仓库源", "离线包", "patch", "补丁",
    )),
)

_CLOUD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("aliyun", ("aliyun", "阿里云", "ecs", "oss", "slb", "rds", "polardb")),
    ("tencent", ("腾讯云", "tencent cloud", "cvm", "cos", "tdsql")),
    ("huawei", ("华为云", "huawei cloud", "obs", "cce", "gaussdb")),
    ("aws", ("aws", "amazon web services", "s3", "ec2", "eks")),
    ("azure", ("azure", "azure cloud", "aks")),
    ("private_cloud", ("私有云", "专有云", "信创云", "openstack")),
)

def _haystack(parts: Iterable[Any]) -> str:
    """把标题/摘要/命令合成为小写文本；只用于分类，不保留原文。"""
    return " ".join(
        str(part or "") for part in parts
    ).lower()


def _matches(keyword: str, text: str) -> bool:
    """英文按词边界匹配，中文按子串匹配。"""
    if not keyword:
        return False
    if re.fullmatch(r"[a-z0-9_.+-]+", keyword):
        # `k8s` 不该命中 `k8sx`；但 `nginx.conf` 应该命中 `nginx`。
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return keyword in text


def normalize_deploy_type(value: object) -> str:
    """把外部传入的类型收敛到固定 8 项；非法值一律为空（未分类）。"""
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in DEPLOY_TYPE_LABELS else ""


def normalize_tech_domain(value: object) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in TECH_DOMAIN_LABELS else "other"


def infer_deploy_type(*parts: Any) -> str:
    """按标题/模板名/命令推断部署类型；8 类之外返回空串。"""
    text = _haystack(parts)
    if not text.strip():
        return ""
    for code, keywords in _TYPE_RULES:
        if any(_matches(keyword, text) for keyword in keywords):
            return code
    return ""


def infer_tech_domain(*parts: Any) -> str:
    """知识库技术域：先业务类型，再云厂商，最后 other。"""
    text = _haystack(parts)
    if not text.strip():
        return "other"
    for code, keywords in _TYPE_RULES:
        if any(_matches(keyword, text) for keyword in keywords):
            return code
    for code, keywords in _CLOUD_RULES:
        if any(_matches(keyword, text) for keyword in keywords):
            return code
    return "other"


def describe_deploy_type(value: object) -> str:
    """给列表回显中文；空值明确叫“未分类”，不伪装成其他类型。"""
    code = str(value or "")
    return DEPLOY_TYPE_LABELS.get(code, "未分类")


__all__ = [
    "DEPLOY_TYPES", "DEPLOY_TYPE_LABELS", "TECH_DOMAINS", "TECH_DOMAIN_LABELS",
    "describe_deploy_type", "infer_deploy_type", "infer_tech_domain",
    "normalize_deploy_type", "normalize_tech_domain",
]
