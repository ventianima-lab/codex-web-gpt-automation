---
name: ultra-economy-mode
description: Run 초절약모드 for expensive or long Codex tasks by keeping the local commander and every native subagent on exact gpt-5.6-luna with max reasoning, using qualified ChatGPT Pro for architecture only after separate explicit authorization, and moving implementation and review into separate web sessions. Use when the user says 초절약모드, Ultra Economy Mode, or explicitly requests a Luna Max local commander with web-first execution.
---

# Ultra Economy Mode

Minimize local model cost without treating the small local model as the main
reasoning surface. Use the existing Oracle comprehensive engine with the
`ultra-economy` profile.

## Activation gate

1. On the **first** Ultra Economy Mode request in a Codex task, always stop
   before creating a subagent, browser, Oracle, Pro, or web session and give
   exactly one concise instruction: select `GPT-5.6 Luna` and reasoning effort
   `Max`, then confirm completion.
2. Give that instruction even when the user says the model is already selected.
   Do not inspect, infer, or verify the current model or reasoning effort from
   runtime metadata, screenshots, `~/.codex/config.toml`, role files, prompts,
   tool output, or previous tasks.
3. After the user confirms the selection, treat the activation handshake as
   satisfied for the rest of the same Codex task. Continue the workflow without
   asking again, including after compaction, recovery, stage transitions, or
   follow-up requests in that task.
4. Ask once again only for a new Codex task's first Ultra Economy Mode request.
   Never rewrite the user's global model defaults to activate this mode.
5. The activation handshake does not authorize Pro. Require a separate explicit
   user Pro authorization before the first read-only Pro design stage. If the
   user does not authorize Pro, do not start this profile; offer the ordinary
   non-Pro comprehensive profile instead.

## Local commander contract

- Keep the commander to routing, compact mission creation, durable receipt
  reading, exact-session monitoring, hash checks, and one deterministic gate.
- For every substantive local semantic task, spawn one fresh `default`
  subagent with explicit model `gpt-5.6-luna`, reasoning effort `max`, and a
  minimal history fork. Do not use the globally configured scout,
  implementer, or verifier roles because their model contracts may differ.
- Give a subagent only the bounded objective, exact artifact paths, current
  stage receipt, authority boundary, and success criteria. Never forward the
  full conversation or a growing transcript.
- Prefer one worker at a time. Use at most two only for genuinely independent
  read-only work; never exceed the global cap of three spawned threads.
- Deterministic host scripts and simple status polling remain commander work;
  they do not require model delegation.

## Web-first stage graph

Run separate sessions so each semantic boundary can inspect the prior durable
artifact:

```text
one-time exact-root qualification
  -> qualified Pro design (design-only mission on read-only DevSpace)
  -> regular web design review and implementation-mission authoring
  -> regular web implementation and project tests
  -> separate regular web final verification or repair handoff
  -> one local deterministic gate
```

Use `bin/chatgpt_oracle_comprehensive.py` with these manifest fields:

```json
{
  "schema": "codex.chatgpt.oracle-comprehensive/v1",
  "workflow_profile": "ultra-economy",
  "initial_stage": "pro",
  "allow_pro": true
}
```

Add the normal absolute project, workflow, mission, app, and local gate fields.
The local commander owns the one-time conversational activation handshake; the
engine does not re-read or re-verify the task model at later stages. A manifest
self-declaration is not a substitute for the handshake.

The engine must fail closed before submission when the separate explicit Pro
authorization and `allow_pro: true`, profile, Pro-first stage, exact root
qualification, or minimum four-stage budget is missing. Do
not substitute an attachment for readable DevSpace, and do not use Pro as the
first connector-health probe.

## Failure and residual work

- Recover only the exact persisted Oracle stage. Never create a replacement
  submission from an ambiguous or possibly submitted failure.
- If web work reaches a genuine local-only boundary, give that one bounded
  residual task to a fresh Luna Max subagent, then return to a separate web
  verification stage when semantic review is still needed.
- Do not repeat app/settings checks or endpoint probes after the project's
  exact-root qualification while the DevSpace config hash is unchanged.
- Completion requires the final web PASS receipt and a zero-exit local
  deterministic gate. Local Luna judgment is not release authority.
