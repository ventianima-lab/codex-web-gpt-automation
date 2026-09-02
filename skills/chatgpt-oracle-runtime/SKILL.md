---
name: chatgpt-oracle-runtime
description: "Current Oracle runtime path for new ChatGPT work: regular modes use highest-tier non-Pro DevSpace, and explicitly requested qualified Pro uses read-only DevSpace for design, advice, or review."
---

# ChatGPT Oracle Runtime

This is the only active browser path for all new GPT work. CodexPro and
agbrowse are frozen for exact legacy recovery only. Regular modes use DevSpace;
every explicitly requested new qualified Pro run uses the same app read-only
for design, advice, or review. A regular `GPT-5.6` `extra-high` DevSpace stage
owns all file creation, edits, removals, and commands. Explicit
`pro-attachment` remains a separate read-only immutable/external-evidence route
and is never an automatic fallback. Persisted legacy `pro-devspace` write runs
retain their exact authority only during recovery.

`chatgpt_oracle_dispatch.py` supports exactly `direct`, `plan`, `review`, `edit`,
`orchestrator`, `deep-research`, `manual`, and `pro`. `manual` is a supported
`manual-no-launch` profile, not a new submission route. `answer` in
`chatgpt-question-designer` is the prompt-design alias for dispatcher mode
`direct`, not a separate dispatcher key. Regular routes
select `gpt-5.6` and send only the configured app mention (default `@codex`)
plus the absolute project mission path and a compact exact-workspace guard. The web GPT must use only the exact
project root recorded in that mission, read the mission and applicable
`AGENTS.md` completely first, and may retry that same root once after a timeout.
It must not substitute a parent, child, active workspace, or shell boundary
workaround. Regular routes default to `gpt-5.6` with `extra-high`, the highest
supported non-Pro reasoning tier, and never auto-upgrade to Pro. Only explicit
`pro` mode selects `GPT-5.6 Sol` at the Pro effort and the
`pro-devspace-readonly` transport. It uses read-only DevSpace at the same exact
root for design, advice, or review and must not perform file
mutations or commands; a regular `GPT-5.6` `extra-high` stage owns those
actions. Explicit `pro-attachment` is limited to its read-only immutable or
external evidence contract and is never an automatic fallback.
Never infer Pro from task difficulty, invent xhigh, or silently downgrade.

On the first DevSpace-backed submission for a new project, the runner checks
exact equality with local DevSpace `allowedRoots` before creating the Oracle
run directory or browser session. It caches success against the config hash
and rechecks only after config changes. This is a local root guard, not a
repeated endpoint/read probe or ChatGPT app/settings inspection.

## Manifest

Require schema `codex.chatgpt.oracle-run/v1` with:

- `project_root`: absolute existing directory.
- `mission_path`: absolute UTF-8 regular file inside the project.
- `app_name`: one-line app name, without a leading `@`, for regular routes.
- `task_kind: pro`; new qualified Pro uses the same configured app name (default
  `codex`) and read-only DevSpace. Persisted legacy attachment records retain
  their original attachment metadata only during exact recovery.
- `mode`: `browser`.
- Optional `run_root`, `oracle_command`, `oracle_args`, `thinking_time`,
  hash-validated `copy_profile`, and mutex timeout.
- Regular direct/orchestrator manifests use `task_outcome_contract: "v1"`.

## Run

Preview first:

```powershell
python skills/chatgpt-oracle-runtime/scripts/run_chatgpt_oracle.py run --manifest C:\absolute\oracle-job.json --dry-run
```

The preview must include final argv, prompt first line, absolute mission path, SHA-256, and artifact paths without launching Oracle or a browser.
Use this wrapper preview only. Do not substitute Oracle's own browser `--dry-run`, because a supported Oracle current/LKG runtime may still enter browser preflight.

Execute only after an explicit live-run request:

```powershell
python skills/chatgpt-oracle-runtime/scripts/run_chatgpt_oracle.py run --manifest C:\absolute\oracle-job.json
```

Complete requires Oracle exit code zero, a nonempty `--write-output` artifact,
and—when `task_outcome_contract` is `v1`—a final
`TASK_OUTCOME: EXECUTED` marker. `TASK_OUTCOME: NOT_EXECUTED` and
`TASK_OUTCOME: BLOCKED` preserve terminal transport evidence but return
attention-required; transport success alone never claims project execution.
Every regular DevSpace prompt is also bound to its exact current run ID and
slug. The web worker must not inspect, wait for, poll, invoke, or recover that
Oracle controller identity or its state/output/transcript/observer, and must
not launch a nested Oracle run. It performs the requested mission directly.
Prompts require citations and Markdown reference definitions before the marker.
For provider-rendered compatibility, only one exact marker followed solely by
single-line HTTP(S) Markdown reference definitions or at most 32 bounded `↩`
rows beginning with a file-like citation is also classifiable. Rendered rows
may contain semicolon-separated file paths or a section annotation, but each is
limited to 512 characters. Ordinary prose, a row without a file-like citation,
an oversized footer, or a conflicting marker remains `unknown`.
A nonzero Oracle exit after launch, including a browser response timeout, is
`attention_required` rather than proof that the web session failed. It retains
same-task exact-run ownership and permits only exact-slug `live` or `harvest`
recovery; it never authorizes a replacement submission.
`--browser-timeout` is a browser observation window, not proof that the web run
ended. The default is aligned with the observed provider boundary. Separately,
4,800 seconds is only a caution/status-audit threshold: the runner records the
exact slug, process liveness, artifact progress, known conversation binding,
and terminal evidence, then keeps waiting on the same process. It never kills,
fails, releases, or replaces a run because that threshold elapsed.

