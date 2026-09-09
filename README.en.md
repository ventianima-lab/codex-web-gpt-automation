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
manually register the ChatGPT app as `codex`, then verify one actual project read. Before updating an existing install,
read the [latest release notes](https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest).

When you hand only the repository URL to an AI coding agent, have it read the
[install contract](docs/INSTALL_AGENT.md) too. After installation the
`python onboard.py start` wizard walks the nine stages one at a time. Login and
app-registration confirmations remain user attestations; completion is reported
only after the real project-root read check passes.

## Why use it?

| Guarded | Recoverable | Web-first | Cross-platform |
|---|---|---|---|
| Exact project roots and mission hashes are bound before execution. | Interrupted work is harvested from its existing Oracle session, never blindly resubmitted. | One mission-based flow with explicit model and effort selection. | Receipt-backed install and rollback are tested on Windows and macOS. |

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
7. **Check app access** — read an approved file once using the selected model

When adding a project, preserve the complete existing root set and add only the
new exact folder. Do not inspect or automate ChatGPT app settings per task.

Keep an existing `codex` app's exact name and `/mcp` URL. When Actions are
stale, use the visible **Refresh**/**New refresh** control in the app detail;
if OAuth or tool calls remain stale, open `https://chatgpt.com/#settings/Plugins/`,
select the existing app, and use **Reconnect**. Business UI or an unavailable
Refresh control is not grounds to recreate the app: do so only when its record
is actually absent or corrupt. Run `post-register` exactly once only when
required, then read an approved file using the selected model and save the
result. No fixed tool sequence or audit receipts are required. A widget-domain warning alone does not
establish whether `read_chunk` is present.

## One execution flow

The app and every project follow the [shared automation policy](docs/AUTOMATION_POLICY.md).
Planning, research, review, and editing belong in the mission, not separate
execution modes. Select the model and effort explicitly.

The default compatibility route selects **Latest → Pro (6 Pro)**, never the
numeric GPT-5.6 row. Use temporary chats. Automatically enable and confirm temporary-chat
personalization before submission, save the result durably, then close only the owned tab.
Recover that same tab after timeout or connection failure; do not automatically
resubmit, archive, or restore conversations.

Project tests and safety rules remain. Forced tool ordering, three audit
receipts, recurring qualification, and magic completion markers do not.

## Run example

Create a UTF-8 mission inside the project and verify identity with a dry run.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" `
  --project-root C:\project `
  --mission-path C:\project\mission.md `
  --model latest `
  --effort pro `
  --dry-run
```

Remove `--dry-run` only when live execution is authorized.

## Safety contract

The [shared policy](docs/AUTOMATION_POLICY.md) preserves approved roots,
authentication, and task ownership. Save results before closing the owned tab;
never automatically resend on failure. Project tests and safety rules remain.
Never commit secrets, passwords, OAuth tokens, or browser profiles.
Report security issues through the [private security path](SECURITY.md).

## Documentation map

- [First install](docs/FIRST_INSTALL.en.md)
- [Shared automation policy](docs/AUTOMATION_POLICY.md)
- [DevSpace setup](docs/DEVSPACE_TAILSCALE_SETUP.md)
- [Architecture](docs/ARCHITECTURE.md) · [Documentation index](docs/README.md)
- [Historical recovery reference](docs/FROZEN_LEGACY.md)

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
