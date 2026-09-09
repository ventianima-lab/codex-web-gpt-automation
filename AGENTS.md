# Codex Web GPT Automation repository rules

## Common policy

Follow [the shared automation policy](docs/AUTOMATION_POLICY.md) for all new
work. Projects reference it and retain their own build, test, data, and safety
requirements. Do not recreate retired automation modes or audit ceremonies.

Use one mission-based flow, explicit model/effort selection, temporary chats,
durable result capture before owned-tab cleanup, and exact-run recovery without
automatic resubmission. Select Latest explicitly before the requested effort.
The runner enables temporary-chat personalization before submission. Account settings and permissions remain user-controlled. New work has
no archive/restore phase.

Historical state and recovery are compatibility-only. Preserve original
records and authority; never infer ownership or replace an uncertain submission.

## Source and verification

- This repository is the reusable source; installed files are deployment copies.
- Preserve unrelated user changes and use non-overlapping writer scopes.
- Test changed behavior, broadening checks proportionately to risk. Do not add
  mandatory semantic review loops, ordered tool calls, or three-receipt gates.
- Commit and push public-safe changes and check CI before claiming delivery.
  Never publish secrets, host-only artifacts, or private history.
- Report source, installed, and released status separately. A version bump or
  branch test does not prove publication or installed byte parity.

## Setup and safety

- Preserve approved roots, authentication, OAuth state, signed-in profile seeds,
  and unrelated settings. Never substitute a nearby project root.
- Establish access during setup; repeat checks only after relevant changes or
  actual failure. HTTP health alone does not prove a requested file was read.
- App registration, login, permissions, and account personalization remain manual.
  Never collect passwords, tokens, or cookies in run state.
- Preserve the configured native commander. Never restore removed CGW routing.
- Never stop or adopt foreign runs. Bind recovery and cleanup to the exact
  owning task, process, tab, root, and mission.
- Stop testing after relevant checks pass unless new evidence warrants more.

## Filesystem hygiene

- Use task-specific system temporary directories or gitignored `.codex-tmp`.
  Never create temporary artifacts directly at a drive root.
- Preserve user projects, credentials, active references, and junction targets.
  Resolve exact ownership before cleanup and prefer recoverable operations.
  Never use a workspace root as a recursive deletion target.
