# Global ChatGPT routing

The supported English names are `GPT`/`direct`, `plan`, `review`, `edit`,
`orchestrator`, `deep research`/`deep-research`, `Web Multi-GPT`,
`Local Multi-GPT`, `comprehensive mode`, `Ultra Economy Mode`/`ultra-economy`,
`Ultra GPT Mode`/`ultra-gpt`, and `Pro`. Korean names documented in the main README map to the same runners;
language never selects a different backend.

Use this routing in the Codex global `AGENTS.md` after installing the package.

- New regular ChatGPT work, including direct, plan, review, edit,
  orchestrator, research, comprehensive, and Web Multi-GPT, uses Oracle plus
  the manually registered DevSpace app.
- Regular web work selects `gpt-5.6` with Oracle `extra-high`, the highest
  supported non-Pro reasoning tier. It does not silently fall back to a lower
  tier or upgrade to Pro.
- The regular composer contains only the configured app mention (default `@codex`), the exact UTF-8 project root,
  and the absolute UTF-8 mission path. It tells DevSpace to open the exact root
  before reading the mission, never the mission directory, a parent, a child,
  or the active workspace as a substitute. It does not attach the task body and
  does not inspect or mutate ChatGPT app settings per question.
- Pro is quota-limited and explicit-only. Ordinary modes, comprehensive plans,
  and recovery logic must never select or upgrade to Pro automatically. A
  standard comprehensive manifest must contain `allow_pro: true`, supplied
  only after an explicit user request. Selecting Ultra Economy Mode authorizes
  the Luna-Max local/web-first profile, not Pro; its one read-only Pro advisory
  requires a separate explicit user authorization.
- Every new qualified Pro run uses Oracle, `GPT-5.6 Sol` at the Pro effort, the
  `pro-devspace-readonly` transport, and the manually registered DevSpace app.
  It binds the exact project root but is
  read-only and limited to design, advice, or review; it must not create, edit,
  or remove files or run commands. A regular `GPT-5.6` `extra-high` DevSpace
  stage owns those actions. Repository safety rules remain authoritative; Pro
  never changes accounts, app settings, or external state. One-time app qualification is sufficient: do not inspect app
  settings or picker state per run.
- Qualified Pro output uses the v1 task-outcome marker. Exit zero and a durable
  answer do not count as success when DevSpace exposed no callable tools or the
  exact mission/root was unread. A durably terminal `NOT_EXECUTED` run may
  release its task-scoped project lock for one fresh retry with identical mission bytes;
  repeated tool absence is `attention_required`, not an automatic app-settings
  repair or attachment fallback.
- For a v1 task-outcome run, Oracle may use a bounded completion fallback when
  ChatGPT omits both the thinking label and completion action bar. It still
  requires the exact conversation and new assistant turn, no Stop control or
  active strong-thinking signal, stable full response bytes across two checks
  for at least five seconds, and one unambiguous final `TASK_OUTCOME` line.
  Generic and legacy runs keep upstream completion behavior.
- Explicit `pro-attachment` remains a separate read-only route for immutable or
  external evidence and is never an automatic fallback from a qualified Pro
  DevSpace run. Persisted legacy `pro-devspace` write runs retain their exact
  original authority only during recovery.
- Existing persisted agbrowse runs remain recovery-only. There is no new
  agbrowse submission path and no Oracle-to-agbrowse fallback.
- Oracle run ownership is scoped by Codex task plus exact run identity, not by
  project root alone. Different Codex tasks may work concurrently in the same
  project with distinct mutexes, slugs, dynamic CDP ports, profiles, and
  conversations. Only the same task's unresolved run blocks its next
  submission. Foreign or legacy-unbound runs are never inferred from the
  newest project run and cannot be recovered, harvested, followed up, stopped,
  or canceled by the current task.
- Incident and completion reports preserve that same boundary. The machine
  packet records the evaluating task, exact owner task, run ID, slug, and one
  bounded operational instruction. A foreign task receives only a route back
  to the owner, while an already terminal and harvested run receives no
  recovery instruction even if its local display status says attention is
  required. Reports are rendered separately per target task rather than
  broadcasting one owner's next action to every task sharing the project.
- Comprehensive stages author the next semantic mission and a bound hash
  receipt. Local Codex owns transport, immutable identity, host safety, and one
  final deterministic gate rather than rewriting web output.
