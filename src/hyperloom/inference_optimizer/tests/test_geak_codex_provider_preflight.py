# SPDX-FileCopyrightText: 2026 Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Focused preflight tests for GEAK's ChatGPT-authenticated Codex provider."""

from __future__ import annotations

import argparse
import subprocess

import pytest

from hyperloom.inference_optimizer.cli import preflight


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GEAK_AGENT_PROVIDER",
        "GEAK_NODE_BIN",
        "GEAK_CODEX_BIN",
        "GEAK_CODEX_NETWORK_ACCESS",
        "GEAK_CODEX_EXTERNAL_SANDBOX",
        "KERNEL_OPT_BACKEND_ORDER",
        "CODEX_HOME",
        *preflight._GEAK_CODEX_API_AUTH_ENV_NAMES,
    ):
        monkeypatch.delenv(name, raising=False)


def test_claude_default_preserves_legacy_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: pytest.fail("must not probe tools"))

    preflight._validate_geak_agent_provider()

    assert preflight.os.environ["GEAK_AGENT_PROVIDER"] == "claude"


def test_codex_provider_uses_normal_profile_and_rejects_api_auth_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEAK_AGENT_PROVIDER", "CoDeX")
    monkeypatch.setenv("GEAK_NODE_BIN", "node-custom")
    monkeypatch.setenv("GEAK_CODEX_BIN", "codex-custom")
    monkeypatch.setenv("CODEX_HOME", "/home/operator/.codex")
    sanitized_overrides = {
        "OPENAI_API_KEY": "must-not-reach-login-probe",
        "OPENAI_BASE_URL": "https://api.example.invalid/v1",
        "OPENAI_TENANT_API_KEY": "dynamic-pattern-key",
        "AZURE_OPENAI_ENDPOINT": "https://azure.example.invalid",
        "CODEX_MODEL_PROVIDER": "api-provider",
        "CODEX_REFRESH_TOKEN_URL_OVERRIDE": "https://tokens.example.invalid",
        "ANTHROPIC_API_KEY": "anthropic-key",
        "CLAUDE_CODE_OAUTH_TOKEN": "claude-subscription-token",
        "LLM_GATEWAY_KEY": "gateway-key",
    }
    for name, value in sanitized_overrides.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/tools/{name}")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("env")))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout="v20.18.0\n", stderr="")
        if argv[-2:] == ["exec", "--help"]:
            flags = "\n".join(preflight._GEAK_CODEX_REQUIRED_EXEC_FLAGS)
            return subprocess.CompletedProcess(argv, 0, stdout=flags, stderr="")
        assert argv[-2:] == ["login", "status"]
        assert kwargs["env"]["CODEX_HOME"] == "/home/operator/.codex"
        for name in sanitized_overrides:
            assert name not in kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="Logged in using ChatGPT\n", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    preflight._validate_geak_agent_provider(argparse.Namespace(no_kernel=False))

    assert preflight.os.environ["GEAK_AGENT_PROVIDER"] == "codex"
    assert preflight.os.environ["GEAK_NODE_BIN"] == "/tools/node-custom"
    assert preflight.os.environ["GEAK_CODEX_BIN"] == "/tools/codex-custom"
    assert [call[0] for call in calls] == [
        ["/tools/node-custom", "--version"],
        ["/tools/codex-custom", "exec", "--help"],
        ["/tools/codex-custom", "login", "status"],
    ]


@pytest.mark.parametrize(
    "login_status",
    (
        "Logged in using an API key\n",
        "Not Logged in using ChatGPT\n",
        "Logged in using ChatGPT through an API proxy\n",
    ),
)
def test_codex_provider_requires_exact_chatgpt_login_line(
    monkeypatch: pytest.MonkeyPatch, capsys, login_status: str
) -> None:
    monkeypatch.setenv("GEAK_AGENT_PROVIDER", "codex")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/tools/{name}")

    def fake_run(argv, **_kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout="v20.18.0\n", stderr="")
        if argv[-2:] == ["exec", "--help"]:
            flags = "\n".join(preflight._GEAK_CODEX_REQUIRED_EXEC_FLAGS)
            return subprocess.CompletedProcess(argv, 0, stdout=flags, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout=login_status, stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        preflight._validate_geak_agent_provider()

    assert excinfo.value.code == 2
    message = capsys.readouterr().err
    assert "ChatGPT subscription" in message
    assert "OPENAI_API_KEY is not used" in message


def test_invalid_provider_never_silently_falls_back(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("GEAK_AGENT_PROVIDER", "codeex")

    with pytest.raises(SystemExit) as excinfo:
        preflight._validate_geak_agent_provider()

    assert excinfo.value.code == 2
    assert "expected claude|codex" in capsys.readouterr().err


def test_codex_network_access_requires_external_worker_acknowledgement(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("GEAK_AGENT_PROVIDER", "codex")
    monkeypatch.setenv("GEAK_CODEX_NETWORK_ACCESS", "1")
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: pytest.fail("must fail first"))

    with pytest.raises(SystemExit) as excinfo:
        preflight._validate_geak_agent_provider()

    assert excinfo.value.code == 2
    message = capsys.readouterr().err
    assert "GEAK_CODEX_EXTERNAL_SANDBOX=1" in message
    assert "restricted egress" in message


def test_no_kernel_skips_codex_tool_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEAK_AGENT_PROVIDER", "codex")
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: pytest.fail("must not probe tools"))

    preflight._validate_geak_agent_provider(argparse.Namespace(no_kernel=True))
