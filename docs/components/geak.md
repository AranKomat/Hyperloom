---
myst:
    html_meta:
        "description": "Learn about GEAK, Hyperloom's agent-driven GPU kernel optimization framework. Covers Triton, HIP, and FlyDSL kernel rewriting, parallel optimization, and patch validation."
        "keywords": "GEAK, Hyperloom, GPU kernel optimization, Triton, HIP, FlyDSL, AMD GPU, ROCm, kernel rewriting, benchmarking, parallel optimization, LLM inference, agent, Ray"
---
# GEAK

GEAK (Generating Efficient AI-Centric Kernels) is a multi-agent framework for end-to-end GPU kernel
optimization in real codebases. It runs a closed loop of profiling, optimization, and
validation, and produces reviewable patches backed by reproducible benchmarks.
GEAK supports Triton, HIP (and CUDA, Composable Kernel (CK), and HSA Code Object (HSACO)), and FlyDSL
kernels. Its role agents can run through Claude Code (the default) or the Codex CLI. It ships two
deterministic JS Workflows — `e2e_workflow` for whole-model serving throughput and `kernel_workflow`
for single kernels — with a deterministic control plane (budget loop, parallel fan-out, verification,
stop conditions) that invokes LLM agents only for judgment.

Within Hyperloom, GEAK is the whole-pipeline end-to-end optimization delegate: when a workload is
handed off, the orchestrator invokes GEAK once at the kernel-agent phase through the stable
`interface/run_e2e.py` contract (a `handoff.json` in, a `result.json` back). Parallel exploration of
candidate kernels then happens inside GEAK's Workflows on the on-box GPUs.

- **Source**: <https://github.com/AMD-AGI/GEAK>
- **License**: MIT

## Role in Hyperloom

Hyperloom uses GEAK as the **whole-pipeline e2e delegate** when
`KERNEL_OPT_BACKEND_ORDER=geak` (the bare-metal default). In this mode the
orchestrator hands the optimization workload to
`src/hyperloom/agents/kernel/tools/backends/geak_runner.py`, which resolves the
GEAK checkout and launches GEAK's e2e runner (`interface/run_e2e.py`) with the
generated session context.

When GEAK owns the phase it runs the whole optimization loop itself — both the
end-to-end serving optimization *and* the per-kernel work underneath it, since
GEAK's `e2e_workflow` recursively drives `kernel_workflow` to author and tune the
individual hot kernels worth fixing. See
[Hyperloom optimization loop](../conceptual/optimization-loop.md).

## Select Claude Code or Codex

Claude Code remains the default, so existing installations need no change. To
run GEAK's standard whole-pipeline path with Codex role agents, authenticate the
Codex CLI with the ChatGPT account whose subscription should be used, then set
the provider explicitly:

```bash
codex login
codex login status       # must report: Logged in using ChatGPT
export GEAK_AGENT_PROVIDER=codex
```

This switch is scoped to GEAK. Hyperloom orchestration and the independent
Forge backend keep their existing provider requirements; the installer may
therefore still provision Claude tooling for Forge even when GEAK uses Codex.

Codex mode requires Node.js 18 or newer and a current Codex CLI. Installer and
startup preflight verify the `codex exec` compatibility flags GEAK needs,
including `--config`, `--ephemeral`, schema output, and user-config/rules
isolation. It does not use or require `OPENAI_API_KEY`: Hyperloom verifies
ChatGPT login after removing API-key overrides from the probe and from GEAK's
Codex children. The runtime
inherits the user's normal Codex profile — an explicitly set `CODEX_HOME`, or
the CLI default `~/.codex` — rather than creating a run-private profile. See the
official [Codex authentication guide](https://learn.chatgpt.com/docs/auth) for
subscription and headless-device login options.

When Hyperloom runs in Docker or on a remote Ray worker, the login must be
available in that actual execution environment. Mount the existing Codex
profile at the same path and pass `CODEX_HOME` when it is non-default; never
copy `auth.json` into the repository, an image layer, `.env`, or an experiment
artifact. Bind only the scoped profile directory — never filesystem root, the
whole home/temp directory, or the Hyperloom workspace. `codex login status`
must succeed inside the container/worker before the GEAK phase starts.

The installer and runtime forward the non-secret `GEAK_NODE_BIN` and
`GEAK_CODEX_*` controls documented in the
[environment-variable reference](../reference/environment-variables.md#geak-role-agent-runtime).
The safe execution default is `GEAK_CODEX_SANDBOX=workspace-write`; additional
writable paths must be listed explicitly with `GEAK_CODEX_ADD_DIRS`.

Command network access is not safe merely because filesystem writes remain
workspace-scoped: a command can read the mounted Codex credential and send it
out. When the workflow must reach a local model server or network-backed tool,
run it in a dedicated worker/container with egress restricted to loopback and
the required destinations, no unrelated secrets or mounts, and a short-lived
ChatGPT login. Then set both `GEAK_CODEX_EXTERNAL_SANDBOX=1` and
`GEAK_CODEX_NETWORK_ACCESS=1`. Do not enable command network on a normal host.

Some full GPU campaigns need system writes beyond the scoped workspace. Only
inside a trusted worker/container that already enforces the filesystem boundary,
opt in explicitly with both `GEAK_CODEX_EXTERNAL_SANDBOX=1` and
`GEAK_CODEX_BYPASS_SANDBOX=1`. Hyperloom never infers or enables that pair.
The same worker must be able to read the mounted ChatGPT-authenticated
`CODEX_HOME`; a login on the submit host alone is insufficient.

## GEAK documentation

For detailed documentation on GEAK, see [GEAK on ROCm Docs](https://rocm.docs.amd.com/projects/geak/en/latest/).
