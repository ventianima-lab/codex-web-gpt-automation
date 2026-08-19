---
name: chatgpt-pro-plan-handoff
description: Run staged work with explicitly authorized read/write DevSpace Pro and Oracle-based regular comprehensive stages; explicit Pro attachments remain available for bounded evidence.
---

# Pro and comprehensive handoff

Pro uses Oracle and is quota-limited. A standard comprehensive workflow may
select it only when its manifest has `allow_pro: true` after an explicit user
request; regular work remains at the highest supported non-Pro reasoning tier.
The qualified `GPT-5.6 Sol` Pro stage uses read/write DevSpace at the exact
project root and may perform mission-authorized edits and commands under the
repository safety policy. `pro-attachment` remains an explicit attachment-only
contract for immutable/external evidence or DevSpace-unreadable artifacts, not
an automatic fallback. CodexPro and all agbrowse creation are frozen; legacy
files remain only for exact persisted-run recovery.

New GPT comprehensive work uses
`bin/chatgpt_oracle_comprehensive.py` with schema
`codex.chatgpt.oracle-comprehensive/v1`:

```text
plan -> optional Pro or Oracle Web Multi -> review
     -> implementation -> final web gate -> one local deterministic gate
```

The optional `ultra-economy` profile is itself an explicit Pro request. It
starts with qualified Pro design, then uses separate regular web review, implementation, and final
gate sessions. On the first activation request in a Codex task, the local
commander gives one unconditional instruction to select `gpt-5.6-luna` with
`max` reasoning and waits for confirmation. It does not inspect the runtime or
repeat that question later in the same task. Follow
`skills/ultra-economy-mode/SKILL.md` for the local commander and Luna Max
subagent contract.

The optional `ultra-gpt` profile replaces every semantic native Codex
subagent role with a separate regular web GPT session. Local Codex remains a
deterministic controller only. The enforced path is plan, independent web
review and partitioning, bounded parallel isolated-worktree Web Multi lanes plus
merger, final web verification, and the local deterministic gate. Pro is not a
stage inside this profile. When the user separately requests design advice,
run at most one explicit Pro advisory before starting a fresh `ultra-gpt`
workflow. Follow `skills/ultra-gpt-mode/SKILL.md` for the full contract.

Comprehensive mode is a staged workflow, not a prompt variant. Its
implementation stage carries the same orchestrator ownership contract used by
the single-submission `orchestrator` mode in `chatgpt-thinking-browser`, so
comprehensive mode contains that mode as one stage. The difference is
structural: comprehensive mode makes several separate web submissions, each
authoring the next mission and a hash-bound receipt, and it can only complete
through a final web PASS plus a zero-exit local gate.

Use single-submission `orchestrator` when the goal and approach are settled and
one authorized pass should finish the work at the lowest cost. Use
comprehensive mode when the plan needs an independent review stage, when Pro or
Web Multi must participate, or when completion must be proven deterministically.
Do not emulate comprehensive staging by chaining `orchestrator` submissions by
hand; same-project web submissions stay serialized and the workflow engine owns
stage identity and recovery.

The manifest supplies absolute `project_root`, `workflow_dir`,
`initial_mission_path`, stable `workflow_id`, and a nonempty
`local_gate_command`. Every regular web stage writes its own next mission and a
bound `codex.chatgpt.oracle-stage-result/v1` receipt. The host validates
workflow/stage/attempt/input hashes, UTF-8 paths, output hashes, PASS status,
and the transition; it never rewrites the semantic prompt.

An explicitly authorized Pro stage runs through Oracle with read/write DevSpace.
It returns one strict identity-bound JSON envelope containing its output and
next-mission text. The host mechanically preserves those strings as UTF-8 files
and computes the standard receipt; it does not summarize or rewrite them.

When a plan explicitly selects `pro-attachment`, its next mission declares one
closed `[PRO_ATTACHMENT_CONTRACT]` block. The JSON body uses schema
`codex.chatgpt.oracle-pro-attachments/v1` and an `attachments` array of
absolute project-root-contained regular non-symlink paths with optional
SHA-256 values. The host attaches only the mission and these declared files; it
never discovers ZIPs from prose. An authorized Pro mission without the block
uses the read/write DevSpace route. Regular non-Pro DevSpace stages reject this block.

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
complete. An explicitly authorized Pro selection launches the qualified read/write DevSpace stage unless
its plan explicitly declares `pro-attachment`, then waits for a bound receipt;
it is never downgraded. Missing receipt/output,
crash, or ambiguity returns attention-required without a replacement submit.
Regular-stage `--browser-timeout` is a browser observation window, not a work
termination deadline. At 4,800 seconds comprehensive mode performs a caution
audit of the persisted exact attempt and keeps the same process/session alive.
If an observer returns while the exact session remains live, comprehensive mode
continues exact-slug live recovery automatically. Time alone never kills,
fails, releases, replaces, restarts, or resubmits the session.

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

Existing v1-v4 agbrowse comprehensive state and v3 parallel implementation are
legacy recovery-only. Their files remain installed for exact recovery but are
not the new-work route.
