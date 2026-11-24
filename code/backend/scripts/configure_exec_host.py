"""
[已废弃] 历史执行主机登记脚本。

当前主机配置存 `v3_configs / namespace=exec_host`，密码走 `password_enc`
（前端表单或导入写入），不再使用 `password_env`；本脚本仅用于排查旧环境。

原用途：登记一台可 SSH 直连的执行主机（只写连接元数据，不写密码）。

用法（在 code 目录）：
    .\\backend\\.venv\\Scripts\\python.exe backend\\scripts\\configure_exec_host.py
    .\\backend\\.venv\\Scripts\\python.exe backend\\scripts\\configure_exec_host.py ^
        --host-id vm-150 --address 10.0.0.150 --user root

默认值就是本次验收的 VMware 主机；密码通过 `password_env` 指向环境变量
`EXEC_SSH_PASSWORD`，脚本本身不读、不回显密码值。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services.exec_hosts import NAMESPACE  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="登记 SSH 执行主机")
    parser.add_argument("--host-id", default="vm-150")
    parser.add_argument("--address", default="10.0.0.150")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="root")
    parser.add_argument("--platform", default="linux")
    parser.add_argument("--os-family", default="",
                        help="debian / rhel / windows；linux 未知时留空并按 unknown 处理")
    parser.add_argument("--distro", default="", help="centos / ubuntu / rocky")
    parser.add_argument("--os-version", default="", help="7.9 / 22.04")
    parser.add_argument("--package-manager", default="",
                        help="apt / dnf / yum / none；留空时按 unknown")
    parser.add_argument("--service-manager", default="", help="systemd / 空")
    parser.add_argument("--password-env", default="EXEC_SSH_PASSWORD")
    parser.add_argument("--key-path", default="var/ssh/ops_agent_rsa")
    parser.add_argument("--known-hosts", default="var/ssh/known_hosts")
    parser.add_argument("--fingerprint", default="",
                        help="固定主机公钥 SHA256，例如 SHA256:...")
    parser.add_argument("--trust-source", default="verified")
    parser.add_argument("--key-only", action="store_true",
                        help="只允许密钥认证，清空 password_env")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    if args.key_only:
        args.password_env = ""
    if not args.fingerprint:
        print("缺少 --fingerprint：先跑 configure_ssh_trust.py 取指纹并核对")
        return 2
    value = {
        "platform": args.platform,
        "os_family": args.os_family or ("windows" if args.platform == "windows" else "unknown"),
        "distro": args.distro,
        "os_version": args.os_version,
        "package_manager": args.package_manager or ("none" if args.platform == "windows" else "unknown"),
        "service_manager": args.service_manager,
        "address": args.address, "port": args.port,
        "user": args.user, "password_env": args.password_env,
        "key_path": args.key_path, "known_hosts": args.known_hosts,
        "host_key_fingerprint": args.fingerprint,
        "trust_source": args.trust_source,
    }
    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO v3_configs (namespace, key, value, enabled, updated_by, updated_at)
                VALUES (%s, %s, %s, true, NULL, now())
                ON CONFLICT (namespace, key) DO UPDATE
                   SET value = EXCLUDED.value, enabled = true, updated_at = now()
                """,
                (NAMESPACE, args.host_id, Jsonb(value)),
            )
    print(f"HOST={args.host_id} ADDRESS={args.address}:{args.port} "
          f"USER={args.user} PLATFORM={args.platform} "
          f"OS={value['os_family']} PM={value['package_manager']}")
    print(f"PASSWORD_ENV={args.password_env}（值不进库，部署时注入环境变量）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
