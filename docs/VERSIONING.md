# Versioning and releases

Agent Web GPT Automation follows [Semantic Versioning](https://semver.org/) as
`MAJOR.MINOR.PATCH`.

## What changes the number?

- **MAJOR** — an incompatible public CLI, manifest, schema, state, or lifecycle
  contract change without an automatic compatibility path
- **MINOR** — a backward-compatible mode, platform, installer capability,
  workflow, or substantial user-facing documentation/branding release
- **PATCH** — a backward-compatible defect, compatibility patch, safety
  tightening, or documentation correction

Frozen legacy schema strings and receipt IDs do not change just to match the
product version. Their stability is part of rollback and exact recovery.

## Version sources of truth

One release must use the same version in all of these places:

1. `package.json`
2. root package entry in `package-lock.json`
3. `install-manifest.json`
4. newest entry in `docs/CHANGELOG.md`
5. annotated Git tag `vMAJOR.MINOR.PATCH`
6. GitHub Release title `vMAJOR.MINOR.PATCH`

The source files are authoritative before publication. A tag or GitHub Release
must not be created until the exact commit passes both Windows and macOS CI.

## Release flow

1. Choose the SemVer impact and update the three machine-readable sources.
2. Add a user-oriented changelog entry.
3. Run focused tests, the fast gate, portability, package/link checks, and the
   complete contract suite required by [Release Checklist](RELEASE_CHECKLIST.md).
4. Commit and push public-safe source to `main`.
5. Require successful Windows and macOS CI for that commit.
6. Create the annotated tag and GitHub Release from the same commit.
7. Verify the release badge and downloadable source archives.

Do not reuse a published version. If release publication fails after a tag is
public, correct the release metadata or publish the next patch; do not move the
tag to different bytes.
