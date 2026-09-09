---
name: chatgpt-oracle-runtime
description: Execute one user-authorized Oracle temporary-chat mission with explicit model and effort, durable capture, and exact-run recovery.
---

# Oracle execution

Follow the installed `docs/AUTOMATION_POLICY.md`. Use one mission-based flow;
planning, research, review, and editing are prompt content, not modes. Preserve
the configured native commander and user-approved delegation.

## Execute

Write the requested objective and scope in a UTF-8 mission inside the approved
project root. Preview without starting a browser or submitting a prompt:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_run.py" execute --project-root C:\project --mission-path C:\project\mission.md --model latest --effort pro --dry-run
```

For authorized execution remove `--dry-run`. Select the requested effort;
`extra-high` is also supported. Do not infer permission for an unrelated task
or silently downgrade. The public `latest` option is translated to the known
Oracle `gpt-5.6-sol/current` compatibility carrier, not passed upstream as an
invented model slug. Explicitly click Latest before selecting effort and
check the observed selection once.

Use the configured app name (default `codex`) and exact project root.
Do not change authentication, approved roots, account personalization, app
registration, or permissions. Temporary chats preserve the user's existing
enabled personalization.

## Capture and recover

Save the complete answer durably before closing the exact owned tab.
Captured output is evidence of capture, not proof that every mission action
succeeded. Report the actual result and relevant project-test evidence.
No mandatory `TASK_OUTCOME` marker, audit nonce, three receipts, or tool-call
sequence is required.

On timeout or connection failure retain the same run and tab:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_run.py" reconnect --run-dir C:\exact\run --dry-run
```

Remove `--dry-run` only to observe that same run. Reconnection is prompt-free;
never automatically replay a prompt or adopt another task's tab. If the tab
was lost, report that fact and obtain an explicit recovery decision.

New work has no archive/restore phase. Historical executors and schemas remain
exact-recovery-only; preserve their original records and authority rather than
rewriting old state or selecting a retired mode for a new submission.
