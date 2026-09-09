---
name: chatgpt-pro-plan-handoff
description: Route standing non-implementation cognition through Oracle @codex Latest then 6 Pro, hand implementation to native Sol, and preserve explicit comprehensive and exact-recovery compatibility workflows.
---

# Pro and comprehensive handoff

## Standing default

Every non-implementation cognitive requirement uses Oracle with the manually
registered `@codex` app. Browser proof requires exact `Latest` checked, full
`5/5` qualification, the composer's `Thinking effort` control, and a `6 Pro`
signal in either the model menu or composer. The user has granted standing authorization, so this default requires
neither per-run Pro opt-in nor an `allow_pro` decision. Requirements analysis,
design, architecture, planning, research and source synthesis, comparison,
advice, critique, review, adjudication, and semantic verification all use this
route. Never fall back to GPT-5.6 or GPT-5.5 when Latest/6 Pro cannot be proven,
and never route through CGW or `chatgpt-web/*`.

Latest/6 Pro is non-implementation and read-only at the exact approved
DevSpace root. Native `gpt-5.6-sol` owns implementation, repair, file changes,
and commands; native Luna owns chores and focused testing. Astra owns
orchestration, exact Oracle recovery, deterministic receipt/hash/process
verification, integration, release, and final reporting. A normal handoff is:

```text
Oracle Latest/6 Pro cognitive mission -> native Sol implementation
-> Luna/local focused tests -> Oracle Latest/6 Pro semantic verification
-> Astra deterministic/release gate
```

The Oracle CLI manifest uses only the validated carrier fields
`model: gpt-5.6-sol`, `model_strategy: current`, and `thinking_time: pro`.
`current` is the compatibility carrier for the browser's explicit Latest then
6 Pro selection; do not pass invented `gpt-6` or `latest` CLI model slugs or
claim a new Oracle engine.

## Explicit compatibility workflows

The existing comprehensive runner uses
`bin/chatgpt_oracle_comprehensive.py` with schema
`codex.chatgpt.oracle-comprehensive/v1`:

```text
plan -> optional Pro or Oracle Web Multi -> review
     -> implementation -> final web gate -> one local deterministic gate
```

This is an explicit compatibility workflow, not the standing default. Its
schema, stage names, receipt contracts, and persisted authorities remain exact
for explicitly selected workflows and recovery. Do not rewrite a recovered
stage's implementation authority to match the standing split. Within this
compatibility schema, `allow_pro: true` and selector-era profiles retain their
existing validation meaning; they are not per-run approval gates for the
standing Latest/6 Pro cognitive route.

The optional `ultra-economy` profile is an explicit compatibility workflow,
not the standing routing default. It starts with its recorded qualified-Pro
design stage, then uses separate regular web review, implementation, and final
gate sessions. On the first activation request in a Codex task, the local
commander gives one unconditional instruction to select `gpt-5.6-luna` with
`max` reasoning and waits for confirmation. It does not inspect the runtime or
repeat that question later in the same task. Follow
`skills/ultra-economy-mode/SKILL.md` for the local commander and Luna Max
subagent contract.

The optional `ultra-gpt` compatibility profile replaces every semantic native Codex
subagent role with a separate regular web GPT session. Local Codex remains a
deterministic controller only. The enforced path is plan, independent web
review and partitioning, bounded parallel isolated-worktree Web Multi lanes plus
merger, final web verification, and the local deterministic gate. Pro is not a
stage inside this profile. When the user separately requests design advice,
run at most one explicit Pro advisory under that profile's recorded contract
before starting a fresh `ultra-gpt` workflow. Follow
`skills/ultra-gpt-mode/SKILL.md` for the full compatibility contract. Do not
infer either profile from ordinary cognitive or implementation work.

Comprehensive mode is a staged workflow, not a prompt variant. Its
implementation stage carries the same orchestrator ownership contract used by
the single-submission `orchestrator` mode in `chatgpt-thinking-browser`, so
comprehensive mode contains that mode as one stage. The difference is
structural: comprehensive mode makes several separate web submissions, each
authoring the next mission and a hash-bound receipt, and it can only complete
through a final web PASS plus a zero-exit local gate.

