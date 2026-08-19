---
name: chatgpt-oracle-runtime
description: "Current Oracle runtime path for new ChatGPT work: regular modes use highest-tier non-Pro DevSpace, explicitly requested qualified Pro uses read/write DevSpace, and explicit Pro attachments remain for bounded evidence."
---

# ChatGPT Oracle Runtime

This is the only active browser path for all new GPT work. CodexPro and
agbrowse are frozen for exact legacy recovery only. Regular modes use DevSpace;
explicitly requested qualified Pro uses the same app with mission-scoped
read/write authority, while `pro-attachment` uses Oracle
attachment transport for its explicit evidence boundary.

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
`pro` mode selects `GPT-5.6 Sol` at the Pro effort. It uses DevSpace at the same
exact root and may perform mission-authorized writes and commands under the
repository safety policy. Explicit
`pro-attachment` sends one short instruction plus exact attachment files.
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
- `task_kind: pro`; qualified Pro uses the same configured app name (default
  `codex`), while explicit `pro-attachment` includes one or more exact
  `attachments`.
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
Use this wrapper preview only. Do not substitute Oracle's own browser `--dry-run`, because Oracle 0.17.1 may still enter browser preflight.

Execute only after an explicit live-run request:

```powershell
python skills/chatgpt-oracle-runtime/scripts/run_chatgpt_oracle.py run --manifest C:\absolute\oracle-job.json
```

Complete requires Oracle exit code zero, a nonempty `--write-output` artifact,
and—when `task_outcome_contract` is `v1`—a final
`TASK_OUTCOME: EXECUTED` marker. `TASK_OUTCOME: NOT_EXECUTED` and
`TASK_OUTCOME: BLOCKED` preserve terminal transport evidence but return
attention-required; transport success alone never claims project execution.
Prompts require citations and Markdown reference definitions before the marker.
For provider-rendered compatibility, only one exact marker followed solely by
single-line HTTP(S) Markdown reference definitions is also classifiable; any
ordinary trailing prose or conflicting marker remains `unknown`.
A nonzero Oracle exit after launch, including a browser response timeout, is
`attention_required` rather than proof that the web session failed. It retains
same-project ownership and permits only exact-slug `live` or `harvest`
recovery; it never authorizes a replacement submission.
`--browser-timeout` is a browser observation window, not proof that the web run
ended. The default is aligned with the observed provider boundary. Separately,
4,800 seconds is only a caution/status-audit threshold: the runner records the
exact slug, process liveness, artifact progress, known conversation binding,
and terminal evidence, then keeps waiting on the same process. It never kills,
fails, releases, or replaces a run because that threshold elapsed.

## Recovery

Recovery always reuses the stored Oracle slug and never restarts or submits:

```powershell
python skills/chatgpt-oracle-runtime/scripts/run_chatgpt_oracle.py recover --run-dir C:\absolute\run --action harvest
```

Use `--action live` only to keep following the same stored session. A successful recovery must write a nonempty stored `output.md`, update `state.json` to `complete`, and refresh `transcript.md`; exit code zero without output is `attention_required`.
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
provider-delivery-timeout states keep the same authority and project lock.
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
maintenance owner may release that exact run only after explicit user
confirmation through `chatgpt_oracle_run.py settle-no-submission` with the
exact run directory, `--confirmation user-confirmed-no-submission`, and a
concise reason. The settlement is hash-bound to the comprehensive stage,
direct Web Multi child, or standalone qualified-Pro identity and immutable
mission evidence and does not launch Oracle. Comprehensive mode may consume
only one replacement for its binding; standalone qualified Pro permits only
the separately authorized single fresh retry with identical mission bytes.
For `pro-attachment-only`, the supported Oracle 0.17.1 attachment-upload
timeout additionally requires an exact immutable attachment manifest (path,
size, and SHA-256 for every file), the upload-timeout marker, matching
stdout/transcript, no stderr, and exact no-live-tab/no-saved-URL recovery hashes.
It remains ineligible without the same explicit user token or if any artifact
has changed.

Direct same-project runs hold one cross-process mutex for the entire Oracle
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
