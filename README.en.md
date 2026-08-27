<p align="center">
  <img src="docs/assets/brand/banner.svg" alt="Codex Web GPT Automation" width="100%">
</p>

<p align="center">
  <a href="https://github.com/ventianima-lab/codex-web-gpt-automation/actions/workflows/release-portability.yml"><img alt="CI" src="https://github.com/ventianima-lab/codex-web-gpt-automation/actions/workflows/release-portability.yml/badge.svg"></a>
  <a href="https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/tag/ventianima-lab/codex-web-gpt-automation?sort=semver&label=release"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/ventianima-lab/codex-web-gpt-automation"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-334155">
  <img alt="Oracle" src="https://img.shields.io/badge/Oracle-0.18.0-8B5CF6">
  <img alt="DevSpace" src="https://img.shields.io/badge/DevSpace-1.0.8-14B8A6">
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
| [First-install guide](docs/FIRST_INSTALL.en.md) | Run `python doctor.py` | [Diagnostics and recovery](docs/README.md) | [Contribution guide](CONTRIBUTING.md) |

Follow this order: install, approve the stable HTTPS endpoint, register the
exact DevSpace root, establish reboot persistence, verify both endpoints, sign
in to the dedicated Oracle browser, grant scoped Local Network access,
manually register the ChatGPT app as `codex`, then run an ordinary non-Pro
connection probe. Before updating an existing install,
read the [latest release notes](https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest).

When you hand only the repository URL to an AI coding agent, have it read the
[install contract](docs/INSTALL_AGENT.md) too. After installation the
`python onboard.py start` wizard walks the nine stages one at a time. Login and
app-registration confirmations remain user attestations; completion is reported
only after the real project-root read check passes.

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

Order matters. [First Install](docs/FIRST_INSTALL.en.md) is the authoritative English guide
for exact commands and provider-specific branches.

1. **Choose a stable public route** — Tailscale Funnel recommended; Cloudflare
   Named Tunnel, ngrok reserved domain, or a custom HTTPS proxy supported
2. **Configure DevSpace** — register every exact project root and public origin
3. **Protect Owner approval data** — never copy the password into CLI, Git, or logs
4. **Verify restart recovery** — confirm local/public endpoints and root persistence
5. **Sign in and persist Local network access** — keep the Oracle browser separate; on Windows the helper prefers the exact-origin `chatgpt.com` policy and falls back to a backed-up, receipted Oracle seed-profile grant when policy ACLs are locked
6. **Register the ChatGPT app manually** — name `codex`, URL `https://stable-host/mcp`
7. **Run a regular GPT read probe** — validate `@codex` without consuming Pro

When adding a project, preserve the complete existing root set and add only the
new exact folder. Do not inspect or automate ChatGPT app settings per task.

