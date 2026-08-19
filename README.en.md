<p align="center">
  <img src="docs/assets/brand/banner.svg" alt="Codex Web GPT Automation" width="100%">
</p>

<p align="center">
  <a href="https://github.com/ventianima-lab/codex-web-gpt-automation/actions/workflows/release-portability.yml"><img alt="CI" src="https://github.com/ventianima-lab/codex-web-gpt-automation/actions/workflows/release-portability.yml/badge.svg"></a>
  <a href="https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/tag/ventianima-lab/codex-web-gpt-automation?sort=semver&label=release"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/ventianima-lab/codex-web-gpt-automation"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-334155">
  <img alt="Oracle" src="https://img.shields.io/badge/Oracle-0.17.1-8B5CF6">
  <img alt="DevSpace" src="https://img.shields.io/badge/DevSpace-1.0.4-14B8A6">
</p>

<p align="center">
  <strong>A guarded, recoverable web ChatGPT execution layer for local Codex projects.</strong>
</p>

<p align="center">
  <a href="README.md">한국어</a> · English · <a href="docs/README.md">All documentation</a>
</p>

> [!IMPORTANT]
> This is a community project, not an official OpenAI product. The user must
> complete ChatGPT sign-in, Developer Mode app registration, and DevSpace Owner
> approval manually.

## Start here

| First install | Already installed | Troubleshooting | Contributing |
|---|---|---|---|
| [First-install guide](docs/FIRST_INSTALL.md) | Run `python doctor.py` | [Diagnostics and recovery](docs/README.md) | [Contribution guide](CONTRIBUTING.md) |

Follow this order: install, register the exact DevSpace root, sign in to the
dedicated Oracle browser, manually register the ChatGPT app as `codex`, then
run an ordinary non-Pro connection probe. Before updating an existing install,
read the [latest release notes](https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest).

## Why use it?

| Guarded | Recoverable | Web-first | Cross-platform |
|---|---|---|---|
| Exact project roots and mission hashes are bound before execution. | Interrupted work is harvested from its existing Oracle session, never blindly resubmitted. | Planning, research, implementation, and review run in separate web ChatGPT sessions. | Receipt-backed install and rollback are tested on Windows and macOS. |

