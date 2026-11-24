"""失败原因分类与可读文案。

执行器返回的 reason 可能是 `ssh_connect_failed:AuthenticationException`
这类带异常类型的原始串；页面只应看到稳定的失败分类。所有写入
`v3_tasks.reason_code` 和部署详情读取都走这里，避免各服务各写一套映射。
"""

from __future__ import annotations

from typing import Any

REASON_TEXT = {
    "not_allowlisted": "命令不在白名单内，执行器拒绝执行",
    "not_allowedlist": "命令不在白名单内，执行器拒绝执行",
    "dangerous_requires_confirmation": "危险命令缺少人工确认，执行器拒绝执行",
    "permit_scope_too_low": "执行许可权限不足，执行器拒绝执行",
    "permit_not_issued": "没有拿到这条命令的一次性执行许可",
    "permit_replayed": "执行许可已被使用，不能重复执行",
    "permit_nonce_store_required": "生产环境未配置 Redis，执行许可无法做跨 worker 去重",
    "permit_nonce_store_unavailable": "执行许可去重存储不可用，已拒绝执行",
    "timeout": "命令执行超时",
    "exec_timeout": "整批命令执行超时",
    "ssh_target_missing": "目标主机连接信息不完整",
    "ssh_target_invalid": "目标主机地址或端口不合法",
    "ssh_connection_failed": "SSH 连接失败，目标主机不可达",
    "ssh_auth_failed": "SSH 认证失败，请核对用户名和密码",
    "ssh_credentials_missing": "目标主机缺少登录凭据",
    # 主机公钥校验：按集中 known_hosts 文件执行，不再使用 AutoAddPolicy。
    "ssh_known_hosts_missing": "主机公钥登记文件缺失，连接被拒绝",
    "ssh_known_hosts_invalid": "主机公钥登记文件格式错误，连接被拒绝",
    "ssh_host_key_untrusted": "目标主机未登记，连接被拒绝",
    "ssh_host_key_mismatch": "目标主机公钥与登记不一致，连接被拒绝",
    "ssh_host_key_probe_failed": "首次探测目标主机公钥失败，请检查网络后重试",
    "host_key_confirmation_required": "目标主机首次接入，等待管理员确认公钥",
    "ssh_host_key_policy_invalid": "主机公钥策略配置非法，连接被拒绝",
    "ssh_host_key_policy_insecure_unack": "关闭主机公钥校验需要额外确认，连接被拒绝",
    "permission_denied": "目标机权限不足，命令被系统拒绝",
    "ssh_exec_failed": "SSH 命令执行失败",
    "exit_code_nonzero": "命令退出码非 0",
    "verify_expect_mismatch": "业务验证结果与模板预期不一致",
    "precheck_expect_mismatch": "变更前检查结果与方案预期不一致",
    "apply_expect_mismatch": "变更结果与方案预期不一致",
    "rollback_expect_mismatch": "回滚结果与方案预期不一致",
    "post_rollback_expect_mismatch": "回滚后验证结果与方案预期不一致",
    "precheck_failed": "变更前检查失败，未执行任何变更",
    "post_rollback_failed": "回滚命令执行后验证未通过",
    "rollback_failed": "回滚命令执行失败",
}


def normalize_reason(code: Any) -> str:
    """把执行器原始失败码收敛成稳定的分类码。"""
    value = str(code or "").strip()
    if value == "not_allowedlist":
        return "not_allowlisted"
    if value.startswith("ssh_connect_failed:"):
        detail = value.split(":", 1)[1].strip().lower()
        if "authentication" in detail or "password" in detail or "auth" in detail:
            return "ssh_auth_failed"
        return "ssh_connection_failed"
    if value.startswith("ssh_exec_failed:"):
        detail = value.split(":", 1)[1].strip().lower()
        if "permissiondenied" in detail or "permission" in detail:
            return "permission_denied"
        return "ssh_exec_failed"
    if value.startswith("ssh_host_key_probe_failed:"):
        return "ssh_host_key_probe_failed"
    return value
