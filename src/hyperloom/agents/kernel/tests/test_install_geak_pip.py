# SPDX-FileCopyrightText: 2026 Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Behavioural + static guards for ensure_geak()'s pip-install path.

GEAK dropped its setup.sh and now ships as a pip package. ensure_geak() must:

* install the GEAK package from the local ${GEAK_ROOT} checkout (NOT a
  git+<remote>@<ref> URL), so the installed package matches the
  interface/run_e2e.py we run and honours GEAK_REPO/GEAK_REF overrides
  (local mirror / fork / SSH URL) that are not valid pip URLs;
* pass GEAK_HOME=${GEAK_ROOT} and GEAK_AGENT_PROVIDER so GEAK's bootstrap
  reuses our checkout and prepares the selected runtime;
* install claude-agent-sdk only for the default Claude provider;
* skip the package install with a clear warning when the checkout carries no
  pyproject.toml/setup.py, instead of failing obscurely;
* never mention setup.sh again.

The behavioural test extracts the real ensure_geak body from install.sh and
runs it with stubbed log/warn/run (run only echoes, so nothing hits the
network or pip).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
INSTALL_SH = REPO_ROOT / "src" / "hyperloom" / "agents" / "kernel" / "scripts" / "install.sh"
DOCKER_DEMO_SKILLS = (
    REPO_ROOT / "examples" / "hyperloom-qwen3-8b-3h" / "SKILL.md",
    REPO_ROOT / "examples" / "hyperloom-qwen3-14b-fp8-12h" / "SKILL.md",
    REPO_ROOT / "examples" / "hyperloom-custom-advanced" / "SKILL.md",
)


def _extract_ensure_geak() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"^ensure_geak\(\) \{.*?^\}", text, re.S | re.M)
    assert m, "could not locate ensure_geak() in install.sh"
    return m.group(0)


def _extract_codex_preflight() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"^preflight_geak_codex_runtime\(\) \{.*?^\}", text, re.S | re.M)
    assert m, "could not locate preflight_geak_codex_runtime() in install.sh"
    return m.group(0)


