<!-- BEGIN CODEX WEB GPT SUBAGENT POLICY -->
## Shared automation policy

- Follow the installed app's `docs/AUTOMATION_POLICY.md`; projects retain their
  own build, test, domain, and safety requirements.
- Preserve the configured commander, reasoning, and user-approved worker
  routing. Installation must not silently choose another main model.
- Use bounded workers with non-overlapping write scopes when useful. The
  commander integrates results and remains responsible for the task.
- Use one mission flow, explicit Latest/model and effort selection, temporary
  chats, durable result capture before closing the owned tab, and same-run
  recovery without automatic resubmission.
- Do not add execution modes, mandatory audit receipts/tool order, per-project
  qualification chains, or archive/restore stages for new work.
- Preserve authentication, approved roots, original profiles, and historical
  evidence. Never adopt, stop, or close another task's run or tab.
- Never restore removed CGW routing. Account and permission changes remain
  under user control.
<!-- END CODEX WEB GPT SUBAGENT POLICY -->