Keep an existing `codex` app's exact name and `/mcp` URL. When Actions are
stale, use the visible **Refresh**/**New refresh** control in the app detail;
if OAuth or tool calls remain stale, open `https://chatgpt.com/#settings/Plugins/`,
select the existing app, and use **Reconnect**. Business UI or an unavailable
Refresh control is not grounds to recreate the app: do so only when its record
is actually absent or corrupt. Run `post-register` exactly once only when
required, then use a fresh regular non-Pro auditNonce canary to prove
`open_workspace → read → read_chunk`. A widget-domain warning alone does not
establish whether `read_chunk` is present.

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
| Codex Ultra-style web delegation | `ultra-gpt` | Web plan/review + parallel isolated-worktree writers + merge/verification; optional SHA-bound closed audit |
| Explicitly requested Pro work | `pro` | GPT-5.6 Sol Pro + read-only DevSpace design, advice, or review |

Natural-language aliases use the same routes: `orchestrator` / orchestrator and
`deep-research` / deep research. Regular web work defaults to the highest supported non-Pro reasoning tier. Pro is quota-limited, never auto-selected, and runs only after an explicit request. Every new qualified Pro run uses Oracle + read-only DevSpace for design, advice, or review. A regular `GPT-5.6` `extra-high` DevSpace stage performs any file creation, edit, removal, or command. Explicit `pro-attachment` remains a separate read-only immutable-evidence route and is never an automatic fallback. Persisted legacy `pro-devspace` write runs retain their exact original authority only during recovery.

See [Global Routing](docs/GLOBAL_CHATGPT_ROUTING.md) for selection rules,
[Ultra Economy Mode](docs/ULTRA_ECONOMY_MODE.md), and
[Ultra GPT Mode](docs/ULTRA_GPT_MODE.md) for its execution contract and optional closed audit.

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

- Allow one active or uncertain Oracle workflow per Codex-task/project pair. Different tasks may run concurrently at the same root under separate ownership and must never recover, harvest, or stop each other.
- Qualify the exact root before the first DevSpace submission for a new project.
- Regular web work defaults to the highest supported non-Pro reasoning tier. Pro requires explicit opt-in and is never an automatic upgrade.
- New explicit Pro is read-only inside the exact root and is limited to design, advice, or review. A regular `GPT-5.6` `extra-high` DevSpace stage performs mission-authorized writes, removals, and commands; persisted legacy `pro-devspace` write runs preserve their original authority only during exact recovery.
- Continued discussion in the same read-only Pro conversation uses only an internal follow-up round against a task-bound terminal parent. Every round re-proves the unchanged conversation and records mission/state/output/transcript hashes; raw Oracle follow-up injection and new-conversation fallback remain forbidden. Dry-run writes no reservation. A live local-preflight failure produces child state/logs and hash-bound launch/result evidence. One parent conversation serializes controllers even across different round keys. Preserve historical reservation-only keys without replay or deletion; after proving the old controller ended, use a new round key.
- Post-submit failure recovers the existing slug and URL and never resubmits the task.
- If Oracle saved official terminal output and completed metadata but the outer state transition was missed, only the owner task may use hash-bound `settle-saved-output`. A proven reserved-versus-observed CDP port mismatch seals a separate v2 browser identity receipt so the exact conversation remains eligible as a follow-up parent. A run already reconciled by v1.19.5 may be migrated only with owner-only `seal-saved-output-browser-identity`; ordinary recovery authority is not relaxed.
- Browser or local-process exit alone is not evidence that web work failed.
- Never commit secrets, Owner passwords, OAuth tokens, or browser profiles.
- `codexpro-*` remains only as an internal compatibility ID for old receipts,
  schemas, and recovery assets. It is not the product name or a new-work route.

Report security issues through the private path in [Security Policy](SECURITY.md),
not a public issue.

## Documentation map

| Start | Operate | Advanced modes | Project |
|---|---|---|---|
| [First Install](docs/FIRST_INSTALL.en.md) | [DevSpace + Tailscale](docs/DEVSPACE_TAILSCALE_SETUP.md) | [Ultra Economy](docs/ULTRA_ECONOMY_MODE.md) · [Ultra GPT](docs/ULTRA_GPT_MODE.md) | [Architecture overview](docs/ARCHITECTURE.md) |
| [Documentation index](docs/README.md) | [Global Routing](docs/GLOBAL_CHATGPT_ROUTING.md) | [Local Multi-GPT](docs/LOCAL_MULTI_GPT.md) | [Changelog](docs/CHANGELOG.md) |
| [Contributing](CONTRIBUTING.md) | [macOS Ultrawork](docs/MACOS_ULTRAWORK.md) | [Frozen legacy boundary](docs/FROZEN_LEGACY.md) | [Versioning](docs/VERSIONING.md) |

## Versions and support

This project follows [Semantic Versioning](https://semver.org/) using
`MAJOR.MINOR.PATCH`. `package.json`, `package-lock.json`,
`install-manifest.json`, the Git tag, and the GitHub Release must identify the
same version. Read the [changelog](docs/CHANGELOG.md) before upgrading.

The current tested baseline is Oracle `0.18.0`, DevSpace `1.0.8`, Node.js
`>=24 <27`, Windows 11, and macOS 12 or newer. Official npm `latest` releases
become candidates immediately, but only an isolated archive, patch,
no-submission, and cross-platform validation plus review can promote them to
current. The six-hour reporter only maintains the drift issue. A separate
scheduled Codex maintainer starts validation within 24 hours and owns the PR,
exact-commit CI, release, lifecycle install, and one safe-window DevSpace
restart, targeting a clean promotion within 48 hours. Stable patch/minor
candidates have standing approval only after all gates pass; major/breaking,
permission/OAuth, patch-conflict, failed, ambiguous, and unsafe-restart cases
still require explicit user approval. Its checked-in contract is audited by
`python scripts/verify_upstream_runtime_maintainer.py` and is never auto-registered
on downstream machines. Oracle `0.17.1` and DevSpace `1.0.7` remain rollback LKG and exact
legacy-recovery versions, not defaults for new work. See the
[upstream runtime policy](docs/UPSTREAM_RUNTIME_POLICY.md).

The WebJjonku Linux archive-verification profile uses the same Oracle `0.18.0`
current.

```sh
python bin/chatgpt_oracle_compat.py --profile webjjonku-linux --resolved-version "oracle 0.18.0" --package-root /exact/node_modules/@steipete/oracle --package-archive /exact/steipete-oracle-0.18.0.tgz
```

The scoped profile requires all three explicit version, installed-root, and
archive arguments; it never relies on platform-specific package discovery.

## License

[MIT License](LICENSE). Third-party copyrights and licenses for Oracle,
DevSpace, and other components are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
