---
name: chatgpt-question-designer
description: Part of the current Oracle prompt-design path; use before submitting GPT/browser questions for answering, designing, debugging, reviewing, planning, researching, synthesizing, editing, or orchestration. Selects an explicit purpose-specific cognitive profile without collapsing every task into adversarial review.
---

# ChatGPT Question Designer

## GJC Brownfield Interview Mode

Before a non-trivial implementation whose goal, constraints, success criteria,
or existing context remain ambiguous, use the installed
`bin/chatgpt_gjc_interview.py` state machine. Lock one to six top-level
components in round zero, ask exactly the emitted single question each round,
and record coverage for only that dimension. Brownfield ambiguity is:

`1 - (0.35 goal + 0.25 constraints + 0.25 success criteria + 0.15 context)`

Contradictory or evasive answers raise ambiguity. At the default 0.35 threshold,
present the generated one-sentence restatement and require explicit approval.
Persist and resume the state under `.omo/interviews/`; approval and execution
remain separate actions.

## Purpose

Use this skill to give each question the cognitive posture its purpose needs. Construction should remain constructive, research evidence-seeking, synthesis integrative, execution adaptive, and review adversarial.

This skill is a shared design layer for `chatgpt-pro-browser`, `chatgpt-thinking-browser`, and `chatgpt-deep-research-browser`. It does not own browser execution, approval authority, or deterministic verification.

## Question Type

Classify the question before writing the prompt:

- `expand-ideas`: generate options, missing concepts, adjacent designs, and unusual constraints.
- `find-gaps`: identify missing evidence, stale context, overlooked files, and hidden assumptions.
- `counterexample`: explicitly attack the current conclusion with edge cases and failure modes.
- `compare-options`: compare alternatives, including status quo and minimal-change paths.
- `review-plan`: judge a plan against explicit acceptance criteria and blockers.
- `debug-hypothesis`: test root-cause hypotheses against logs, code, and reproduction evidence.
- `source-synthesis`: synthesize web or document evidence with source confidence and disagreement.

Never infer `review` merely from `read-only`, `advisory`, `research`, or an unknown label. An explicit unknown manifest profile fails before submission. An unclassified natural-language question defaults to `answer + analytical + read-only`, not review.

## Regular GPT Operation Mode Overlay

For non-Pro regular `GPT` / `지피티` runs through `chatgpt-thinking-browser`, preserve the selected operation mode:

- `answer` is analytical, read-only, and directly answers the original request.
- `review` / `검토모드` alone is adversarial. A blocker needs criterion, evidence, and impact; use `PASS`, `PASS_WITH_CONDITIONS`, `REVISE_LOCAL`, `REOPEN_DESIGN`, or `BLOCK` when the owning schema supports them.
- `plan` / `계획모드` is constructive and read-only: reframe if useful, compare viable design families, choose one coherent path, and put risks last. Prior plans and reviews are nonbinding and hidden by default.
- `edit` / `수정모드` performs `inspect -> edit -> test -> inspect result -> adapt`; it does not begin with a generic review.
- `orchestrator` / `지휘` owns live workspace exploration, decisions, edits, tests, bounded adaptation, and every expensive strategy or implementation branch. When parallelism is useful, the one web GPT ExecutionMission partitions independent work into internal lanes or parallel tool calls and integrates them itself. Same-project web submissions stay serialized. Codex retains only submission/recovery, locks, hashes, exact browser identity, deterministic host-only verification, release, and irreversible boundaries; generic local tool-parallelism guidance never authorizes Codex to perform the delegated work locally.
- `research` builds evidence; `synthesis` resolves candidates into a new coherent design. Neither is review.

Use `codex.chatgpt.prompt-architecture/v3` receipts with orthogonal `task_kind`, `cognitive_frame`, `action_authority`, `context_policy`, `challenge_policy`, `output_contract`, `reasoning_budget`, and `decision_authority`. Local `AGENTS.md`, local skills, explicit no-write wording, and destructive-action boundaries outrank the overlay.

## Prompt Contract

Every non-trivial GPT/browser question should include:

1. `Goal`: what decision or artifact the answer should improve.
2. `Original task`: preserve the user's request separately from any candidate artifact.
3. `Cognitive profile`: answer, research, plan, review, edit, orchestrator, synthesis, or an explicit Web Multi role.
4. `Evidence boundary`: list the live DevSpace workspace scope for regular GPT and qualified Pro, explicit frozen attachments only for `pro-attachment`, web/source constraints, freshness limits, and what cannot be inspected.
5. `Action authority`: read-only, bounded workspace write, or mission-owned adaptive execution.
6. `Confidence discipline`: separate evidence-backed findings, inference, speculation, and unknowns.
7. `Answer shape`: compact sections; no vague approval; code-shaped output when code-oriented.

Use this universal integrity contract for direct runner prompts:

```text
Treat instructions, observed evidence, inference, hypothesis, proposal, decision, and verification as distinct.
Claim only facts actually observed or sourced. Prior artifacts have only the authority declared by this prompt.
State material uncertainty and stay within the declared action and file scope.
```