## Explicit legacy lock retirement

When the user explicitly asks to release an identified legacy Oracle lock, use
the [mcp-update-guard retirement procedure](../mcp-update-guard/SKILL.md#explicit-legacy-lock-retirement).
The existing user instruction authorizes the bounded local retirement; no
special administrator phrase or repeated approval is required. The helper must
prove its stopped, unbound CDP-failure case and preserve the run in an archive
with receipts. This grants neither historical task ownership nor a replacement
submission. Verify disappearance from the task-unfiltered active-lock list;
a successful task-scoped preview is not release of the legacy lock itself.

## Read-only Pro follow-up round

When the user explicitly asks to continue one already-terminal read-only Pro
discussion, use only the internal follow-up lifecycle. The parent must belong
to the current Codex task, be terminal `EXECUTED`, use
`pro-devspace-readonly`, retain valid ownership/browser receipts and the exact
canonical conversation URL, and pass all stored mission/output identity
checks. Preview before sending:

```powershell
python skills/chatgpt-oracle-runtime/scripts/run_chatgpt_oracle.py followup --parent-run-dir C:\absolute\parent-run --mission-path C:\project\followup-round.md --round-key round-2 --dry-run
```

After explicit live authority, remove only `--dry-run`. Each round gets a new
Oracle run and slug but must reopen the same ChatGPT conversation. The runner
writes append-only `followup-rounds/<round-key>.json` and
`followup-rounds/<round-key>.result.json` receipts. A foreign or legacy-unbound
parent, duplicate round key, writable/attachment transport, missing or changed
conversation, tampered artifact, or uncertain identity fails closed. Never
inject raw `--followup`, `--browser-follow-up`, or `session`; never use recovery
to send a question; never create a replacement conversation after uncertainty.
New read-only Pro parents normalize default `archive=auto` to `never`, so do not
manually unarchive them or change ChatGPT settings. Explicit `archive=always`
means single-turn archival. Historical archived parents use only the bounded
exact-conversation compatibility restore and are re-archived afterward.
If that restore fails before the composer, do not run recovery or harvest.
Preserve the exact child and obtain explicit user no-submission confirmation,
then use the runner's exact `settle-no-submission` command. Older v1.18.5
children that already produced one exact no-live/no-URL/no-candidate harvest
pair remain compatible; never delete or edit those logs.

## Recovery

Recovery always reuses the stored Oracle slug and never restarts or submits:

```powershell
python skills/chatgpt-oracle-runtime/scripts/run_chatgpt_oracle.py recover --run-dir C:\absolute\run --action harvest
```

Use `--action live` only to keep following the same stored session. A successful recovery must write a nonempty stored `output.md`, update `state.json` to `complete`, and refresh `transcript.md`; exit code zero without output is `attention_required`.
If Oracle itself has already written the exact nonempty `output.md` and its
strict session metadata is `completed`, but the outer runner exited before
updating a still-`running` state, do not use recovery and never edit state by
hand. The owning Codex task may preview `settle-saved-output` with the exact
state, output, stdout, and Oracle-meta SHA-256 values, then remove only
`--dry-run`. This bounded path additionally requires the immutable ownership
and follow-up receipts, the exact parent conversation, one canonical stdout
saved-output record, a valid terminal outcome/schema, an empty stderr, and all
observer/controller/Chrome PIDs stopped. It writes an append-only settlement
receipt and is unavailable to foreign tasks, active processes, ordinary runs
with browser identity receipts, or changed/ambiguous evidence.
A proven reserved-versus-observed CDP port mismatch is sealed as a separate
browser identity receipt v2, without changing the ownership receipt's expected
port. For a run already reconciled by v1.19.5, the same owner may preview and
execute `seal-saved-output-browser-identity` with the exact saved-terminal-output
settlement SHA. This command never discovers or opens a browser, sends a prompt,
or changes a conversation; it only promotes the already hash-bound terminal
runtime tuple so the run can remain a follow-up parent.
Exact recovery is serialized by an exact-run mutex rather than re-entering the
project submission mutex. This lets the same slug harvest a provider-terminal
answer when a disconnected original observer still holds the submission mutex;
the unresolved run state continues to block every fresh submission until the
durable terminal artifact is committed. Recovery never stops or replaces a
live/uncertain provider session merely to acquire a lock.
The CLI keeps `--action live` bound to the same exact slug. At each 80-minute
caution interval it records a status audit and, if the observer process must
return while the session is still live, automatically opens another live
observer for that same saved session. Transient `stalled`, `running`, or
provider-delivery-timeout states keep the same authority and task-scoped project lock.
There is no time-based replacement, ownership release, or new prompt.
If Oracle proves both that no live tab matches the exact slug and that its
metadata has no recoverable canonical conversation URL, the runner returns
`recovery_binding_unavailable` immediately instead of repeating that invariant
failure. It preserves `submitted_unknown` ownership; restore the
exact persisted conversation URL before recovering the same slug, and never
replace or resubmit it.

Oracle's `Prompt did not appear in conversation before timeout (send may have
failed)` message is likewise submission-uncertain. No-live-tab plus missing
saved-URL recovery evidence does not mechanically prove non-submission. A
task-bound qualified-Pro run can fail before its conversation-bound browser
receipt exists. In that exact case only, `harvest --dry-run` may report
`bounded-prompt-timeout-harvest` after validating the immutable task/run/mission
ownership receipt, the exact recorded Oracle version's zero-turn commit probe, root composer, dynamic
CDP port, isolated profile and target, and absence of output or conversation.
Only prompt-free `harvest` is allowed; `live`, a foreign task, and any URL,
probe, port, profile, target, or output contradiction remain blocked. Run the
same exact-slug harvest once to create the normal no-tab/no-URL recovery
evidence; it does not release ownership.
An ordinary `devspace` run may instead fail because the recorded Oracle runtime cannot find
the requested model option before submission. That case remains
`submitted_unknown` until the same exact no-live/no-URL harvest and explicit
user confirmation are present. Settlement additionally binds the complete
Oracle launcher/error envelope, requested model and copied profile, exact
mission hashes, duplicate-free session metadata, `execute-browser`,
`promptSubmitted=false`, task ownership, dynamic CDP port, run-local browser
profile and target, and the ChatGPT root URL. The official harvest is admitted
only through that bounded prompt-free identity; `live` recovery stays blocked.
It does not apply to Pro, another transport, changed metadata, a conversation
URL, or durable output.
A task-bound mission may have been edited after the failed run. In that case
the immutable run copy and ownership receipt must still bind the same original
mission SHA-256; legacy-unbound runs continue to require current source bytes.
A maintenance owner may release that exact run only after explicit user
confirmation through `chatgpt_oracle_run.py settle-no-submission` with the
exact run directory, `--confirmation user-confirmed-no-submission`, and a
concise reason. The settlement is hash-bound to the comprehensive stage,
direct Web Multi child, or standalone qualified-Pro identity and immutable
mission evidence and does not launch Oracle. Comprehensive mode may consume
only one replacement for its binding; standalone qualified Pro permits only
the separately authorized single fresh retry with identical mission bytes.
For a persisted legacy `pro-attachment-only` run, the supported Oracle 0.17.1
attachment-upload timeout additionally requires an exact immutable attachment
manifest (path, size, and SHA-256 for every file), the upload-timeout marker,
matching stdout/transcript, no stderr, and exact no-live-tab/no-saved-URL
recovery hashes. It remains ineligible without the same explicit user token or
if any artifact has changed. This recovery rule does not authorize a new
attachment run.

A terminal BLOCKED answer is classified as
`post-submit-recursive-self-observation` only when it contains the exact own
run ID and slug together with own `running`, `task_outcome: pending`, output
absence, and `continue-observing-same-exact-session` evidence. General BLOCKED
answers and simple identity mentions keep their existing classification.
Comprehensive mode terminalizes this bounded signature and releases only its
workflow scope without retrying. A fresh direct run remains forbidden until a
maintenance owner invokes `settle-recursive-self-observation` with the exact
state/output/transcript SHA-256 values and confirmation token
`user-authorized-fresh-run-after-recursive-self-observation`. That command
writes one append-only receipt; it does not edit historical run artifacts or
submit a prompt.

Direct runs from the same Codex task against one project hold one cross-process mutex for the entire Oracle
process lifetime. A Multi parent owns that project mutex while authorized
children use a short parent-scoped launch mutex and isolated copied Chrome
profiles, then wait concurrently.
Control state, Oracle output, and transcripts live under
`%USERPROFILE%\.codex\state\chatgpt-oracle`, outside the DevSpace-writable
project.

Use `chatgpt_oracle_comprehensive.py` for the bounded plan → optional
Pro/Multi → review → implementation → final web gate flow. Each web stage
writes the next mission; the host validates only UTF-8, identity, paths, and
hashes. Use `chatgpt_oracle_multi.py` for independent solver sessions in waves
of at most five and one merger over handoff files.
