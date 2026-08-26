# Upstream runtime policy

Oracle and DevSpace use a **newest validated stable** policy.

- Official npm `latest` is detected within six hours and becomes a candidate immediately.
- The scheduled candidate reporter never installs, promotes, restarts services, opens ChatGPT, or
  touches projects. It may only query registry metadata and maintain the one managed GitHub issue.
- A separate scheduled Codex maintainer is the promotion and installation owner. It may open the
  validation PR, merge and release an all-gates routine candidate, install that published release,
  and perform the one managed restart in a proven safe window. Those actions are not permissions of
  the reporter; they are the maintainer's gated execution phase.
- The scheduled watcher is only the candidate reporter. Its issue is the durable handoff to the
  scheduled Codex maintainer automation, with the issue assigned to GitHub maintainer
  `ventianima-lab`. The maintainer automation must claim validation within 24 hours and targets
  promotion within 48 hours when no gate is blocked.
- The scheduled Codex maintainer automation and required GitHub CI perform the tests. Routine stable patch/minor
  candidates have standing approval only after every gate passes; a major/breaking change,
  permission or OAuth change, patch conflict, failed canary, or ambiguous result requires explicit
  user approval. No elapsed-time target weakens a failed or missing gate.
- Concretely, detection is due within 6 hours, the maintainer claims validation within 24 hours, and
  a clean patch/minor targets release plus local deployment within 48 hours. The target pauses at a
  real gate; it never converts a failed or missing check into approval.
- Promotion requires the published archive integrity, exact package tree and patch hashes,
  Node syntax, focused compatibility tests, an Oracle no-submission canary, DevSpace local/public
  health and large-read/root canaries, Windows/macOS/Linux CI, review, and a normal release.
- The DevSpace canary must not infer read health from `open_workspace`, an HTTP 401, or a bundled
  instruction payload. It must receive one workspace ID, make a separate `read` call for the
  project-contained mission through that same ID, and use `read_chunk` on the same file to return
  its server-computed complete SHA-256. The local gate must match that digest to the bound mission
  bytes. An audit run must make that nonce-bearing `open_workspace` the first workspace/process/
  mutation call in its opaque OpenAI session scope; DevSpace then enforces and numbers the exact
  `open_workspace` → `read` → `read_chunk` sequence and blocks every mutation surface, including
  artifact download. The three tool results expose server-generated random receipt IDs which the
  exact terminal Oracle conversation must echo; the gate verifies those IDs against the durable
  receipt files, challenge-binding the opaque scope to the public conversation. `mcp_network_error`,
  a changed ID, a partial chunk, a missing challenge response, a digest mismatch, or another connector blocks
  promotion and blocks Pro use until a fresh regular non-Pro canary succeeds.
- The promoted `current` is the only default for new work. The previous verified version is
  retained as last-known-good (LKG) for rollback and exact historical recovery only.
- Existing Oracle runs always retain their recorded version, command, task ownership, browser
  identity, and conversation. Promotion never rewrites historical authority.

The machine-readable source of truth is [`upstream-runtime-policy.json`](../upstream-runtime-policy.json).
The scheduled GitHub workflow compares it with official npm metadata and maintains one
`Upstream runtime drift` issue labeled `upstream-runtime`. The issue is the maintainer queue, not a
notification with no owner: it records the promotion owner, validation/target deadlines, exact
candidate identities, and the closed gate checklist. The promotion owner opens a reviewed
validation PR; exact-commit CI, release publication, lifecycle install, parity, doctor, and service
health evidence close the issue. On the maintainer host this owner is the active Codex heartbeat
named `Validate upstream runtime drift`; it is intentionally not installed on downstream user
machines, which consume only already-published releases.
Its exact host contract is [`upstream-runtime-maintainer-automation.json`](../upstream-runtime-maintainer-automation.json),
and `python scripts/verify_upstream_runtime_maintainer.py` proves that the active heartbeat still
matches that contract. The contract is shipped for audit; `downstream_registration=false` prevents
it from silently creating a maintainer task on user machines.

Current runtime contract:

| Runtime | Current | Rollback LKG |
| --- | --- | --- |
| Oracle | `0.18.0` | `0.17.1` |
| DevSpace | `1.0.8` | `1.0.7` |

DevSpace `1.0.8` includes an optional local-agent daemon and provider CLI
adapters. The managed ChatGPT workspace service explicitly sets
`DEVSPACE_SUBAGENTS=false`; upstream promotion does not authorize or silently
enable that separate execution surface.

This policy intentionally optimizes for fast upstream bug/UI fixes without executing an
unreviewed moving `latest` tag on a user's machine.
