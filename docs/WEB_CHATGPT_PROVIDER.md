# Web ChatGPT provider for OpenCodex

This optional local route lets OpenCodex act as the client while regular Web
ChatGPT performs the work through Oracle and the registered DevSpace app. It
uses the Web ChatGPT allocation. It does not use an OpenAI API key or the Codex
subscription quota.

## Boundaries

- The bridge binds only to `127.0.0.1` and requires a generated bearer token.
- OpenCodex sends text requests to the model. Web ChatGPT, not the local Codex
  tool loop, owns file and command tools through DevSpace. An explicit image
  request asks Oracle to save the generated image artifact and returns it as a
  local Markdown image reference; the bridge does not expose arbitrary binary
  upload or download endpoints.
- Exactly one Web ChatGPT run may be active. A second request receives HTTP 429
  instead of creating a duplicate browser submission.
- A submitted Oracle run is never terminated when the client disconnects. Its
  persisted run remains the only recovery authority.
- Mission files are retained under `<project>/.codex-tmp/web-chatgpt-provider`
  so exact-session recovery keeps its original bytes.

## Install

Install this repository first, then configure the provider:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_web_provider_setup.py" install `
  --project-root "D:\New project" `
  --app-name codex
```

The setup makes a timestamped local backup of the OpenCodex config, adds only
the `web-chatgpt` provider and its `web-gpt-codex` model, and registers a hidden
per-user login watchdog. Existing providers and the selected default remain
unchanged.

Check readiness:

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_web_provider_setup.py" status
```

Select `Web ChatGPT Codex (DevSpace)` in the Codex/OpenCodex model picker.
Long-running requests send SSE keepalives while the desktop browser works.

To generate an image, ask directly in the selected provider session, for
example `이미지 생성해줘` or `create an image of ...`. Image-related coding
requests such as `이미지 생성 기능을 provider에 추가해줘` remain normal text
implementation requests and are not routed to the image-artifact flag.
