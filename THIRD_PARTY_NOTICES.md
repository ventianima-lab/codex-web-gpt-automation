# Third-party notices

This repository ships no copy of agbrowse, the Oracle package, DevSpace,
Codex, CodexPro, browser binaries, or account data. It does ship narrow
textual compatibility patches for hash-verified Oracle 0.16.1/0.17.1, the
explicitly scoped WebJjonku Linux Oracle 0.18.0 profile, and DevSpace 1.0.4
installations.

- `hehee9/multi-gpt@4f5e130` is MIT-licensed. Its attribution and the recorded `server.mjs` hash must be preserved when its upstream-compatible integration is changed.
- `agbrowse@0.1.18` is an external npm package retained only for recovery of
  already-persisted legacy runs (metadata license: MIT; integrity:
  `sha512-vO2E1XrqTAvkWeSyV1xzsONz+OBB3aDKbxIGVS7Z4pH42Hxg/mlcteIAzM+EuD4hnp6Tt5IJu/X2fjMOiftBCA==`).
  It is not a new-submission engine. A root `LICENSE` file was missing from the
  upstream GitHub checkout at packaging review; rely on published package
  metadata and re-check before any redistribution. This project installs the
  package externally and does not copy its source.
- `@steipete/oracle` is an external MIT-licensed browser automation package.
  The default tested version is 0.17.1. Oracle 0.18.0 is accepted only by the
  named `webjjonku-linux` profile after its published npm archive integrity and
  complete published package payload are verified. Separately installed
  dependencies remain governed by the invoking runtime lock. Agents may resolve any other version
  only after capability validation. Its package source is not vendored. Files
  under `bin/oracle-compat/0.17.1` are current derivative patch instructions;
  `bin/oracle-compat/0.18.0` contains the scoped follow-up timeout patch, and
  `bin/oracle-compat/0.16.1` remains frozen for exact legacy recovery. All retain
  the following upstream MIT notice.
- `@waishnav/devspace` is an external MIT-licensed MCP workspace server. The
  tested version is 1.0.4. Setup resolves it externally; this repository does
  not vendor its source. The derivative patch instruction under
  `bin/devspace-compat/1.0.4` retains the upstream MIT license and is applied
  only when both the package version and exact source hash match.
- OMO Codex Light (`lazycodex-ai`) and Tailscale are optional external
  installations. Their executables and source are not redistributed here.

## @steipete/oracle MIT notice

MIT License

Copyright (c) 2026 Peter Steinberger

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## @waishnav/devspace MIT notice

MIT License

Copyright (c) 2026 Waishnav

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## hehee9/multi-gpt MIT notice

MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