Codex Web GPT Automation uses [Oracle](https://github.com/steipete/oracle) to
run signed-in ChatGPT browser sessions and
[DevSpace](https://github.com/Waishnav/devspace) to expose only project roots
approved by the user. Local Codex owns transport identity, recovery, hashes,
and the final deterministic gate.

```text
Local Codex
  `- UTF-8 mission + exact project root + SHA-256
       `- Oracle -> signed-in web ChatGPT session
            `- DevSpace -> approved projects only
                 `- harvested result -> identity, hash, final gate
```

## Three-minute install

### Windows

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
.\install.ps1 -WhatIf
.\install.ps1
python doctor.py
```

### macOS

```bash
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python3 install.py --dry-run
python3 install.py
python3 doctor.py
```

The first interactive install asks whether to add the optional Local Multi-GPT
component and defaults to `No`. The installer backs up existing global files
and writes a receipt under `~/.codex/receipts`. Restart Codex after installation.

> [!NOTE]
> Installing files does not finish the ChatGPT connection. Complete the
> one-time connection sequence below.

## First connection sequence

Order matters. [First Install](docs/FIRST_INSTALL.md) is the authoritative guide
for exact commands and provider-specific branches.

1. **Choose a stable public route** — Tailscale Funnel recommended; Cloudflare
   Named Tunnel, ngrok reserved domain, or a custom HTTPS proxy supported
2. **Configure DevSpace** — register every exact project root and public origin
3. **Protect Owner approval data** — never copy the password into CLI, Git, or logs
4. **Verify restart recovery** — confirm local/public endpoints and root persistence
5. **Sign in to the Oracle-only browser** — keep it separate from daily Chrome
6. **Register the ChatGPT app manually** — name `codex`, URL `https://stable-host/mcp`
7. **Run a regular GPT read probe** — validate `@codex` without consuming Pro

When adding a project, preserve the complete existing root set and add only the
new exact folder. Do not inspect or automate ChatGPT app settings per task.

## Choose a mode

| Desired result | Mode | Route |
|---|---|---|
| Questions, analysis, small work | `direct` | Oracle + DevSpace |
| Design before implementation | `plan` | Read-only web session |
| Independent code or plan review | `review` | Read-only web session |
| Scoped changes | `edit` | Web implementation and tests |
| One-pass execution | `orchestrator` | Single web session |
| Public-source investigation | `deep-research` | Oracle Deep Research |
| Parallel independent perspectives | Web Multi-GPT | Multiple Oracle sessions + merger |
| PC-local advice and counterexamples | Local Multi-GPT | Optional, Luna Max, read-only |
| Plan through final gate | comprehensive mode | Staged web workflow |
| Minimize local model cost | `ultra-economy` | Luna Max command + separate web stages |
| Codex Ultra-style web delegation | `ultra-gpt` | Web plan/review + parallel isolated-worktree writers + merge/verification |
| Explicitly requested Pro work | `pro` | GPT-5.6 Sol Pro + read/write DevSpace |

Natural-language aliases use the same routes: `orchestrator` / orchestrator and
`deep-research` / deep research. Regular web work defaults to the highest supported non-Pro reasoning tier. Pro is quota-limited, never auto-selected, and runs only after an explicit request. Qualified Pro uses Oracle + read/write DevSpace; explicit `pro-attachment` is reserved for immutable evidence that the approved workspace cannot read.

See [Global Routing](docs/GLOBAL_CHATGPT_ROUTING.md) for selection rules,
[Ultra Economy Mode](docs/ULTRA_ECONOMY_MODE.md), and
[Ultra GPT Mode](docs/ULTRA_GPT_MODE.md) for their strict contracts.

## Run example

Create a UTF-8 mission inside the project and verify identity with a dry run.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" `
  --mode orchestrator `
  --project-root C:\project `
  --mission-path C:\project\mission.md `
  --manifest-output C:\project\.ai-bridge\oracle.json `
  --reasoning-level "Very High" `
  --dry-run
```

Remove `--dry-run` only when live execution is authorized.

## Safety contract

- Allow one active or uncertain Oracle workflow per project.
- Qualify the exact root before the first DevSpace submission for a new project.
- Regular web work defaults to the highest supported non-Pro reasoning tier. Pro requires explicit opt-in and is never an automatic upgrade.
- Explicit Pro may perform mission-authorized writes and commands inside the exact root, under the repository safety policy.
- Post-submit failure recovers the existing slug and URL and never resubmits the task.
- Browser or local-process exit alone is not evidence that web work failed.
- Never commit secrets, Owner passwords, OAuth tokens, or browser profiles.
- `codexpro-*` remains only as an internal compatibility ID for old receipts,
  schemas, and recovery assets. It is not the product name or a new-work route.

Report security issues through the private path in [Security Policy](SECURITY.md),
not a public issue.

## Documentation map

| Start | Operate | Advanced modes | Project |
|---|---|---|---|
| [First Install](docs/FIRST_INSTALL.md) | [DevSpace + Tailscale](docs/DEVSPACE_TAILSCALE_SETUP.md) | [Ultra Economy](docs/ULTRA_ECONOMY_MODE.md) · [Ultra GPT](docs/ULTRA_GPT_MODE.md) | [Architecture overview](docs/ARCHITECTURE.md) |
| [Documentation index](docs/README.md) | [Global Routing](docs/GLOBAL_CHATGPT_ROUTING.md) | [Local Multi-GPT](docs/LOCAL_MULTI_GPT.md) | [Changelog](docs/CHANGELOG.md) |
| [Contributing](CONTRIBUTING.md) | [macOS Ultrawork](docs/MACOS_ULTRAWORK.md) | [Frozen legacy boundary](docs/FROZEN_LEGACY.md) | [Versioning](docs/VERSIONING.md) |

## Versions and support

This project follows [Semantic Versioning](https://semver.org/) using
`MAJOR.MINOR.PATCH`. `package.json`, `package-lock.json`,
`install-manifest.json`, the Git tag, and the GitHub Release must identify the
same version. Read the [changelog](docs/CHANGELOG.md) before upgrading.

The current tested baseline is Oracle `0.17.1`, DevSpace `1.0.4`, Node.js
`>=22.19 <27`, Windows 11, and macOS 12 or newer.

## License

[MIT License](LICENSE). Third-party copyrights and licenses for Oracle,
DevSpace, and other components are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
