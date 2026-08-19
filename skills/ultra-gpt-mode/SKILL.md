---
name: ultra-gpt-mode
description: Run 울트라 GPT 모드 by replacing cognitive Codex subagents with separate Oracle web GPT sessions while the local Codex remains a deterministic controller. Use when the user says 울트라 GPT 모드, Ultra GPT Mode, or asks to reproduce Codex Ultra-style delegation with web GPT workers.
---

# Ultra GPT Mode

Use the Oracle comprehensive engine with the `ultra-gpt` profile. This mode
copies the useful topology of Codex Multi-agent—bounded independent contexts,
explicit roles, concurrency limits, and a root synthesis step—without using
native Codex subagents for semantic work.

## Authority and activation

- The user's request for Ultra GPT Mode authorizes regular web GPT stages, not
  Pro. Regular stages use the highest supported non-Pro reasoning tier.
- Pro remains quota-limited and explicit-only. When the user separately
  authorizes Pro and architecture uncertainty genuinely warrants it, run at
  most one design-only Pro advisory before the workflow. Freeze its durable
  output into the initial mission; never let the `ultra-gpt` workflow select
  Pro internally.
- Do not change the user's local model, global subagent configuration, ChatGPT
  settings, or registered app.

## Local controller contract

- Do not spawn native Codex subagents in this mode. The local commander owns
  only authorization, compact mission and manifest bytes, exact-root
  qualification, locks, hashes, session monitoring, deterministic commands,
  Git/CI/release operations, and final reporting.
- Do not perform substantive planning, implementation, review, debugging, or
  synthesis locally. Route a semantic residual to a fresh bounded web stage.
- Deterministic validation may reject a bad receipt, hash, diff, test exit, or
  ownership boundary. It must not rewrite the web stage's semantic output.

## Web agent graph

```text
one-time exact-root qualification
  -> regular web root planner
  -> separate regular web review and work partition
  -> Oracle Web Multi: 2..5 parallel worktree-write web implementers, concurrency <= 3
  -> all-lanes barrier and host audit
  -> web merger inspects the combined canonical result
  -> separate regular web final verification
  -> one local deterministic gate
```

The original Codex Multi-agent runtime can write concurrently in one shared
filesystem. Web sessions do not provide a trustworthy shared-process ownership
boundary, so Ultra GPT preserves parallelism with a distinct pre-created Git
worktree per writer. Each lane declares nonempty project-relative
`owned_paths`; ownership is pairwise disjoint, including ancestor/descendant
overlap. The host injects that immutable scope, audits the actual delta and Git
metadata, and applies nothing until every lane is durably terminal and valid.
One failed or out-of-scope lane blocks the merger; partial merge is forbidden.
Derived worktrees live below `<output_dir>/worktrees`. They inherit the
canonical root's existing DevSpace qualification through a hash-validated v2
parent/lane binding; the mode does not mutate `allowedRoots` or restart the
service for temporary worktrees.

## Manifest

Use `bin/chatgpt_oracle_comprehensive.py` with:

```json
{
  "schema": "codex.chatgpt.oracle-comprehensive/v1",
  "workflow_profile": "ultra-gpt",
  "initial_stage": "plan",
  "allow_pro": false,
  "max_stages": 8
}
```

Add the normal absolute workflow ID, project root, workflow directory, initial
mission, app, model, and local gate fields. Always dry-run before the first
submission. The runtime fails closed unless plan transitions to review, review
transitions to a bound `codex.chatgpt.oracle-multi/v2` manifest with two to five
`worktree-write` lanes and concurrency at most three, and every lane has
disjoint project-relative `owned_paths`. All lane roots must be pre-created
worktrees of the same repository at the same HEAD and must be descendants of
`<output_dir>/worktrees`. The merger must transition to the separate final web
gate.

## Recovery and completion

- Recover only the exact persisted workflow, stage, run, and Oracle slug.
  Never replace an ambiguous or possibly submitted web session.
- The 80-minute mark is a caution audit, not a stop condition.
- Do not overlap local native-subagent work with the web workflow.
- Completion requires the final web PASS receipt and the configured local
  deterministic gate with exit code zero.
