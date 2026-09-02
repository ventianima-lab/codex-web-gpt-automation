<!-- BEGIN CODEX WEB GPT SUBAGENT POLICY -->
## Codex native subagent policy

- The primary commander uses GPT-5.6 Sol at high reasoning. Default subagents use GPT-5.6 Terra at medium reasoning; role files may narrow this further.
- Whenever GPT-5.6 Luna is selected for the primary commander or any native subagent, its reasoning effort must be `max`. Never start, spawn, or continue a Luna agent below `max`; if Luna Max cannot be explicitly selected or verified, fail closed and do not use Luna.
- Every spawned native subagent task name reports its effective runtime as `<model>_<reasoning>_<task>`, normalized to lowercase letters, digits, and underscores. Never label a task `luna_max` unless it actually runs GPT-5.6 Luna at `max`.
- Use subagents actively when the user, applicable repository rules, or a selected skill asks for delegation and the work is independently bounded.
- Do not blanket-fan-out. Start with no more than two concurrent workers in normal operation; the global hard cap is three spawned threads.
- Prefer `scout` for narrow repetitive read-only discovery only when its effective runtime satisfies the Luna Max rule. If a role preset fixes Luna below `max`, use a `default` agent explicitly configured as GPT-5.6 Luna with `max` reasoning and a non-full-history fork instead. Prefer `implementer` only when the parent supplies an explicit non-overlapping file list, and `verifier` for independent read-only validation.
- Never assign overlapping write ownership. The primary agent integrates results and remains responsible for final deterministic verification.
- Keep `multi_agent_v2` disabled while it is unstable; the supported `[agents]` settings and standalone role files are sufficient.

## Filesystem hygiene

- Never create test output, temporary directories, logs, downloaded archives, or dependency checkouts directly under a drive root such as `C:\` or `D:\`.
- Use the operating-system temp directory under a task-specific `Codex` child first. If Windows path length requires a shorter location, use the active repository's gitignored `.codex-tmp\<task>` directory, never `D:\pytest-*` or another drive-root scratch path.
- Put reusable third-party source checkouts under `%LOCALAPPDATA%\Codex\Sources`. Keep explicit user project roots separate and never repurpose them as scratch space.
- Before cleanup, verify ownership and active references. Preserve user projects, system folders, credentials, and ambiguous items; move confirmed automation artifacts to a recoverable archive instead of deleting them.

## Oracle long-run observation

- Treat 80 minutes as a caution/status-audit threshold, never as a forced stop, failure, handoff, ownership release, or replacement-submission deadline.
- At the threshold inspect the exact run's process liveness, response/log/output progress, known conversation binding, and provider terminal evidence. If it is live, streaming, progressing, or uncertain, continue the same process or exact-slug live recovery.
- If a host observer must return, preserve the Oracle process/session and automatically continue observation through the same exact slug. Never create a fresh prompt or release the task-scoped project lock because elapsed time alone.
- Only a real provider hard limit, explicit terminal evidence, an explicit user stop, or verified inability may end observation. Keep prompt-not-observed fail-closed and no-duplicate rules unchanged.

## Oracle task ownership

- Ownership is bound to the originating Codex task plus the exact run, not to the project root alone. Different tasks may run concurrently at the same root with separate task-scoped mutexes, slugs, dynamic CDP ports, browser profiles, conversations, and receipts. Only the same task's unresolved run blocks its next submission.
- A foreign task may be listed for diagnosis but must never be adopted, recovered, harvested, followed up, canceled, or stopped. Never infer a legacy-unbound owner from the project root, newest run, Chrome window, or timestamp.
- An explicit user request to release an identified legacy-unbound Oracle lock authorizes only the bounded local retirement in `mcp-update-guard`'s "Explicit legacy lock retirement" procedure. Use the authoritative checkout's `scripts/retire_legacy_oracle_lock.py` with its stopped-process, exact CDP-failure, identity/hash, no-output/no-conversation, and prior-harvest evidence gates. Do not repeat explicit approval, infer historical ownership, or resubmit. Preserve the run and receipts in the retirement archive and verify removal from the task-unfiltered active-lock list; bound foreign runs and live or uncertain processes remain protected.
- A same-conversation read-only Pro round may use only the runner's internal `followup` command against a task-bound terminal `pro-devspace-readonly` parent. Raw `--followup`, `--browser-follow-up`, and `session` injection stay forbidden. New read-only Pro parents normalize default `archive=auto` to `never`; explicit `always` is a single-turn choice. A historical or explicitly archived exact parent may be restored only through the bounded compatibility path for that round and must be re-archived afterward. If restoration fails before the composer, do not harvest; preserve the run and require explicit user no-submission confirmation before exact settlement. Each round must prove the unchanged conversation, archive transition, and append hash-bound reservation/result receipts; an unproven or changed conversation fails closed without a replacement prompt.
- Follow-up dry-run is side-effect free. A live attempt creates child state/logs before local preflight and appends parent launch/result evidence. Historical reservation-only round keys stay immutable and are never deleted or replayed; after proving the detached controller ended, the owner uses a new unique key.

## Web GPT model and Pro authority

- Default ordinary web work to `gpt-5.6` with `extra-high`, the highest supported non-Pro reasoning tier. Never select or upgrade to Pro automatically.
- Treat Pro as quota-limited and explicit-only. Use `GPT-5.6 Sol` at the Pro effort only after the user explicitly requests Pro; a standard comprehensive workflow additionally requires `allow_pro: true`.
- Every new explicit Pro run uses the `pro-devspace-readonly` route as read-only DevSpace for design, advice, or review. It must not create, edit, or remove files or run commands; a regular `GPT-5.6` `extra-high` DevSpace stage owns those actions under the applicable `AGENTS.md` and repository safety rules.
- Pro must not alter accounts, ChatGPT app settings, or external state. Explicit `pro-attachment` remains a separate read-only immutable/external-evidence route and is never an automatic fallback.
- Preserve persisted legacy `pro-devspace` write and `pro-devspace-readonly` runs with their original authority and transport during exact recovery; never reinterpret historical authority.

## Ultra GPT mode

- When the user explicitly requests `울트라 GPT 모드` or `Ultra GPT Mode`, use the installed `ultra-gpt-mode` skill and the `ultra-gpt` comprehensive profile.
- Closed workflow provenance is an explicit `closed_audit` option of `ultra-gpt`, not a separate mode. Never enable it silently. Accept `workflow_profile: strict-ultra` only for exact legacy compatibility and recovery.
- In that profile, do not spawn native Codex subagents for semantic work. Use separate web GPT sessions for planning, review and partitioning, parallel implementation lanes in distinct pre-created Git worktrees with host-audited disjoint project-relative ownership, an all-lanes barrier, merging, and final verification. Local Codex remains a deterministic controller and runs only the final local gate.
- Pro is not an internal Ultra GPT stage. A user-requested Pro design advisory is a separate, single pre-workflow consultation; ordinary Ultra GPT stages remain on the highest supported non-Pro tier.
<!-- END CODEX WEB GPT SUBAGENT POLICY -->
