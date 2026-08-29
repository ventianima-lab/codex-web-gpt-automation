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

## CGW web model routing

- OpenCodex is the single Codex routing entrypoint. Codex Web GPT (CGW) is an external provider behind OpenCodex; keep the CGW direct bridge inactive and never let CGW overwrite the OpenCodex base URL or unrelated provider routes.
- Ordinary requested web-model work uses `cgw/chatgpt-web-extra-high`. The phrases "ask GPT Pro", "ask Pro", and equivalent explicit requests mean a `cgw/chatgpt-web-pro` subagent with `max` reasoning. Never reinterpret them as an Oracle or DevSpace run.
- CGW is semantic assistance, not execution authority. Local Codex owns files, commands, tests, device actions, accounts, and deterministic verification. A CGW answer never proves a local mutation or user-visible outcome by itself.
- If the requested CGW model is unavailable, fails before returning a durable answer, or cannot be selected exactly, fail closed or continue with authorized local work. Do not silently downgrade, resend an uncertain prompt, or fall back to Oracle, DevSpace, attachments, or another web harness.
- Preserve Local Multi GPT and native Codex subagents as independent local advisory/execution paths. This migration does not disable, replace, or route Local Multi GPT through CGW.

## Legacy Oracle and DevSpace freeze

- Oracle, DevSpace-backed GPT runs, Web Multi, comprehensive/Ultra web orchestration, CodexPro, and agbrowse are frozen for new user work. Do not open their Chrome windows, create runs, register roots, submit prompts, or select them as fallbacks.
- Keep their source, installed bytes, historical state, receipts, and recovery tooling intact for maintenance and audit. Exact recovery of a persisted historical run requires an explicit user request naming that legacy run; project work must not trigger it automatically.
- Maintenance of the legacy automation repository may continue separately, but it must not change the active user-work route back from CGW or manipulate ChatGPT apps, DevSpace, Chrome profiles, or project files as part of ordinary work.
- The installed `ultra-gpt-mode`, `ultra-economy-mode`, Oracle-browser, question-designer, workspace-setup, and Web Multi skills are legacy-frozen. Their presence is compatibility evidence, not permission to use them for new work.
<!-- END CODEX WEB GPT SUBAGENT POLICY -->
