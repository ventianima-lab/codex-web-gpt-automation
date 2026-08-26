---
name: chatgpt-workspace-setup
description: Part of the current Oracle path, perform the one-time, user-authorized DevSpace and stable HTTPS tunnel setup or read-only diagnosis for ChatGPT workspace access. Tailscale Funnel is the managed route. Never use this during ordinary GPT runs and never automate ChatGPT settings or app selection.
---

# ChatGPT Workspace Setup

Use this skill only for a first connection, an explicitly requested DevSpace/tunnel repair, or a read-only endpoint diagnosis. Ordinary ChatGPT modes must not call it. Follow `docs/FIRST_INSTALL.md` for the complete ordered installation; this helper directly manages only the Tailscale Funnel route.

## One-time setup

The user must provide every allowed project root and the Tailscale MagicDNS hostname. A drive root such as `C:\` is rejected. The setup process is intentionally interactive because DevSpace itself stores the Owner secret in its own standard location; never copy that secret into a manifest, log, or Git file.

When setup is invoked with only a new root, the preview reads the current
DevSpace `allowedRoots`, preserves every existing root, and displays the
complete merged list. For an existing installation, `--apply` backs up and
atomically updates the non-secret DevSpace config while preserving its other
fields. A first installation still uses DevSpace's interactive initialization.
The helper verifies that the complete list persisted before restarting the
service, preventing a one-root setup from silently removing approved projects.

Preview the exact setup plan first:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\example --hostname your-device.your-tailnet.ts.net --dry-run
```

Only after the user approves the interactive DevSpace initialization and public Funnel exposure:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\example --hostname your-device.your-tailnet.ts.net --apply
```

On a first installation, `--apply` attaches `devspace init` to the current
terminal, then performs the TTY-only Owner password keep/custom review. The
generated high-entropy value is the recommended default; custom values are
hidden-input, confirmed, and written only to DevSpace's private `auth.json`.
Existing installations never enter this secret flow while adding roots.
Afterward the helper starts `devspace serve` hidden and creates an HTTPS Funnel
to `127.0.0.1:7676`. During `devspace init`, enter only the listed roots and
the public origin `https://<hostname>` (without `/mcp`).

Before starting or restarting DevSpace 1.0.8, run the installed
`bin/chatgpt_devspace_compat.py`. It hash-validates the exact upstream
`dist/workspaces.js`, backs it up, and applies bounded concurrent discovery
that skips transient `.pytest-*` and cache trees. If it reports
`service_restart_required=true`, restart DevSpace before any Oracle
submission. Unknown versions or hashes fail closed.

DevSpace 1.0.8 also ships an optional local-agent daemon and provider CLI
adapters. The managed ChatGPT workspace service always sets
`DEVSPACE_SUBAGENTS=false`; enabling that separate execution surface requires
an explicit user action outside this setup skill.

The same hash-gated compatibility layer exposes read-only `read_chunk` for a
regular UTF-8 file whose single line exceeds the upstream 50KB line reader.
Start at `offsetBytes=0`, continue only with the returned
`nextOffsetBytes`, and require one stable whole-file SHA-256 through
`eof=true`. The isolated compatibility doctor reconstructs a 60KB-plus
single-line Unicode fixture before readiness.

On Windows, any Startup shortcut or service wrapper must read
`%USERPROFILE%\.devspace\config.json` at every launch and derive
`DEVSPACE_ALLOWED_ROOTS` from its current `allowedRoots`. Never hardcode a
second root list in the startup wrapper: DevSpace gives the environment
variable precedence over the persisted config, so a stale wrapper silently
removes newer projects after every reboot.

Every new or managed DevSpace service launch must set
`DEVSPACE_TOOL_MODE=full`. This retains the approved-root boundary while
making read-only workspace discovery tools such as `grep`, `glob`, and `ls`
available. Do not change ChatGPT connector settings to compensate for a tool
mode issue. `doctor` reports the managed launch setting and any persisted
`toolMode`; an explicitly non-`full` persisted mode requires service setup
review, while a running process environment is not inferred from an HTTP probe.