- An optional comprehensive Pro stage is available only after explicit opt-in
  and uses read-only DevSpace for design, advice, or review. Its plan gives any
  mutation or command to a regular `GPT-5.6` `extra-high` stage. A separately
  explicit immutable-evidence boundary may select `pro-attachment`. The Pro route returns one strict
  identity-bound JSON envelope whose output and next-mission strings the host
  materializes byte-for-byte.
- Genuine Web Multi-GPT uses distinct Oracle sessions. Windows lanes use
  independent throwaway copies of the signed-in Oracle profile, run in waves
  of at most five, and hand compact files to one merger.
- Local Multi-GPT is an optional, read-only PC-local advisory component. It is
  fixed to `gpt-5.6-luna` with `max` reasoning and is not a web transport or a
  release authority.
- Ultra Economy Mode keeps the local commander and native subagents on exact
  Luna Max while separate Oracle sessions own regular review,
  implementation, and web verification. Its first request in each Codex task
  always produces one Luna/Max selection instruction; after user confirmation,
  that task never re-inspects the runtime or asks again. A separate explicit
  authorization is required before its optional read-only Pro design advisory.
- Ultra GPT Mode forbids cognitive native subagents. A deterministic local
  controller runs the `ultra-gpt` comprehensive profile: regular web plan and
  review, two to five parallel isolated-worktree Web Multi implementers with
  host-validated disjoint project-relative ownership and concurrency at most
  three, an all-lanes audit barrier, merger/integration, and a final web gate.
  The profile cannot select Pro internally. One pre-workflow Pro design advisory
  is allowed only after a separate explicit user authorization.

## Standalone Pro versus comprehensive

`chatgpt-pro-browser` is the visible standalone Pro skill. It submits one
explicitly requested, qualified read-only DevSpace Pro design, advice, or
review session, saves the durable result, returns it to the calling Codex task,
and stops unless the user explicitly requests a bounded follow-up round in the
same conversation. New read-only Pro parents normalize default `archive=auto`
to `never`. That round must use the internal runner `followup` command; only a
historical or explicitly archived exact task-bound parent uses bounded restore
and re-archive. A before-composer restore failure goes directly to explicit
user-confirmed no-submission settlement and must not be harvested. Any URL
drift or unverified transition fails closed without fallback.
The follow-up command requires the exact terminal parent run directory, a
project-contained UTF-8 mission,
and a unique round key. Raw Oracle follow-up options remain blocked. Each round
gets a new Oracle run/slug but must prove the unchanged ChatGPT conversation
and append hash-bound reservation/result receipts. It never starts
implementation, edits files, or runs commands. A
separately explicit immutable-evidence request may use `pro-attachment`. Its required
`WEB_MULTI_NEEDED` decision may start the ready-to-run advisory Web Multi stage
after the exact Pro session is terminal; that advisory still returns to the
calling Codex task rather than implementing.

`chatgpt-pro-plan-handoff` owns comprehensive mode. Only that staged runner may
place an optional Pro decision between plan and review and continue afterward
to implementation and gates. Natural-language `Pro` or `GPT Pro` requests route
to the standalone skill; explicit comprehensive-mode requests route to the
handoff skill.

## Orchestrator versus comprehensive

These two are often confused because both let the web GPT own implementation.
They differ in structure, not in ambition.

| | `orchestrator` (지휘) | comprehensive (종합) |
|---|---|---|
| Runner | `chatgpt_oracle_dispatch.py --mode orchestrator` | `chatgpt_oracle_comprehensive.py` |
| Web submissions | one | several, one per stage |
| Stage receipts | none | hash-bound per workflow/stage/attempt/input |
| Independent review | no | yes, review repairs and finalizes the plan |
| Pro / Web Multi stage | not available | selectable |
| Completion | the answer itself | final web PASS plus zero-exit local gate |
| Recovery unit | one run | workflow plus stage identity |

Comprehensive mode runs orchestrator-equivalent work as its implementation
stage, so it contains that mode rather than competing with it.

Pick `orchestrator` when the goal and approach are settled and one authorized
pass should finish the work at the lowest local and web cost. Pick comprehensive
when the plan needs independent review, when Pro or Web Multi must participate,
or when completion must be proven by a deterministic local gate. Do not hand-chain
`orchestrator` submissions to imitate staging; submissions from the same Codex
task stay serialized while different task owners remain isolated, and the
workflow engine owns stage identity and recovery.

The package does not overwrite an existing user `AGENTS.md` automatically.
Apply this block deliberately so unrelated personal rules are preserved.
