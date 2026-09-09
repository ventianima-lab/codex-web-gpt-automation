# Documentation

This index is the single map for Codex Web GPT Automation documentation.
`FIRST_INSTALL.md` and `FIRST_INSTALL.en.md` are the canonical localized procedures for installation and ChatGPT
connection. Other guides link to it rather than restating it. `INSTALL_AGENT.md`
is a deliberate short duplicate for an AI agent that received only the
repository URL; keep the two in sync when either changes.

## Start here

| Document | Language | Purpose |
|---|---|---|
| [Main README](../README.md) | 한국어 | Product overview and quick install |
| [English README](../README.en.md) | English | English product overview and quick install |
| [First Install](FIRST_INSTALL.md) | 한국어 | Canonical install-to-ChatGPT connection sequence |
| [First Install](FIRST_INSTALL.en.md) | English | Canonical install-to-ChatGPT connection sequence |
| [Install Contract](INSTALL_AGENT.md) | 한국어 | Agent-facing contract when only the repository URL is given |
| [Contributing](../CONTRIBUTING.md) | English | Development, tests, pull requests, security boundary |

## Operate

Complete first installation, then follow the shared automation policy. All
projects use the same mission-based flow with explicit model and effort choices.

| Document | Language | Authority |
|---|---|---|
| [Shared automation policy](AUTOMATION_POLICY.md) | English | Common new-work rules for the app and every project |
| [DevSpace + Tailscale](DEVSPACE_TAILSCALE_SETUP.md) | English | Managed DevSpace/Funnel setup and diagnosis |
| [Global ChatGPT Routing](GLOBAL_CHATGPT_ROUTING.md) | English | Shared policy reference and recovery boundaries |
| [macOS Ultrawork](MACOS_ULTRAWORK.md) | 한국어 | macOS lifecycle, launchd, long-run handoff |
| [Local Multi-GPT](LOCAL_MULTI_GPT.md) | English | Optional local parallel-advisory component |

## Understand the project

| Document | Purpose |
|---|---|
| [Architecture](ARCHITECTURE.md) | Current Oracle/DevSpace execution and lifecycle overview |
| [Brand guide](BRAND.md) | Product name, visual assets, terminology, attribution |
| [Versioning](VERSIONING.md) | SemVer policy and release source of truth |
| [Upstream runtime policy](UPSTREAM_RUNTIME_POLICY.md) | Newest-validated Oracle/DevSpace promotion, drift, and rollback LKG |
| [Changelog](CHANGELOG.md) | User-visible changes by release |
| [Release checklist](RELEASE_CHECKLIST.md) | Maintainer verification before tags and releases |
| [Security policy](../SECURITY.md) | Supported versions and private reporting |
| [Third-party notices](../THIRD_PARTY_NOTICES.md) | Upstream licenses and provenance |

## Frozen legacy reference

[Frozen Legacy](FROZEN_LEGACY.md) defines the exact recovery-only boundary.
Files whose names contain `codexpro`, `agbrowse`, `v2`, `v3`, or `v4` may be
retained compatibility contracts. They must not be presented as the current
product or as a fallback for new work.

The following documents are historical implementation references:

- [Retired Ultra Economy Mode](ULTRA_ECONOMY_MODE.md)
- [Retired Ultra GPT Mode](ULTRA_GPT_MODE.md)
- [Retired closed audit](STRICT_ULTRA.md)
- [Legacy goal supervisor](ARCHITECTURE_GOAL_SUPERVISOR_V1.md)
- [Legacy agbrowse v2](ARCHITECTURE_V2.md)
- [Legacy parallel implementation v3](ARCHITECTURE_V3.md)
- [Legacy comprehensive workflow v4](ARCHITECTURE_V4.md)
- [Legacy orchestrator runbook](codexpro-gpt55-orchestrator-runbook.md)
- [Legacy prompt architecture](gpt55-operation-mode-prompts.md)

## Documentation conventions

- Product name: **Codex Web GPT Automation**
- Repository/package name: `codex-web-gpt-automation`
- Manually registered ChatGPT app name in examples: `codex`
- Current transport: Oracle + DevSpace
- Legacy identifiers remain lowercase/code-form and are explained as
  compatibility IDs on first use.
- README files summarize. `FIRST_INSTALL.md`, routing, architecture, versioning,
  and release documents own their respective details.
- Commands must use placeholders; never publish hostnames, passwords, tokens,
  browser profiles, or user-specific project paths.
