# Oracle Harness Native Orchestrator

@../AGENTS.md

## Layers and Change Flow

This directory is the project-local orchestration layer. Root `AGENTS.md` remains authoritative; this file narrows routing for this repository and never weakens its Oracle ownership, exact-root, no-duplicate-submission, or commit requirements.

1. Classify a request before editing: authority/state and receipt contracts (`bin/chatgpt_oracle_state.py`, profile and identity helpers), Oracle execution and recovery (`bin/chatgpt_oracle_*.py`), workflow topology (`chatgpt_oracle_comprehensive.py`, `chatgpt_oracle_multi.py`, `skills/ultra-gpt-mode/`, `skills/web-multi-gpt/`), lifecycle/install surface (`install*`, `update*`, `rollback*`, `bin/codexpro_*`), package and policy metadata, validation (`tests/`, `scripts/`), or public runbooks (`docs/`, `README*`).
2. For multi-layer changes, settle public schemas, authority boundaries, and persisted-state invariants first; then runtime execution/recovery; then lifecycle/install and package metadata; finally focused tests and user-facing documentation. Consumers must not implement against an unsettled receipt, manifest, or ownership contract.
3. A source change, installed harness copy, Oracle browser run, provider conversation, and user project mutation are distinct facts. Do not infer one from another, and do not claim deployment, submission, or acceptance without the corresponding durable evidence.
4. Before changing exported runtime helpers or persisted schema fields, identify every caller with language-aware references when available; migrate all callers in the same cutover. Do not retain compatibility aliases unless the persisted recovery contract explicitly requires one.
5. Treat runtime policy, mode documentation, skill instructions, fixtures, and focused tests as one externally observable contract when a workflow profile, receipt shape, command behavior, or safety boundary changes.

## Efficient Agent Routing

- Keep deterministic edits, narrow test repairs, inventory, and single-file documentation in the current session. Do not create a worker merely to summarize files already read.
- Use `sonic` only for mechanical, bounded changes. Use `scout` or `librarian` for read-only source or upstream API research. Assign `task` only one isolated module plus a named input/output contract.
- Parallelize only independent, disjoint ownership boundaries. For Oracle Web Multi/Ultra workflows, preserve the runtime's worktree ownership audit, all-lanes barrier, and single merger; never emulate parallel writes in a shared checkout.
- Escalate to the high-reasoning reviewer exactly once for authority/identity/recovery changes, security-sensitive browser or profile behavior, multi-stage topology changes, release blockers, or final high-risk review. Do not duplicate a completed review across models.
- Give workers paths, contract invariants, and the narrow verification command. Do not retransmit raw browser logs, credentials, durable receipts, or already-read long runbooks.

## Verification and Release

- Use the narrowest test that proves the changed persisted or runtime contract. Run broader gates only when a boundary spans their scope or release policy requires it.
- New or changed Oracle, skill, bridge, runner, workflow, profile, state, or recovery behavior must have focused verification and a descriptive Git commit before reporting completion.
- Source verification does not prove global installation, DevSpace registration, browser login, provider submission, or an external project mutation. Perform those actions only when explicitly requested and when their prerequisite evidence is present.
