# DevSpace + Tailscale Funnel setup

This repository does not modify DevSpace upstream and does not automate the ChatGPT settings UI. DevSpace is a local MCP server; it can read, edit, and run commands inside the roots you approve, so choose narrow project directories rather than an entire drive.

## Prerequisites

- Node.js 24–26.x, npm, and Git Bash on Windows.
- Tailscale with MagicDNS, HTTPS, and Funnel permission enabled for this device.
- A stable MagicDNS hostname, for example `your-device.your-tailnet.ts.net`.

## First connection (explicit and interactive)

From this repository, preview the plan and check the roots:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup --root C:\projects\one --root C:\projects\two --hostname your-device.your-tailnet.ts.net --dry-run
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

DevSpace prints an Owner password during initialization and stores it in its standard local configuration. Do not put that password in a script, manifest, issue, or repository.

The managed service is launched with `DEVSPACE_TOOL_MODE=full`, which enables
read-only workspace discovery (`grep`, `glob`, and `ls`) without expanding the
approved roots. Keep the root list in DevSpace's configuration; the launch
environment only selects the tool mode.

The managed service also advertises `offline_access` together with the
`devspace` OAuth scope so ChatGPT can renew its authorization instead of losing
the connector after the one-hour access token expires. After upgrading an
existing setup from metadata that omitted `offline_access`, keep the existing
`codex` app and exact URL, then manually reconnect that app once so ChatGPT
reads the corrected OAuth metadata. Recreate only if the existing app record is
actually absent or corrupt.

The managed launch also pins `DEVSPACE_SUBAGENTS=false`. DevSpace 1.0.8's
optional local-agent daemon and provider CLI adapters remain disabled unless
the user separately and explicitly approves that additional execution surface;
an inherited or persisted setting must not silently enable it.

Managed recovery primes the exact pinned package before native validation and
permits a rebuild only for the hash-bound `better-sqlite3@12.11.1` dependency.
The resulting service is supervised with redacted, bounded live logs and
PID/start/exit evidence. It requires two consecutive loopback `/mcp` and
`/healthz` observations before repairing Funnel routing.

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

After a manual first registration or requested reconnect, run the following
managed refresh exactly once only when the stage or diagnosis requires it. It
does not change roots, Owner credential, OAuth database, or Funnel hostname:

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py post-register --root C:\projects\one --hostname your-device.your-tailnet.ts.net
```

Verify the registered app with a fresh regular, non-Pro Oracle `@codex`
read-only probe that opens the exact project root and reads a small directory
listing. Do not substitute Codex Desktop's built-in `DevSpace` plugin tools:
they are a separate connector and do not validate the manually registered
ChatGPT app. Never spend a Pro submission as the first connectivity probe. The
Oracle composer names the exact project root before the mission path so a
mission directory cannot be mistaken for the workspace root.

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
call is stale, first preserve the existing `codex` app and exact URL: use the
visible Refresh/New refresh control in its app detail to update Actions. If
OAuth or calls remain stale, manually open `https://chatgpt.com/#settings/Plugins/`,
select the existing app, and use Reconnect. Run the explicit `post-register`
refresh exactly once only when the stage or diagnosis requires it, then repeat
only the fresh regular non-Pro auditNonce read-only Oracle probe. If it still
fails, keep the server running and report the same connector URL; do not
automate deletion, re-registration, or repeated refreshes. Business UI or an
unavailable Refresh control is not a re-registration fallback; recreation is
exceptional for an app record that is actually absent or corrupt.

A widget-domain warning concerns required app-submission UI metadata, not a
`read_chunk` Action inventory. The managed 1.0.8 compatibility patch now binds
that resource to the exact credential-free public HTTPS origin. If the warning
remains after installation, Refresh the existing app's Actions; do not recreate
the app. The warning alone still cannot establish that `read_chunk` is absent,
so the fresh canary remains the action-availability proof.

A local/public `401` proves that the DevSpace OAuth challenge is reachable; it
does **not** prove that ChatGPT's registered `@codex` account binding can mint a
token. Likewise, Codex Desktop's built-in `devspace_open_workspace` and
`codex_open_workspace` are distinct connector surfaces. A successful call on
one cannot clear `-32603` or `OAuth token request failed 503` on another. The
Oracle diagnosis report classifies a terminal `TASK_OUTCOME: BLOCKED` carrying
that OAuth 503 as `registered-app-oauth-token-request-503`, never as a complete
mission. Repair of that external binding requires the one manual reconnect
already described above, followed by one regular non-Pro probe; Pro remains
blocked until that exact registered-app probe succeeds.

If a terminal web answer reports the exact Oracle run's own live status instead
of performing the mission, diagnosis requires the full bounded
`post-submit-recursive-self-observation` signature: own run ID and slug,
running/pending state, absent output, the exact continue-observing audit phrase,
and terminal BLOCKED. The comprehensive controller releases its workflow scope
without automatic retry. Direct fresh-run authority requires the append-only,
three-artifact hash-bound `settle-recursive-self-observation` receipt; a general
BLOCKED answer or a simple run-ID mention never qualifies.

When a previously healthy long-running session fails only as its access token
expires, the managed DevSpace 1.0.8 compatibility layer also checks a bounded
server-side refresh replay grace. It returns the same rotated pair only for an
identical client, scope, and resource during a 30-second window, keeps at most
32 entries in memory, and rejects expiry, mismatch, or revocation. This avoids
parallel tool calls losing a rotated refresh token without making old tokens
durably reusable. Apply the hash-gated compatibility update, recycle only the
exact managed service once, and prove the registered app with a regular
non-Pro read plus no-op command canary. Do not delete OAuth state or reconnect
the app merely to exercise this repair.

The same exact-version compatibility layer adds a read-only `read_chunk`
tool for UTF-8 files whose first line exceeds the upstream 50KB reader limit.
Read from byte offset 0, reuse each returned `nextOffsetBytes`, and stop only
at `eof=true`; every chunk is capped at 24KiB and carries the same whole-file
SHA-256 and total byte count. Its doctor reconstructs an isolated 60KB-plus
single-line Unicode fixture, so this path does not depend on `bash`.

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