Managed launches also set
`DEVSPACE_OAUTH_SCOPES=devspace,offline_access`. DevSpace already issues refresh
tokens; advertising `offline_access` lets ChatGPT request and renew them. If an
older app registration was created before this metadata was exposed, the user
must reconnect or recreate that app once. Never automate that settings action.

Before every managed service launch, the helper loads `better-sqlite3` with the
active Node runtime and opens an in-memory database. A missing npm 12 native
binding fails closed with `DEVSPACE_NATIVE_BINDING_UNAVAILABLE`; never approve
an unbounded list of install scripts automatically.

The only app information to enter manually in ChatGPT Developer Mode is:

- Recommended app name: `codex`
- URL: `https://<hostname>/mcp`
- Complete the first Owner-password approval page that DevSpace presents.

Never open ChatGPT settings, register/delete an app, change permissions, inspect app lists, select an app name, or press Tab in the ChatGPT UI.

Immediately after a manual first registration or requested reconnect, recycle
the managed DevSpace process exactly once while preserving its configuration,
Owner credential, OAuth database, roots, and Funnel hostname:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py post-register --root C:\projects\example --hostname your-device.your-tailnet.ts.net
```

`post-register` also recycles only the exclusive managed HTTPS port before
reasserting the same Funnel target. It never uses the global `funnel reset`
operation and preserves a port that has additional path handlers.

Then verify the manually registered app with a fresh **regular, non-Pro**
Oracle `@codex` read-only probe that opens the exact project root and reads a
small directory listing. The probe must also issue a separate mission-file
`read` with the exact workspace ID returned by `open_workspace`, then
`read_chunk` that same file and report the server-computed complete SHA-256.
The local gate matches it to the bound mission bytes; bundled instructions in
the open response and local/public HTTP 401 health are not read-route evidence.
A changed workspace ID, digest mismatch, or `mcp_network_error` fails the probe
and keeps Pro blocked until a fresh regular non-Pro probe succeeds.
Codex Desktop's built-in `DevSpace` plugin is a
different connector; its tools cannot prove that the manually registered
ChatGPT app works. A Pro submission must never be the first connectivity test.

For a terminal `OAuth token request failed 503` that begins only when a
previously working long run reaches access-token expiry, run the exact
DevSpace compatibility helper before changing any credential or ChatGPT app
setting. Its 1.0.8 contract hash-gates a short, client/scope/resource-bound
refresh replay grace and exercises it against an isolated SQLite database.
Preserve the real OAuth database, roots, Owner credential, and Funnel; restart
only the exact managed service once, then require a regular non-Pro exact-root
read plus no-op command canary. Expired, revoked, mismatched, or unverified
replay remains fail-closed, and Pro stays blocked until the canary succeeds.

Before the first DevSpace-backed Oracle question for a new project, the Oracle
runner checks that the normalized exact project root is present in the local
`allowedRoots`. Parent, child, and similarly named roots do not qualify. A
successful qualification is cached against the exact config SHA-256; later
questions for that project do not repeat endpoint, read, OAuth, or app-setting
probes, while any config change triggers a lightweight recheck.

## Diagnosis

This is read-only and checks only local DevSpace, then Funnel status, then the public `/mcp` endpoint:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py doctor --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

If the public endpoint is healthy but a ChatGPT call still fails immediately
after a manual registration or reconnect, run `post-register` once and repeat
only the regular read-only Oracle probe. If that still fails, report the same
registration URL and stop. Do not re-register the app automatically or loop
the refresh.

For an explicitly requested service/Funnel repair, use the idempotent `ensure`
command after DevSpace starts:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py ensure --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

`ensure` requires the actual local MCP endpoint to respond before it reasserts
the exact Funnel mapping. It refuses a conflicting mapping and never changes
ChatGPT settings or app registration.
