# Shared automation policy

This is the common policy for new automation work and its consuming projects.
Project instructions may add domain, build, test, and safety requirements; they
must not recreate retired automation modes or audit ceremonies. Historical run
records retain their original meaning and are never rewritten by this policy.

## One execution flow

- Express planning, research, review, and implementation requirements in the
  mission, not separate execution modes. Select the model and effort explicitly.
- For the current Oracle compatibility route, select **Latest** in ChatGPT,
  then the requested effort. The default is Pro, observed as **6 Pro**. Do not
  select GPT-5.6 numerically to obtain Pro. The CLI compatibility carrier is
  `gpt-5.6-sol` with `model_strategy=current`; it is not the requested UI model.
  Keep this workaround until native Oracle support is actually verified.
- Use temporary chats. The runner enables and confirms temporary-chat personalization before submission; it does not change account settings. If confirmation fails, do not submit.
  Do not silently change account, privacy, app, or permission settings.
- Check the actual selected model once before submission. Do not duplicate
  that check through multiple receipts or recurring qualification stages.

## Results and tab ownership

- Bind each run to its owning task, exact project root, mission, and browser tab.
  Never adopt or close another task's tab or run.
- Save the complete result durably before closing the owned tab. Response
  completion alone is not permission to discard an uncaptured result.
- On timeout or connection failure, retain the same tab and recover that run.
  Do not automatically resend the prompt. A lost tab requires an explicit
  recovery decision; elapsed time is not proof that submission never occurred.
- New work has no archive or restore phase. Historical recovery is separate
  and must not mutate historical evidence or become a new-work fallback.

## Minimal validation

- Preserve authentication, approved roots, user authority, and task ownership.
  Check access during setup or when relevant configuration changes, not as a
  repeated daily or per-project ceremony.
- There is no compulsory `open_workspace` / `read` / `read_chunk` order,
  `auditNonce`, three-receipt requirement, or mandatory output marker. Tool
  choices follow the mission. Report unavailable access or incomplete results
  honestly; a process exit code alone does not prove the mission succeeded.
- Projects retain relevant tests and domain checks. Automation transport
  checks do not replace those tests, and projects must not duplicate transport
  audits. Stop testing after relevant checks pass unless new evidence warrants
  more investigation.
- Preserve the configured native commander and current user-approved worker
  routing. This policy does not restore CGW or force web delegation.

## Adoption

Projects reference this document instead of copying its implementation rules.
Conflicting older automation-specific instructions are superseded for new work;
unrelated project instructions and historical recovery authorities remain intact.
Source policy changes are not proof that installed runtime changes have shipped.
