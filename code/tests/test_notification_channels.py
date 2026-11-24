"""飞书/邮件渠道的纯单元测试：加密写入数据库、脱敏响应、发送与停用。"""

from __future__ import annotations

import json
import smtplib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from app.core.security import SecretBox
from app.services import notification_channels as channels
from cryptography.fernet import Fernet


class _FakeConn:
    """最小假连接：读 v3_configs，保存时更新内存行。"""

    def __init__(self):
        self.rows: dict[str, tuple[dict, bool]] = {}
        self.last_sql = ""
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def cursor(self):
        """审计层用 conn.cursor()；这里把游标能力委托回自身。"""
        return self

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        if "INSERT INTO v3_configs" in sql:
            name, key, value, enabled, _actor = params
            self.rows[key] = (dict(getattr(value, "obj", value)), bool(enabled))
        return self

    def fetchone(self):
        if "FROM v3_configs" in self.last_sql:
            key = self.last_params[1]
            return self.rows.get(key)
        if "currval" in self.last_sql:
            return (1,)
        return None


def _settings():
    """只带 SecretBox 需要的密钥，不连真实环境。"""
    return SimpleNamespace(totp_encryption_key=Fernet.generate_key().decode("ascii"))


def _with_fake(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(channels, "connect_app", lambda _settings: conn)
    return conn


def test_feishu_update_encrypts_and_never_returns_url(monkeypatch):
    """webhook 加密写入数据库；对外只返回 configured。"""
    settings = _settings()
    conn = _with_fake(monkeypatch)
    url = "http://127.0.0.1:19001/hook"
    item = channels.update_feishu(
        settings, actor_id=1, enabled=True, webhook_url=url,
    )
    assert item["configured"] is True
    assert "webhook_url" not in item
    raw = conn.rows["feishu"][0]["webhook_url_enc"]
    assert raw != url
    assert SecretBox(settings.totp_encryption_key).decrypt(raw) == url


def test_email_update_keeps_password_when_blank(monkeypatch):
    """密码留空 = 保留原值；收件人拆成列表，响应不回显密码。"""
    settings = _settings()
    conn = _with_fake(monkeypatch)
    first = channels.update_email(
        settings, actor_id=1, enabled=True,
        smtp_host="smtp.example.com", smtp_port=587, smtp_user="ops",
        password="first-pass", from_addr="ops@example.com",
        to_addrs="a@example.com, b@example.com", use_tls=True,
    )
    assert first["configured"] is True
    assert first["to_addrs"] == ["a@example.com", "b@example.com"]
    assert "password" not in first
    encrypted = conn.rows["email"][0]["smtp_password_enc"]

    channels.update_email(
        settings, actor_id=1, enabled=True,
        smtp_host="smtp.example.com", smtp_port=587, smtp_user="ops",
        password="", from_addr="ops@example.com",
        to_addrs="a@example.com, b@example.com", use_tls=True,
    )
    assert conn.rows["email"][0]["smtp_password_enc"] == encrypted


class _HookHandler(BaseHTTPRequestHandler):
    """本地假飞书 webhook：把请求体存到类变量。"""

    received: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"code":0}')

    def log_message(self, *_args):
        """测试不打印访问日志。"""


def test_feishu_send_posts_text_payload(monkeypatch):
    """启用后真的 POST 到 webhook，发的是飞书文本消息体。"""
    settings = _settings()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = _with_fake(monkeypatch)
        channels.update_feishu(
            settings, actor_id=1, enabled=True,
            webhook_url=f"http://127.0.0.1:{server.server_port}/hook",
        )
        ok, detail = channels.send_feishu(settings, "任务完成")
        assert (ok, detail) == (True, "ok")
        assert _HookHandler.received[0]["msg_type"] == "text"
        assert _HookHandler.received[0]["content"]["text"] == "任务完成"
        assert conn.rows["feishu"][1] is True
    finally:
        server.shutdown()
        server.server_close()


class _FakeSMTP:
    """记录发送参数，不连真实 SMTP。"""

    instances: list[_FakeSMTP] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.messages = []
        self.logged_in = None
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return None

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.messages.append(message)


def test_email_send_uses_message_and_login(monkeypatch):
    """启用后调用 SMTP，登录密码来自解密配置。"""
    settings = _settings()
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    _with_fake(monkeypatch)
    channels.update_email(
        settings, actor_id=1, enabled=True,
        smtp_host="127.0.0.1", smtp_port=2525, smtp_user="ops",
        password="smtp-pass", from_addr="ops@example.com",
        to_addrs="a@example.com", use_tls=False,
    )
    ok, detail = channels.send_email(settings, "报告完成")
    assert (ok, detail) == (True, "ok")
    instance = _FakeSMTP.instances[0]
    assert instance.host == "127.0.0.1"
    assert instance.logged_in == ("ops", "smtp-pass")
    assert instance.messages[0]["To"] == "a@example.com"
    assert "报告完成" in instance.messages[0].get_content()


def test_disabled_channel_is_skipped(monkeypatch):
    """停用的渠道直接返回 disabled，不发网络请求。"""
    settings = _settings()
    _with_fake(monkeypatch)
    channels.update_feishu(
        settings, actor_id=1, enabled=False,
        webhook_url="http://127.0.0.1:9/hook",
    )
    assert channels.send_feishu(settings, "不发送") == (False, "disabled")
