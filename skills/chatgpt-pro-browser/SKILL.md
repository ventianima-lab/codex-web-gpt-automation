---
name: chatgpt-pro-browser
description: Use the standing Oracle @codex Latest/6 Pro route for non-implementation cognition, with bounded explicit follow-up rounds and exact compatibility recovery.
---

# ChatGPT Pro through Oracle

## Standalone scope

This is the standalone read-only Pro conversation route. It may produce a
design, advice, research finding, review, or decision. After each durable
answer it returns control to Codex and stops; only an explicit user request may
add another bounded round to that same conversation. It never starts a
review-to-implementation chain, authors a
follow-on implementation stage, or invokes `chatgpt-pro-plan-handoff` on its
own. If the user asks for comprehensive mode, use `chatgpt-pro-plan-handoff`
instead.

Oracle is the only backend for a new Pro run. There is no new agbrowse,
CodexPro, in-app Browser, custom CDP/Playwright, or `@chrome` fallback.

## Standing default route

All non-implementation cognitive requirements use this route under standing
approval; do not request per-run Pro opt-in. Oracle uses the manually registered
`@codex` DevSpace app and the validated CLI carrier `model: gpt-5.6-sol`,
`model_strategy: current`, and `thinking_time: pro`. Browser proof requires
exact `Latest` checked, full `5/5` qualification, the composer's
`Thinking effort` control, and a `6 Pro` signal in either the model menu or
composer. Never fall back to GPT-5.6/5.5 or invent a `gpt-6`/`latest` CLI slug.
The mission must bind one
exact absolute project root. After one-time qualification, do not inspect,
register, repair, select, or otherwise verify ChatGPT app/settings state on
each run.

Before the first qualified Pro submission for a new project, the local runner
must verify that the normalized exact root is present in DevSpace
`allowedRoots`. A parent, child, or similarly named root is not sufficient.
The result is cached by exact config hash, so later questions in the same
project do not repeat endpoint/read probes; a changed config is revalidated.
Failure returns `DEVSPACE_EXACT_ROOT_UNAVAILABLE` before Oracle or a browser is
created and points to the complete root-preserving setup preview.

Pro reads the mission and applicable `AGENTS.md` chain completely. Within the
exact root it is read-only and limited to design, advice, or review: it must not
create, edit, or remove files or run commands. Native `gpt-5.6-sol` owns any
required implementation, mutation, or command; native Luna owns chores and
focused tests, and Astra owns orchestration and deterministic release. Repository
safety rules remain authoritative. Pro must not change accounts, app settings,
or external state. It may
not substitute a parent, child, similarly named, active, or shell-boundary
workspace, and may retry only the same root once after a timeout.

## Explicit attachment evidence route

`pro-attachment` remains an explicit, read-only route for immutable/external
evidence or artifacts that DevSpace cannot read. It is never an automatic
fallback from a DevSpace failure. Build only the declared packet, bind every
attachment path and SHA-256, and never infer attachments from prose.

## Explicit compatibility Web Multi decision

Only an explicitly selected selector-era/Web Multi compatibility workflow uses
this exact decision block; it is not part of the standing default:

```text
WEB_MULTI_NEEDED: YES|NO
WEB_MULTI_REASON: evidence-based reason tied to the decision and alternatives
```

Pro chooses `YES` only when three to five materially independent regular GPT
sessions are likely to add decision-relevant alternatives or evidence. Their
mission carries the same project maximum-context evidence and the durable Pro answer,
assigns stable lane order, and synthesis/judge criteria. After a durable Pro
answer says `WEB_MULTI_NEEDED: YES`, Codex starts that ready-to-run Web Multi-GPT Very
High mission automatically without a routine user
choice. It waits for the exact Pro session to be terminal first and preserves
the same-task project serialization contract. A different Codex task owns a separate run namespace and may proceed concurrently. Choose `NO` for a trivial, single-answer, or purely mechanical question. This optional advisory handoff
does not turn the standalone Pro result into a review-to-implementation chain.

## Preflight and completion

1. Resolve and hash-validate the tested Oracle compatibility contract.
2. Bind the same task-scoped normalized-project mutex used by regular Oracle work.
3. Build a short UTF-8 mission that states the exact root, question, read-only
   cognitive authority, and any evidence limitations. Route any required file
   mutation or command to native Sol.
4. Use a fresh Oracle slug and require Oracle model and transport evidence
   before accepting a send.

