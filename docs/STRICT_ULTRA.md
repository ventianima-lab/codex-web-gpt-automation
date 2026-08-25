# Ultra GPT closed workflow audit

> New workflows use `workflow_profile: ultra-gpt` and opt in with
> `closed_audit`. Never use `workflow_profile: strict-ultra` for new work; that
> name remains only for exact legacy recovery and schema compatibility.

The closed audit is an opt-in contract inside the existing `ultra-gpt`
comprehensive workflow, not a separate execution mode. It does not replace the
scheduler. The existing Oracle Multi v2 writer waves, isolated worktrees,
all-lanes barrier, audited apply, merger, and final web gate remain the
execution engine.

The option adds a closed audit boundary for workflows that need machine-
verifiable provenance. A new comprehensive manifest keeps
`workflow_profile: "ultra-gpt"` and binds one
`codex.chatgpt.strict-ultra-contract/v1` file by exact SHA-256 through the
`closed_audit` object. The contract in turn binds:

- a closed dependency manifest whose regular, non-symlink project files are
  checked against exact SHA-256 values;
- a closed authority manifest separating the local controller, ordinary web
  GPT, optional pre-workflow Pro advisory, and advisory-only Local Multi GPT;
- `allow_pro=false` and `native_subagents_disabled=true` proof for the workflow;
- an advisory-only Research Governor scorecard with project-neutral admitted,
  settled, newly measured, repair, blocked, unexecuted, diversity, and
  opportunity-cost fields;
- output locations for the hash-linked identity ledger, local-gate receipt,
  and final closed workflow audit.

All strict artifacts reject duplicate keys, non-finite numbers, unexpected
keys, paths outside the exact project, symlinks, and hash drift. The ordinary
web model still performs semantic work. A pre-workflow Pro consultation is a
separate optional advisory binding and never grants Pro authority inside the
strict workflow.

## Deterministic local gate

The strict local gate command must contain both `{artifact_path}` and
`{artifact_sha256}` placeholders. The runner substitutes the bound Research
Governor artifact and requires one JSON object on stdout:

```json
{
  "schema": "codex.chatgpt.strict-local-gate-result/v1",
  "status": "PASS",
  "opened_path": "D:\\project\\governor.json",
  "opened_sha256": "<64 lowercase hex>",
  "validator": "project-validator/v1"
}
```

Exit zero without that exact receipt is a failure. This prevents an echo or
always-success command from satisfying the strict gate without proving which
bound artifact it opened.

## Workflow audit

On completion, `codex.chatgpt.strict-ultra-workflow-audit/v1` exposes the
dependency, authority, Governor, and identity-ledger hashes; separate model
role telemetry; each strict Multi run's exact wave schedule, lane results,
barrier, audited apply, and merger; the local-gate receipt; and the final
verifier output binding. Five lanes scheduled with concurrency three are
durably represented as stable `3 + 2` waves.

The identity ledger is append-only and hash-linked. It records workflow,
stage, run, slug, conversation, recovery, attempt, and status identities when
present. Elapsed activity, aliases, or unexecuted plans cannot be counted as
new successful work merely by changing a display label.

Existing `standard`, `ultra-economy`, and `ultra-gpt` manifests and receipts
retain their current behavior when `closed_audit` is absent. Legacy manifests
that selected `workflow_profile: "strict-ultra"` remain accepted for exact
compatibility and recovery, but that name is deprecated for new workflows.
