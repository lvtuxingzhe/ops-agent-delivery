"""
[已废弃] 历史 SSH 指纹/密钥安装脚本。

现行流程改用 `backend/scripts/pin_ssh_host_key.py`：先在目标机控制台核对
`ssh-keygen -lf` 指纹，再用 `--expected-fingerprint` 写入主机配置；worker
连接时会强制比对，未固定或不一致直接拒绝。本脚本仅用于排查旧环境，不再
作为现行接入流程。

原用途：把目标机的 SSH 主机公钥固定到 known_hosts，并把本机公钥装进目标机。

用法（在 code 目录，密码只从 .env 的 EXEC_SSH_PASSWORD 读）：
    python backend/scripts/configure_ssh_trust.py --host-id host-150 --install-key

做的事：
  1. 用现有凭据连一次目标机，取出它出示的主机公钥；
  2. 以 `[address]:port keytype base64` 写进 `var/ssh/known_hosts`（幂等）；
  3. `--install-key` 时把 `var/ssh/ops_agent_rsa.pub` 追加到目标机
     `~/.ssh/authorized_keys`（已存在同注释键则跳过）。

注意：第一次取指纹仍是「首次信任」（TOFU）。拿到指纹后应和 VM 控制台
`ssh-keygen -lf /etc/ssh/ssh_host_rsa_key.pub` 的结果人工核对一次。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

import paramiko

CODE = Path(__file__).resolve().parents[2]
BACKEND = CODE / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services import exec_hosts  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402


def _fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _connect(host: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": host["address"], "port": host["port"],
        "username": host["user"], "timeout": 15, "banner_timeout": 15,
        "auth_timeout": 15, "allow_agent": False, "look_for_keys": False,
    }
    password = os.environ.get(host["password_env"] or "", "")
    if password:
        kwargs["password"] = password
    elif host["key_path"]:
        kwargs["key_filename"] = str((CODE / host["key_path"]).resolve())
    else:
        raise RuntimeError("no_credentials")
    client.connect(**kwargs)
    return client


def _write_known_hosts(path: Path, lines: list[str]) -> int:
    """幂等追加；返回新增条数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    added = 0
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            if line not in existing:
                handle.write(line + "\n")
                added += 1
    return added


def _install_key(client: paramiko.SSHClient, pub_path: Path) -> str:
    pub = pub_path.read_text(encoding="utf-8").strip()
    if not pub:
        raise RuntimeError("public_key_empty")
    # 公钥是本地受信文件；内容只有 base64 与注释，不含 shell 元字符
    command = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys && "
        f"grep -q 'ops-agent@local' ~/.ssh/authorized_keys || "
        f"echo '{pub}' >> ~/.ssh/authorized_keys"
    )
    _stdin, stdout, stderr = client.exec_command(command, timeout=20)
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"install_key_failed:{stderr.read().decode()[:200]}")
    return "installed_or_present"


def _save_fingerprint(settings, host_id: str, fingerprint: str,
                      trust_source: str) -> None:
    """把指纹写回主机清单，作为运行期的第二道校验。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
            (exec_hosts.NAMESPACE, host_id),
        ).fetchone()
        value = dict(row[0] or {}) if row else {}
        value["host_key_fingerprint"] = fingerprint
        value["trust_source"] = trust_source
        conn.execute(
            "UPDATE v3_configs SET value=%s, updated_at=now() "
            " WHERE namespace=%s AND key=%s",
            (Jsonb(value), exec_hosts.NAMESPACE, host_id),
        )


def main() -> int:
    load_dotenv()
    settings = load_settings()
    parser = argparse.ArgumentParser(description="固定 SSH 主机指纹 / 安装公钥")
    parser.add_argument("--host-id", default="host-150")
    parser.add_argument("--known-hosts", default="var/ssh/known_hosts")
    parser.add_argument("--public-key", default="var/ssh/ops_agent_rsa.pub")
    parser.add_argument("--install-key", action="store_true")
    parser.add_argument("--expected-fingerprint", default="")
    parser.add_argument("--accept-new", action="store_true",
                        help="首次引导时接受当前指纹（TOFU），会记为 trust_source=tofu")
    args = parser.parse_args()

    host = exec_hosts.get_host(settings, args.host_id)
    if not host or host.get("local"):
        print(f"HOST_NOT_FOUND={args.host_id}")
        return 2
    host = {**host, "platform": host.get("platform") or "linux"}
    if not host["address"] or not host["user"]:
        print("HOST_INCOMPLETE")
        return 2
    client = _connect(host)
    try:
        key = client.get_transport().get_remote_server_key()
        fingerprint = _fingerprint(key)
        expected = args.expected_fingerprint.strip()
        if expected and expected != fingerprint:
            print(f"FINGERPRINT_MISMATCH expected={expected} actual={fingerprint}")
            return 2
        if not expected and not args.accept_new:
            print(f"CANDIDATE={fingerprint}（核对后带 --expected-fingerprint 重跑，"
                  f"或明确 --accept-new）")
            return 2
        body = f"{key.get_name()} {key.get_base64()}"
        # OpenSSH 用 [host]:port；paramiko 在 22 端口按裸 host 查，两种都写
        lines = [f"[{host['address']}]:{host['port']} {body}"]
        if host["port"] == 22:
            lines.append(f"{host['address']} {body}")
        target = (CODE / args.known_hosts).resolve()
        added = _write_known_hosts(target, lines)
        trust_source = "verified" if expected else "tofu"
        _save_fingerprint(settings, args.host_id, fingerprint, trust_source)
        print(f"FINGERPRINT={fingerprint}")
        print(f"KNOWN_HOSTS=+{added} PATH={target} TRUST={trust_source}")
        if args.install_key:
            print("PUBLIC_KEY=" + _install_key(client, (CODE / args.public_key).resolve()))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