When an explicit compatibility request selects single-submission
`orchestrator`, use it for one authorized pass under its existing contract.
When an explicit compatibility request selects comprehensive mode, use the
workflow engine rather than emulating its stages by chaining `orchestrator`
submissions by hand. Same-task submissions against one project stay serialized
while foreign task owners remain isolated, and the workflow engine owns stage
identity and recovery.

The manifest supplies absolute `project_root`, `workflow_dir`,
`initial_mission_path`, stable `workflow_id`, and a nonempty
`local_gate_command`. Every regular web stage writes its own next mission and a
bound `codex.chatgpt.oracle-stage-result/v1` receipt. The host validates
workflow/stage/attempt/input hashes, UTF-8 paths, output hashes, PASS status,
and the transition; it never rewrites the semantic prompt.

Within a selected comprehensive compatibility workflow, a Pro stage runs
through Oracle with its recorded read-only DevSpace authority. It returns one
strict identity-bound JSON envelope containing its output and next-mission
text. The host mechanically preserves those strings as UTF-8 files and
computes the standard receipt; it does not summarize, rewrite, or widen their
authority. Outside exact compatibility execution or recovery, native Sol owns
all resulting file mutations and commands.

When an explicit compatibility plan selects `pro-attachment`, its next mission declares one
closed `[PRO_ATTACHMENT_CONTRACT]` block for immutable/external evidence. The
host attaches only the declared project-contained regular non-symlink files
and validates optional SHA-256 values; it never discovers attachments from
prose. The attachment stage remains read-only. Preserve the recorded next-stage
authority inside that compatibility workflow; for standing-default work,
native Sol performs every required mutation or command.

Plan receipts should use `PLAN_READY`. For compatibility, `completed` is
accepted only when the plan receipt is otherwise a fully ready, blocker-free,
hash-valid transition to `review`, `web-multi`, or `pro`; ambiguous or incomplete
receipts remain fail-closed and are never rewritten on disk.

Pro must JSON-escape every quote and backslash inside `output_text` and
`next_mission_text`. The host always parses strict JSON first. If strict parsing
fails, it may make one narrow recovery attempt only for the canonical ordered
envelope whose text fields contain unescaped quotes. Recovery still requires the
exact workflow, stage, attempt, and input-mission identities plus a complete
unambiguous tail. Invalid escapes, truncation, duplicate/ambiguous boundaries,
or identity drift remain fail-closed. A recovered receipt records the immutable
source output SHA-256, recovery method, and strict parser error position.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_comprehensive.py" --manifest C:\project\workflow.json --dry-run
```

The review GPT owns plan repair and finalization. It does not merely list
findings: it directly repairs every defect resolvable from the mission,
DevSpace workspace, project rules, or available evidence, writes the corrected
final plan, and authors the complete implementation mission. `PASS` and
`PASS_WITH_NOTES` proceed directly to implementation; notes are carried inside
that mission. New work must not emit `REVISE`. A legacy `REVISE` receipt is
accepted only for compatibility and ends in attention-required without creating
another plan. `FAIL` is reserved for a concrete unavailable external input or
authority, unresolved safety boundary, or genuine execution impossibility.

Every regular stage binds an exact project root and exact input mission path.
DevSpace may reuse or open only that normalized root, with at most one retry of
the same root after inspecting registered workspaces. Parent, child, similarly
named, active-workspace, and shell-boundary fallbacks are forbidden. The stage
reads its mission and applicable `AGENTS.md` chain completely before project
exploration or edits.

Transport or runner recovery keeps the same workflow and stage identity. It
must never create a `workflow-retryN` replacement. The revision budget and
remaining critical finding set are persisted in the workflow state for
operator visibility. Only final web PASS plus a zero-exit local gate can
complete. A Pro selection recorded by the compatibility manifest launches its
qualified read-only DevSpace stage and waits for a bound receipt; its next
stage keeps the authority recorded by that exact workflow. Missing receipt/output,
crash, or ambiguity returns attention-required without a replacement submit.
Regular-stage `--browser-timeout` is a browser observation window, not a work
termination deadline. At 4,800 seconds comprehensive mode performs a caution
audit of the persisted exact attempt and keeps the same process/session alive.
If an observer returns while the exact session remains live, comprehensive mode
continues exact-slug live recovery automatically. Time alone never kills,
fails, releases, replaces, restarts, or resubmits the session.

If a completed, terminal-harvested regular final gate wrote the exact malformed
transition `status=PASS`, empty `next_stage`, `ready_for_next=false`, empty
`blocker`, and an empty terminal mission, leave that receipt and Oracle run
unchanged. After explicit user authorization, the owning maintenance task may
prepare one fresh final attestation in the same workflow with
`--retry-final-receipt`. Bind the current workflow state, scope state, Oracle
run state, and malformed receipt by exact SHA-256, use confirmation token
`user-authorized-final-receipt-retry`, and run `--dry-run` first. The command
accepts only the standard profile, the exact task owner, a stopped observer,
valid ownership/browser/provider evidence, unchanged mission and output bytes,
and remaining stage budget. It writes an immutable authority receipt and
prepares only `final-web-gate`; it never repairs the old verdict, replays
implementation, or launches a prompt itself. Resume the same manifest normally
after preparation. The fresh final receipt must use `next_stage=complete` and
`ready_for_next=true`, after which the existing deterministic local gate still
decides completion.

```text
python ~/.codex/bin/chatgpt_oracle_comprehensive.py \
  --manifest <same-workflow.json> --retry-final-receipt --dry-run \
  --expected-workflow-sha256 <sha256> --expected-scope-sha256 <sha256> \
  --expected-run-state-sha256 <sha256> --expected-receipt-sha256 <sha256> \
  --confirmation user-authorized-final-receipt-retry
