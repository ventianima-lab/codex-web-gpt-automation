---
name: chatgpt-question-designer
description: Write concise mission prompts for the single Oracle execution flow without adding modes or audit stages.
---

# Mission design

Follow the installed `docs/AUTOMATION_POLICY.md` (or the source repository's
same document). Preserve the configured commander and user-approved routing;
this skill does not require web delegation.

Write the actual objective, relevant context or file paths, requested result,
and necessary authority boundaries. Ask only unresolved user-owned questions
that would materially change the work. Do not require interview scores,
purpose schemas, stage chains, audit nonces, or receipt counts.

Adapt the prose to the work: research should seek sources, debugging should
test hypotheses, and review should identify actionable defects. These are
prompt choices, not execution modes. Use only the selected model and effort.

Do not embed controller polling or recovery tasks inside the web mission.
The owning local task handles submission, same-tab recovery, durable capture,
and tab cleanup. Do not demand a magic completion marker; evaluate whether
the returned result addresses the requested work.
