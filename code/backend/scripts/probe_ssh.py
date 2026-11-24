"""
SSH 连通性探测（诊断用，不落凭据）。

从环境变量读取，命令行不传密码：
    SSH_HOST / SSH_PORT / SSH_USER / SSH_PASSWORD
成功时打印主机名与内核，失败只打印异常类型，绝不回显密码。
"""

from __future__ import annotations

import os
import sys

import paramiko


def main() -> int:
    host = os.environ.get("SSH_HOST", "")
    port = int(os.environ.get("SSH_PORT", "22"))
    user = os.environ.get("SSH_USER", "")
    password = os.environ.get("SSH_PASSWORD", "")
    key_file = os.environ.get("SSH_KEY", "")
    if not host or not user or not (password or key_file):
        print("缺少 SSH_HOST / SSH_USER / (SSH_PASSWORD 或 SSH_KEY)")
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host, port=port, username=user,
            password=password or None, key_filename=key_file or None,
            timeout=10, banner_timeout=10, auth_timeout=10,
            allow_agent=False, look_for_keys=False,
        )
    except paramiko.AuthenticationException as exc:
        methods = getattr(exc, "allowed_methods", None)
        # PAM 常把密码认证走成 keyboard-interactive：password 方式失败后，
        # 再按服务器提示回填同一个密码，不额外猜密码。
        if not _try_keyboard_interactive(client, user, password):
            print(f"AUTH_FAILED methods={methods}")
            return 1
    except Exception as exc:  # noqa: BLE001 - 诊断只报类型
        print(f"CONNECT_FAILED:{type(exc).__name__}")
        return 3

    try:
        _stdin, stdout, stderr = client.exec_command("hostname; uname -a", timeout=10)
        print("OK")
        print(stdout.read().decode("utf-8", errors="replace").strip())
        err = stderr.read().decode("utf-8", errors="replace").strip()
        if err:
            print(f"STDERR:{err[:200]}")
    finally:
        client.close()
    return 0


def _try_keyboard_interactive(client: paramiko.SSHClient,
                              user: str, password: str) -> bool:
    """PAM 把密码认证走成 keyboard-interactive 时，回填同一个密码。"""
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        return False

    def handler(_title: str, _instructions: str,
                prompts: list[tuple[str, bool]],
                _echo: list[bool]) -> list[str]:
        return [password for _prompt, _flag in prompts]

    try:
        transport.auth_interactive(user, handler)
    except Exception:  # noqa: BLE001 - 失败就按未通过处理
        return False
    return transport.is_authenticated()


if __name__ == "__main__":
    sys.exit(main())
