"""命令展示脱敏测试：凭据只能出现在执行参数里，不能出现在日志文本里。"""

from __future__ import annotations

from ops_guard import (
    command_text_for_log,
    credential_option,
    sanitize_argv_for_log,
    sanitize_command_text,
)


def test_redis_password_option_is_redacted():
    argv = ["redis-cli", "-a", "supersecret", "INFO"]
    assert credential_option(argv) == "-a"
    assert sanitize_argv_for_log(argv) == ["redis-cli", "-a", "<redacted>", "INFO"]
    assert "supersecret" not in command_text_for_log(argv)


def test_kubectl_token_option_is_redacted():
    argv = ["kubectl", "--token=abc123", "get", "pods"]
    assert credential_option(argv) == "--token"
    assert sanitize_argv_for_log(argv) == [
        "kubectl", "--token=<redacted>", "get", "pods",
    ]
    assert "abc123" not in command_text_for_log(argv)


def test_mysqladmin_attached_password_is_redacted():
    argv = ["mysqladmin", "-psecret", "status"]
    assert credential_option(argv) == "-p"
    assert sanitize_argv_for_log(argv) == ["mysqladmin", "-p<redacted>", "status"]
    assert "secret" not in command_text_for_log(argv)


def test_mysqladmin_password_prompt_is_not_secret_value():
    argv = ["mysqladmin", "-p", "status"]
    assert credential_option(argv) == ""
    assert sanitize_argv_for_log(argv) == argv


def test_curl_userinfo_is_redacted_only_when_it_contains_password():
    assert credential_option(["curl", "-u", "user:pass", "http://x"]) == "-u"
    assert sanitize_argv_for_log(["curl", "-u", "user:pass"]) == [
        "curl", "-u", "<redacted>",
    ]
    assert credential_option(["curl", "-u", "user", "http://x"]) == ""


def test_raw_command_string_redaction():
    text = sanitize_command_text("redis-cli -a supersecret INFO")
    assert "supersecret" not in text
    assert text == "redis-cli -a <redacted> INFO"