```

`Prompt did not appear in conversation before timeout (send may have failed)`
remains submission-uncertain by default. Exact recovery reporting no live tab
and no saved conversation URL is still not enough to release ownership. Only
after the user explicitly confirms that the exact attempt was not submitted may
the maintenance owner run:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_run.py" settle-no-submission --run-dir <exact-run-dir> --confirmation user-confirmed-no-submission --reason <concise-user-confirmation>
```

The command never launches Oracle. It requires hash-valid prompt-timeout and
recovery evidence, writes a workflow/stage/attempt/input-bound settlement, and
lets comprehensive mode consume at most one replacement for that immutable
binding. Missing or changed evidence restores fail-closed project ownership;
a replacement failure never authorizes a second submission.

If the user explicitly clicks the provider UI stop control and then explicitly
abandons the whole comprehensive workflow, do not use the legacy agbrowse stop
path and do not edit Oracle or workflow JSON by hand. The maintenance owner may
use the installed comprehensive runner's `--cancel-user-stopped` command only
after binding the exact workflow state, scope state, and terminal-harvested
Oracle run `state.json` by their current SHA-256 values. The exact confirmation
token is `user-confirmed-provider-stop`. Dry-run first. The command launches no
prompt or recovery, preserves the Oracle run state byte-for-byte, writes a
durable authority and completion receipt, marks the workflow `CANCELED`, and
releases only its exact comprehensive scope. Missing, live, executed, foreign,
or changed evidence fails closed.

For the bounded pre-submit error `version resolution failed: DevSpace large
single-line read bridge check timed out`, the same command accepts only the
explicit token `user-confirmed-pre-submit-workflow-cancel`. It additionally
requires empty stdout, no output, no conversation URL, and exact pre-submit
state before it can write `CANCELED` and release the scope. Never use this token
for another error or a possibly submitted run.
The exact `DEVSPACE_SERVICE_RESTART_REQUIRED` pre-submit error uses the same
token only after the managed setup procedure has restarted DevSpace once and
verified the preserved roots and endpoints. Settlement itself never restarts
the service.

Existing v1-v4 agbrowse comprehensive state and v3 parallel implementation are
legacy recovery-only. Their files remain installed for exact recovery but are
not the new-work route.
