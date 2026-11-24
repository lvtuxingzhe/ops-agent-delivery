"""只查 SSH 允许的认证方式，不发送任何凭据。"""

from __future__ import annotations

import os
import sys

import paramiko


def main() -> int:
    host = os.environ.get("SSH_HOST", "")
    port = int(os.environ.get("SSH_PORT", "22"))
    user = os.environ.get("SSH_USER", "root")
    transport = paramiko.Transport((host, port))
    transport.start_client(timeout=10)
    try:
        transport.auth_none(user)
    except paramiko.BadAuthenticationType as exc:
        print("METHODS=" + ",".join(exc.allowed_types))
    except Exception as exc:  # noqa: BLE001
        print(f"PROBE_FAILED:{type(exc).__name__}")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
