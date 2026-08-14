# DevSpace + Tailscale Funnel setup

This repository does not modify DevSpace upstream and does not automate the ChatGPT settings UI. DevSpace is a local MCP server; it can read, edit, and run commands inside the roots you approve, so choose narrow project directories rather than an entire drive.

## Prerequisites

- Node.js 22.19–26.x, npm, and Git Bash on Windows.
- Tailscale with MagicDNS, HTTPS, and Funnel permission enabled for this device.
- A stable MagicDNS hostname, for example `your-device.your-tailnet.ts.net`.

## First connection (explicit and interactive)

From this repository, preview the plan and check the roots:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\one --root C:\projects\two --hostname your-device.your-tailnet.ts.net --dry-run
```

On macOS, the same helper runs `npx` directly; Git Bash is not required:

```bash
python3 skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup \
  --root "$HOME/projects/one" \
  --root "$HOME/projects/two" \
  --hostname your-device.your-tailnet.ts.net \
  --dry-run
```

If DevSpace is already configured, a preview that names only a new root safely
merges the current `allowedRoots` and displays the complete list. It never
silently plans a subset that would remove an existing project. The interactive
init must persist every displayed root or setup stops before service restart.

After reviewing the plan, use `--apply`. It invokes `devspace init` through Git Bash, then starts `devspace serve` and configures a Tailscale HTTPS Funnel to the local default port (7676). DevSpace asks you to select roots and enter the public origin. Enter exactly the reviewed roots and `https://your-device.your-tailnet.ts.net`, without `/mcp`.

The helper will not overwrite an existing Funnel mapping. If port 443 is
already owned by another local service, choose an unused supported Funnel port
explicitly, for example `--public-port 8443`; the registration URL then becomes
`https://your-device.your-tailnet.ts.net:8443/mcp`.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\one --root C:\projects\two --hostname your-device.your-tailnet.ts.net --apply
```

For macOS, replace `--dry-run` with `--apply` in the preceding command.

DevSpace prints an Owner password during initialization and stores it in its standard local configuration. Do not put that password in a script, manifest, issue, or repository.

The managed service is launched with `DEVSPACE_TOOL_MODE=full`, which enables
read-only workspace discovery (`grep`, `glob`, and `ls`) without expanding the
approved roots. Keep the root list in DevSpace's configuration; the launch
environment only selects the tool mode.

The managed service also advertises `offline_access` together with the
`devspace` OAuth scope so ChatGPT can renew its authorization instead of losing
the connector after the one-hour access token expires. After upgrading an
existing setup from metadata that omitted `offline_access`, recreate or
reconnect the app once so ChatGPT reads the corrected OAuth metadata.

## Manual ChatGPT registration

Enable Developer Mode in ChatGPT and manually create the connector:

- Name: `codex`
- MCP URL: `https://your-device.your-tailnet.ts.net/mcp`

To use a different display name, store the identical name in
`%USERPROFILE%\.codex\chatgpt-workspace.json`, for example
`{"app_name":"codex"}`. New Oracle manifests then mention that registered app.
The `DevSpace` default remains only for compatibility with older installs that
do not yet have this explicit config.

Approve the initial Owner-password page when DevSpace asks. This tooling never opens settings, creates/deletes apps, picks permissions, inspects app lists, or selects an app in the composer.

After a manual first registration or requested reconnect, recycle the managed
DevSpace process once without changing its roots, Owner credential, OAuth
database, or Funnel hostname:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py post-register --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

```bash
python3 skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py post-register \
  --root "$HOME/projects/one" \
  --hostname your-device.your-tailnet.ts.net
```

Verify the registered app with a fresh regular, non-Pro Oracle `@codex`
read-only probe that opens the exact project root and reads a small directory
listing. Do not substitute Codex Desktop's built-in `DevSpace` plugin tools:
they are a separate connector and do not validate the manually registered
ChatGPT app. Never spend a Pro submission as the first connectivity probe.

Before the first DevSpace-backed Oracle question in a new project, the runner
checks that the normalized exact folder is present in local `allowedRoots`.
Parent, child, and similarly named folders do not qualify. Success is cached
against the DevSpace config SHA-256, so later questions for the same project do
not repeat endpoint/read or ChatGPT app checks; changing the config triggers a
lightweight revalidation. A missing exact root returns
`DEVSPACE_EXACT_ROOT_UNAVAILABLE` before Oracle or a browser session exists.

## Read-only diagnosis

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py doctor --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

Diagnosis checks local DevSpace `/mcp`, then `tailscale funnel status --json`,
then the public `/mcp` endpoint. If the endpoint is healthy but a ChatGPT tool
call fails just after manual registration or reconnect, run the explicit
`post-register` refresh once and repeat only the regular read-only Oracle
probe. If it still fails, keep the server running and report the same connector
URL; do not automate deletion, re-registration, or repeated refreshes.

## Idempotent service/Funnel recovery

After a DevSpace or Tailscale restart, restore only the already-approved public
route with `ensure`. It first proves that the local MCP endpoint is healthy,
then reuses a matching Funnel or recreates the missing exact mapping. A port
listener alone is not accepted as DevSpace health, and a conflicting Funnel is
never overwritten.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py ensure --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

Run this command from the hidden startup wrapper after DevSpace becomes
healthy. It does not touch ChatGPT settings or app registration.

For login-time recovery, use `recover` from a hidden per-user startup entry.
Unlike `ensure`, it starts the exact hash-validated DevSpace service when the
local MCP endpoint is unavailable, then verifies or restores the same Funnel:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py recover --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

The command is idempotent, never overwrites a conflicting Funnel mapping, and
does not contain or print the DevSpace Owner credential.

New Funnel mappings use a bounded public `/mcp` readiness retry after the exact
local endpoint and mapping are healthy. A timeout reports the last redacted
probe instead of treating normal propagation delay as a configuration error.

The shipped `scripts/start_devspace_bootstrap.ps1` wrapper reads the current
root list from `%USERPROFILE%\.devspace\config.json` on every health cycle. It
reads only the non-secret host, ports, and Python path from the diagnostic
mirror at `%CODEX_HOME%\config\codexpro-devspace-bootstrap.json`, retries while
Tailscale is still settling, and writes monthly logs under
`%CODEX_HOME%\logs\codexpro-devspace`. On Windows, `setup --apply` registers and
starts the wrapper as a hidden per-user `Watch` process. The watcher checks the
local service and exact Funnel every five minutes and restores them if the
DevSpace child exits after login; a one-shot login command is not sufficient.
Do not place the Owner credential in its command or config. The mirror is
synchronized during setup, but it is never the runtime authority for
`allowedRoots`.

It also reports the required managed tool mode (`full`) and any persisted
`toolMode`. A configured non-`full` value is advisory failure because a
manually started service may not inherit the managed launch environment.

Tailscale Funnel makes the endpoint public. It requires Tailnet permissions and uses the device's stable MagicDNS name. Review Tailscale's policy and exposure rules before `--apply`.