The public dispatcher entry points are:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" --mode pro --project-root <ROOT> --mission-path <MISSION> --manifest-output <MANIFEST> --dry-run
```

Remove `--dry-run` only after the manifest, project mutex, Oracle version, and
compatibility hashes pass preflight. New `pro` work uses read-only DevSpace;
attachment work uses only the separate explicit evidence contract.

## Same-conversation follow-up

When the user explicitly wants continued discussion, do not create a new Pro
conversation and do not loosen the raw Oracle argument allowlist. The exact
parent must be owned by the current Codex task, terminal `EXECUTED`, bound to
`pro-devspace-readonly`, and retain valid ownership/browser receipts plus the
canonical conversation URL. Put the next read-only question in a UTF-8 mission
inside the same project, then preview the internal lifecycle:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_run.py" followup --parent-run-dir <TERMINAL_PARENT_RUN_DIR> --mission-path <FOLLOWUP_MISSION> --round-key <UNIQUE_ROUND_KEY> --dry-run
```

Remove only `--dry-run` after the preview and explicit send authority. The
child gets a new Oracle run/slug and dynamic CDP port but must reopen the exact
same ChatGPT conversation. Append-only reservation and result receipts bind
the round mission, child state, output/transcript hashes, task owner, and
conversation identity. Foreign/legacy ownership, a duplicate key, attachment
or writable transport, artifact tamper, missing receipt, or a changed/unproven
conversation fails closed. Never inject raw `--followup`,
`--browser-follow-up`, or `session`; recovery observes only and cannot send a
round; uncertainty never authorizes a replacement prompt.
New read-only Pro parents normalize default `archive=auto` to `never`; no user
archive-setting action is required. Explicit `archive=always` is a single-turn
choice. Only historical or explicitly archived parents use the bounded exact
restore-and-rearchive compatibility path.
If restoration fails before the composer, do not harvest. Preserve the exact
child, obtain explicit user no-submission confirmation, and use the runner's
exact `settle-no-submission` path. An older exact no-live/no-URL/no-candidate
harvest pair may be revalidated, but must never be deleted or rewritten.

Completion requires a structured observed-picker receipt proving exact
`Latest` checked, full `5/5` qualification, the composer's `Thinking effort`
control, and a `6 Pro` signal in either the model menu or composer; requested
values or a log line alone are insufficient. It also requires exit zero, fresh
nonempty host-only `output.md`, immutable run identity, and a refreshed
transcript. The final nonempty line must also be
`TASK_OUTCOME: EXECUTED|NOT_EXECUTED|BLOCKED`; every citation, footnote, and
Markdown reference definition belongs before it. For bounded compatibility
with provider-rendered answers, the classifier also accepts exactly one marker
followed solely by single-line HTTP(S) Markdown reference definitions. Ordinary
trailing prose or another marker remains `unknown`. A terminal answer that reports
zero callable DevSpace tools or says the mission/root could not be read is
`NOT_EXECUTED`, never successful Pro work. When that exact terminal run is
durably captured, it releases the current task's project ownership and permits at most one
fresh retry with the same mission bytes and SHA-256. If the retry has the same
tool-exposure failure, stop with `attention_required`; do not loop, manipulate
ChatGPT app settings, or switch to attachments automatically. A nonzero exit
after submission is `attention_required`, not proof that the web session
failed.

## Recovery

Recover only the stored exact Oracle run directory and slug. `live` and
`harvest` may observe or collect that same session; they never restart,
resubmit, change route/model/effort, or create a replacement conversation.

When the exact recorded Oracle runtime reports the prompt-not-observed timeout, first run
exact-slug harvest. No live tab plus no recoverable conversation URL remains
submission-uncertain and needs explicit user confirmation before the
hash-bound `settle-no-submission` path can release a standalone qualified-Pro
run. Only then may an explicitly authorized single retry reuse the identical
mission bytes; no output, URL, mismatched hash, conflicting recovery state, or
ordinary trailing browser error may be treated as proof.

If the task-bound failure occurred before a conversation-bound browser receipt
could be sealed, preview the same exact slug with `recover --action harvest
--dry-run`. The preview may use `bounded-prompt-timeout-harvest` only when the
ownership receipt, qualified-Pro profile, the exact recorded Oracle version's zero-turn commit probe,
root composer, dynamic CDP port, isolated profile/target, and absent output/URL
all match. Remove only `--dry-run` for that one prompt-free harvest. `live`, a
foreign task, or any contradictory identity stays blocked. The harvest still
does not unlock the project; the explicit user confirmation command remains
mandatory. A later project-mission edit is acceptable only when the immutable
run copy and task ownership receipt bind the same original mission SHA-256;
legacy-unbound runs do not receive this compatibility path.

The same user-confirmed, fail-closed settlement is available to a
`pro-attachment` run only when the exact recorded Oracle runtime reports the attachment-upload
timeout before prompt submission. It binds every attachment path, size, and
SHA-256; the source and transport mission copies; Oracle locator/version; exact
stdout/transcript and recovery bytes; and the absence of output and a
conversation URL. The user confirmation token is still mandatory. Any changed
attachment, live recovery state, URL, output, or unrecognized error keeps the
current task's project lock. This recovery rule never authorizes an automatic replacement attachment run.

For an already persisted agbrowse Pro run only, former recovery commands remain
available. They must never create a new run.
