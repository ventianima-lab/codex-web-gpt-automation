# Codex Web GPT Automation First Install

This is the English install-to-verification path. The wizard selects Korean or
English from the shell locale; override it with `--lang en` or `--lang ko`.

## Start the wizard

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python install.py --dry-run
python install.py
python doctor.py
python onboard.py --lang en start --root <project-folder>
python onboard.py --lang en next
```

Repeat `--root` for every project. Existing DevSpace `allowedRoots` are merged
and preserved; malformed existing JSON fails closed. Tailscale Funnel is the
managed default. Cloudflare requires a named tunnel, ngrok requires a static or
reserved domain, and custom providers require a stable `https://.../mcp` URL
plus an OS login service. Ephemeral URLs never satisfy installation.

The first interactive install asks whether to install optional Local Multi-GPT.
The default is no. Use `--enable-local-multi-gpt` only when wanted. The wizard
persists the choice and does not complete installation until its exact MCP
registration passes doctor.

## Follow one stage at a time

```powershell
python onboard.py --lang en next
python onboard.py --lang en confirm <stage-id>
```

The stages are install, stable endpoint approval, DevSpace initialization,
reboot persistence, local/public endpoint checks, dedicated Oracle Chrome
login, scoped Local Network permission, ChatGPT app registration, and the final
real-root gate. Do not skip ahead.

The user personally completes Tailscale sign-in, DevSpace Owner-password input,
ChatGPT sign-in in the dedicated Oracle profile, developer-mode activation, app
registration, and Owner OAuth approval. The agent must never request or store
passwords, tokens, cookies, or OAuth secrets and must not automate ChatGPT
settings or app selection.

Before changing Chrome Local Network policy, record explicit consent:

```powershell
python onboard.py consent 06b_local_network_access
```

The change is scoped to `https://chatgpt.com`; unrelated Chrome policy entries
and the everyday Chrome profile remain untouched.

Register the app as `codex` with the exact stable `/mcp` URL. Depending on the
account UI, check either Settings > Apps > Advanced settings > Developer mode,
or Settings > Plugins > Developer mode. A missing Create button is first a UI,
workspace, or developer-mode diagnostic—not proof that a higher plan is
required.

ChatGPT keeps a registered app's Action list as a snapshot. If a fresh final
canary sees `open_workspace` and `read` but no `read_chunk`, or it sees no
server-generated Audit receipt IDs, do not accept the partial result. The user
must manually open the exact `codex` app's overflow menu under **Workspace
settings > Apps**, select **Action control > Refresh**, then review and enable
the new Actions. On Business, or when Refresh is unavailable, recreate and
publish the app with the same exact `/mcp` URL, review and enable the Actions
currently exposed by the server, and complete Owner approval manually. Agents must not automate
these ChatGPT settings. Afterwards run the one `post-register` command shown by
stage `08_final_gate`, then run a fresh regular non-Pro auditNonce canary.

## Final gate

Unauthenticated local and public `/mcp` returning HTTP 401 is healthy. The final
gate uses a fresh regular non-Pro `GPT-5.6` extra-high Oracle read of the exact
project root through the registered app. It does not accept the built-in Codex
Desktop connector, Pro, arbitrary prose, or an unbound directory listing.

First place a short read-only canary mission inside the exact project root and
generate the manifest plus dry-run/live commands from the current Codex task:

```powershell
python onboard.py prepare-final-gate `
  --root <project-folder> `
  --mission-path <project-folder>\missions\onboarding-final-gate.md
```

Run the emitted `dry_run_command` first and verify `submission_action=none`,
then run the emitted `run_command` exactly once from the same Codex task. An
ordinary terminal or a different Codex task fails closed on task ownership.

```powershell
python onboard.py record-final-gate `
  --run-dir <Oracle-run-directory> `
  --root <project-folder> `
  --evidence "Exact root and listing summary" `
  --listing <observed-entry>
```

The wizard revalidates the run location, exact root/app identity, regular model
and effort, terminal EXECUTED state, conversation URL, output hash, listing,
workspace identity, and final `TASK_OUTCOME: EXECUTED` marker. Only
`Full install and real project-root read verified` means the installation is
complete.

The canary must prove `open_workspace`, a separate `read`, and complete
offset-zero `read_chunk` of the same file, with all three server-generated
receipt IDs echoed in its answer. `open_workspace` plus `read` is still a
failure, including after an app refresh.

For detailed Tailscale service setup and recovery, see
[DevSpace + Tailscale](DEVSPACE_TAILSCALE_SETUP.md). The Korean full guide is
[FIRST_INSTALL.md](FIRST_INSTALL.md).