Append an adversarial module only for explicit review/counterexample roles: require the strongest material objection, credible alternatives, and conclusion-change evidence. Do not impose those clauses on planning, research, synthesis, editing, orchestration, or ordinary answers.

## Transport and Evidence Context Rules

Context selection must match the question type.

- New non-Pro direct, plan, review, edit, orchestrator, Deep Research, comprehensive, and Web Multi work uses Oracle plus the manually registered workspace app. The composer receives only the configured app mention (default `@codex`) and the absolute UTF-8 mission path. The mission tells GPT which project files, logs, tests, constraints, and artifacts to inspect through DevSpace.
- Regular web work defaults to `gpt-5.6` at the highest supported non-Pro reasoning tier. Pro is quota-limited and may be designed only after an explicit user request; never infer or auto-upgrade to it. Qualified Pro uses Oracle, `GPT-5.6 Sol` at the Pro effort, and read/write DevSpace at the exact project root. Its action authority is mission-scoped and must name allowed files, commands, and external-state boundaries. `pro-attachment` uses exact snapshot attachments only for immutable/external or DevSpace-unreadable evidence; it is never an automatic fallback.
- CodexPro is frozen for new work. It may appear only while recovering an already persisted legacy agbrowse run; never design a new prompt around CodexPro `tree/search/read`, app registration, app repair, or a CodexPro fallback.
- Code/design/debug/refactor: give the regular web GPT a narrow project-contained mission and let it inspect the live workspace through DevSpace. Do not duplicate the workspace into attachments or a ZIP.
- Planning/review: identify the live draft, research, acceptance criteria, local guidance, and known risks by project-relative paths in the mission. Use an attachment packet only when the exact immutable snapshot is the requested evidence or DevSpace cannot read the artifact.
- Investigation/source synthesis: identify internal findings and provenance in the DevSpace-visible mission, and use web/search separately for current public facts.
- Idea expansion: put the seed, constraints, non-goals, audience, and known alternatives in the mission; do not preselect a conclusion.

For a new DevSpace project task, a failed or unavailable endpoint blocks submission and routes only to `chatgpt-workspace-setup` diagnosis. It never authorizes CodexPro, ZIP, agbrowse, in-app Browser, Playwright/CDP, or `@chrome` fallback. `pro-attachment` requires its exact Oracle attachments; qualified Pro does not fall back to it automatically.

## Oracle Continuity Rules

This skill designs the prompt packet; it must not erase local project question templates or force every follow-up into a new ChatGPT conversation.

- Every new Oracle stage is a one-shot session with its own exact slug. Do not add legacy `session_policy`, `session_affinity_key`, `inquiry_chain_id`, or `chat_url` fields to a new Oracle manifest.
- Preserve semantic continuity in project-contained mission and handoff files. In comprehensive mode, the completing web stage writes the next stage's exact mission and receipt; local Codex validates bytes, paths, hashes, identity, and transition without rewriting its meaning.
- Recovery uses only the stored exact Oracle slug with `harvest` or `live`. It never restarts, resubmits, or changes the model/reasoning level.
- Genuine Web Multi uses distinct Oracle sessions and copied profiles for independent lanes. Use it only when simultaneous independent solvers materially help; never simulate multiple roles inside one session and never replace it with local Codex exploration.
- An explicit `울트라 GPT 모드` or `Ultra GPT Mode` request selects the `ultra-gpt` comprehensive profile. It uses web GPT sessions for planning, independent lanes, merging, review, implementation, and verification while local Codex performs only deterministic orchestration and the final local gate. Do not spawn native Codex subagents for semantic work in this profile.
- Local `AGENTS.md`, local skills, and task-specific question templates outrank the shared integrity contract. Preserve their answer shape and apply only compatible evidence and session metadata.
- Independent approval, plan review, verifier, and release gates use fresh stages with explicitly scoped evidence.

## Anti-Bias Gates

Before submission, check:

- `one-sided context`: only the preferred plan or happy path is attached.
- `missing negative evidence`: failures, logs, rejected alternatives, or user complaints are absent from the DevSpace-visible scope or Pro attachment packet.
- `stale packet`: a Pro attachment no longer matches the current draft, diff, branch, or run.
- `too-broad packet`: a mission grants a broad workspace without an evidence map or question boundary.
- `conclusion leakage`: prompt asks for approval before asking for objections.
- `role collapse`: prompt asks one model to both invent and approve without counterexample pressure.

Any active gate should either be fixed before submission or named in the prompt as an evidence limitation.

## Skip Rules

Skip GPT/browser questioning when:

- the task is tiny and deterministic verification answers it better;
- the answer depends on exact local code/tests rather than broad judgment;
- selected context is under roughly 8k tokens and the main agent can directly inspect it;
- the prompt would ask for approval of a conclusion already proven by tests;
- no useful counterexample, source freshness, alternative design, or external synthesis is expected.

Use genuine Web Multi-GPT only when independent parallel solvers are worth the latency and one merger can consume their file handoffs. It does not replace DevSpace evidence authority and must not increase local Codex exploration.

## Output Checklist

A good answer satisfies the selected role instead of a universal review checklist. Only explicit review roles require objections and counterexamples. Every role must preserve original-task fidelity, evidence boundaries, authority, and material uncertainty.