def _run_ensure_geak(tmp_path: Path, *, package_metadata: bool, provider: str = "claude") -> str:
    """Run the extracted ensure_geak body with stubs; return combined output."""
    geak_root = tmp_path / "os" / "GEAK"
    (geak_root / ".git").mkdir(parents=True)  # take the "already present" path
    (geak_root / "interface").mkdir(parents=True)
    (geak_root / "interface" / "run_e2e.py").write_text("# runner\n", encoding="utf-8")
    if package_metadata:
        (geak_root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")

    harness = f"""#!/usr/bin/env bash
set -euo pipefail
log()  {{ echo "[log] $*"; }}
warn() {{ echo "[warn] $*"; }}
run()  {{ echo "RUN: $*"; }}
CHECK_ONLY=0
DRY_RUN=0
GEAK_ROOT="{geak_root}"
# A non-HTTPS override that is a valid `git clone` target but NOT a valid
# `git+...` pip URL — proves we never build such a URL.
GEAK_REPO="git@github.com:acme/GEAK.git"
GEAK_REF="main"
GEAK_E2E_RUNNER="${{GEAK_ROOT}}/interface/run_e2e.py"
GEAK_AGENT_PROVIDER_VAL="{provider}"
GEAK_NODE_BIN="/tools/node"
GEAK_CODEX_BIN="/tools/codex"

{_extract_ensure_geak()}

ensure_geak
"""
    script = tmp_path / "harness.sh"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0, f"ensure_geak harness failed:\n{proc.stdout}"
    return proc.stdout


def test_installs_local_checkout_not_git_url(tmp_path: Path) -> None:
    out = _run_ensure_geak(tmp_path, package_metadata=True)
    geak_root = tmp_path / "os" / "GEAK"
    # Installs the local checkout, with GEAK_HOME pointing at it.
    assert f"GEAK_HOME={geak_root} GEAK_AGENT_PROVIDER=claude" in out, out
    assert f"pip install -q --no-cache-dir --break-system-packages {geak_root}" in out, out
    # Never refetches from the remote via a git+ pip URL.
    assert "git+" not in out, out
    # setup.sh is gone.
    assert "setup.sh" not in out, out
    # SDK still installed; runner present so no "missing" warnings.
    assert "pip install -q --no-cache-dir --break-system-packages claude-agent-sdk anyio" in out, out
    assert "package metadata missing" not in out, out
    assert "e2e runner not found" not in out, out


def test_codex_bootstrap_receives_provider_and_skips_claude_sdk(tmp_path: Path) -> None:
    out = _run_ensure_geak(tmp_path, package_metadata=True, provider="codex")

    assert "GEAK_AGENT_PROVIDER=codex" in out
    assert "GEAK_NODE_BIN=/tools/node" in out
    assert "GEAK_CODEX_BIN=/tools/codex" in out
    assert "claude-agent-sdk" not in out


def test_skips_pip_with_warning_when_no_package_metadata(tmp_path: Path) -> None:
    out = _run_ensure_geak(tmp_path, package_metadata=False)
    geak_root = tmp_path / "os" / "GEAK"
    assert "package metadata missing" in out, out
    # The package install must be skipped (no pip install of the checkout dir)...
    assert f"pip install -q --no-cache-dir --break-system-packages {geak_root}" not in out, out
    # ...but the SDK install still runs.
    assert "claude-agent-sdk anyio" in out, out


def test_static_guards_pip_from_checkout() -> None:
    body = _extract_ensure_geak()
    assert 'python3 -m pip install ${_PIP_FLAGS} "${GEAK_ROOT}"' in body, (
        "ensure_geak must pip-install the local ${GEAK_ROOT} checkout"
    )
    assert 'GEAK_HOME="${GEAK_ROOT}"' in body, "must pass GEAK_HOME to reuse the checkout"
    assert 'GEAK_AGENT_PROVIDER="${GEAK_AGENT_PROVIDER_VAL}"' in body
    assert "git+" not in body, "must not build a git+<remote> pip URL"
    assert "setup.sh" not in body, "setup.sh path must be fully removed"


def test_codex_preflight_uses_chatgpt_profile_not_api_key(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    node = bin_dir / "node"
    node.write_text("#!/bin/sh\nprintf 'v20.18.0\\n'\n", encoding="utf-8")
    node.chmod(0o755)
    probe_log = tmp_path / "codex-probe.log"
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1 $2\" = 'exec --help' ]; then\n"
        "  printf '%s\\n' --config --ephemeral --ignore-user-config --ignore-rules --output-schema --output-last-message --skip-git-repo-check\n"
        "  exit 0\n"
        "fi\n"
        f"printf 'api=%s base=%s provider=%s refresh=%s anthropic=%s oauth=%s gateway=%s codex_home=%s\\n' "
        '"${OPENAI_API_KEY-unset}" "${OPENAI_BASE_URL-unset}" '
        '"${CODEX_MODEL_PROVIDER-unset}" "${CODEX_REFRESH_TOKEN_URL_OVERRIDE-unset}" '
        '"${ANTHROPIC_API_KEY-unset}" "${CLAUDE_CODE_OAUTH_TOKEN-unset}" '
        '"${LLM_GATEWAY_KEY-unset}" '
        f"\"${{CODEX_HOME-unset}}\" > '{probe_log}'\n"
        "printf 'Logged in using ChatGPT\\n'\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    harness = tmp_path / "preflight.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'log() { echo "[log] $*"; }\n'
        'warn() { echo "[warn] $*"; }\n'
        'die() { echo "[error] $*" >&2; exit 1; }\n'
        "CHECK_ONLY=0\n"
        "DRY_RUN=0\n"
        "GEAK_AGENT_PROVIDER_VAL=codex\n"
        f"PATH='{bin_dir}:/usr/bin:/bin'\n"
        "GEAK_NODE_BIN=node\n"
        "GEAK_CODEX_BIN=codex\n"
        "OPENAI_API_KEY=api-key-must-be-stripped\n"
        "OPENAI_BASE_URL=https://api.example.invalid/v1\n"
        "CODEX_MODEL_PROVIDER=api-provider\n"
        "CODEX_REFRESH_TOKEN_URL_OVERRIDE=https://tokens.example.invalid\n"
        "ANTHROPIC_API_KEY=anthropic-key\n"
        "CLAUDE_CODE_OAUTH_TOKEN=claude-subscription-token\n"
        "LLM_GATEWAY_KEY=gateway-key\n"
        "CODEX_HOME=/home/operator/.codex\n"
        "export PATH GEAK_NODE_BIN GEAK_CODEX_BIN OPENAI_API_KEY OPENAI_BASE_URL "
        "CODEX_MODEL_PROVIDER CODEX_REFRESH_TOKEN_URL_OVERRIDE ANTHROPIC_API_KEY "
        "CLAUDE_CODE_OAUTH_TOKEN LLM_GATEWAY_KEY CODEX_HOME\n\n"
        f"{_extract_codex_preflight()}\n\n"
        "preflight_geak_codex_runtime\n"
        'printf \'node=%s codex=%s\\n\' "$GEAK_NODE_BIN" "$GEAK_CODEX_BIN"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(harness)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout
    assert "auth=ChatGPT" in proc.stdout
    assert probe_log.read_text(encoding="utf-8") == (
        "api=unset base=unset provider=unset refresh=unset anthropic=unset oauth=unset "
        "gateway=unset codex_home=/home/operator/.codex\n"
    )


def test_installer_persists_codex_runtime_settings_without_private_home() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    write_start = text.index("write_env_file() {")
    write_end = text.index("\nensure_geak() {", write_start)
    write_body = text[write_start:write_end]

    assert "export GEAK_AGENT_PROVIDER=" in write_body
    assert "_GEAK_CODEX_RUNTIME_ENV_VARS" in write_body
    assert "upsert_dotenv_var GEAK_AGENT_PROVIDER" in write_body
    assert "upsert_dotenv_var CODEX_HOME" in write_body
    assert "mkdir" not in "\n".join(line for line in write_body.splitlines() if "CODEX_HOME" in line), (
        "installer must not create a private Codex profile"
    )


def test_installer_codex_login_probe_is_exact_and_sanitized() -> None:
    body = _extract_codex_preflight()

    assert "grep -E '^[[:space:]]*Logged in using ChatGPT[[:space:]]*$'" in body
    assert "for required_flag in --config --ephemeral" in body
    assert "GEAK_CODEX_NETWORK_ACCESS requires GEAK_CODEX_EXTERNAL_SANDBOX=1" in body
    for name in (
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AZURE_OPENAI_ENDPOINT",
        "CODEX_MODEL_PROVIDER",
        "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "LLM_GATEWAY_KEY",
    ):
        assert f"-u {name}" in body


def test_docker_demo_codex_mounts_reject_broad_profile_roots() -> None:
    for skill in DOCKER_DEMO_SKILLS:
        text = skill.read_text(encoding="utf-8")
        assert 'GEAK_CODEX_PROFILE="${CODEX_HOME:-$HOME/.codex}"' in text
        assert 'GEAK_CODEX_HOST_HOME="$(cd "$HOME" && pwd -P)"' in text
        assert 'GEAK_CODEX_TEMP_ROOT="${TMPDIR:-/tmp}"' in text
        assert '/|"$GEAK_CODEX_HOST_HOME"|"$GEAK_CODEX_TEMP_ROOT"|"$REPO_ROOT")' in text
        assert '"$REPO_ROOT/"*)' in text
        assert '--mount "type=bind,src=$GEAK_CODEX_PROFILE,dst=$GEAK_CODEX_PROFILE"' in text
