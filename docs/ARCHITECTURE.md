# Architecture

New work follows [the shared automation policy](AUTOMATION_POLICY.md).

## Normal path

The native commander supplies one mission, an exact approved project root,
and an explicit model/effort choice. Oracle operates a temporary ChatGPT tab;
DevSpace exposes only user-approved project access. Task type is prompt content,
not a separate planning, review, editing, orchestration, or comprehensive engine.

The current model compatibility route explicitly selects Latest in the UI
before the requested effort. The Oracle CLI carrier is not the UI model name.
Model selection is checked once before submission, without a chain of duplicate
receipts.

## Lifecycle

Each run owns its task identity, mission, state, browser tab, and output.
Capture the complete result durably before closing the owned tab. A timeout or
connection failure retains the same run for recovery; it never automatically
creates a new submission. Temporary chats have no archive/restore phase.

Transport completion means the answer was captured, not that every requested
project action succeeded. The commander evaluates the actual answer and any
relevant project tests. No magic output marker or prescribed tool-call order
is needed to express a successful result.

## Boundaries

Authentication, approved roots, and task ownership are enforced at their
respective boundaries. Setup verifies real access once and rechecks only when
relevant configuration changes or a failure warrants it. Projects retain
their domain and test requirements while sharing the app's transport policy.

Historical executors and schemas remain isolated recovery references. Existing
records are not migrated by rewriting their authority, and legacy recovery
never becomes an automatic new-work fallback.

Installation preserves user configuration and credential-bearing state.
Source delivery, installed byte parity, and release publication are reported
separately. Runtime updates must not interrupt foreign active work.
