# Release checklist

Follow [the shared automation policy](AUTOMATION_POLICY.md). Release checks
protect shipped behavior and publication identity; they are not per-project
execution audits.

## Verify the change

- Inspect the actual diff and preserve unrelated user work and historical state.
- Run focused tests for changed behavior. Use required cross-platform CI for
  the release commit; broaden local testing only for unresolved risk.
- For browser changes, exercise a bounded no-submission check and relevant
  owned-tab lifecycle behavior. For access changes, verify an actual app read.
  Do not require a tool-call sequence, three audit receipts, or a magic answer.
- Test lifecycle changes in a temporary installation, including preservation
  and rollback. Do not interrupt foreign active runs or mutate credentials.
- Check package inventory, public links, and version consistency.

## Publish and deploy

- Merge the reviewed change through the normal repository PR process.
  Do not require a synthetic `INDEPENDENT_REVIEW: PASS` comment as a second
  approval system. Never fabricate review or execution evidence.
- Match package metadata, lockfile, install manifest, and changelog versions.
- Require successful exact-main-commit portability CI before publication.
- Push an annotated immutable version tag; never move an existing published tag.
- Verify the peeled remote tag, non-draft GitHub Release, and `releases/latest`
  refer to the intended version and commit.
- Verify lifecycle installation and source/install byte parity separately.
  Source delivery does not prove the installation has changed.
- Preserve the rollback backup and report modified-file conflicts instead of
  overwriting local customization.

Missing publication or installation evidence means **release incomplete**.
A version bump alone is not a release. Report the exact remaining step rather
than starting repeated unrelated audit or review loops.
