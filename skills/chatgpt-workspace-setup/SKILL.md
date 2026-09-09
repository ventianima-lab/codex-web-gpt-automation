---
name: chatgpt-workspace-setup
description: One-time user-authorized DevSpace and stable HTTPS setup, or focused diagnosis of workspace access.
---

# Workspace setup

Follow the installed `docs/AUTOMATION_POLICY.md`. This skill is for setup or
an actual access failure, not ordinary GPT runs or repeated daily qualification.

## Setup

1. Read existing configuration and preserve every approved root, authentication
   record, OAuth state, and unrelated customization. Fail safely on malformed
   configuration; never replace it with a guessed default.
2. Agree the exact roots and stable HTTPS endpoint with the user. Use the
   managed setup helper appropriate to that endpoint. Keep credentials out of
   prompts, logs, public files, and onboarding state.
3. The user handles ChatGPT login, app registration, permissions, and account
   personalization. Never automate those settings or request secret values.
4. Check endpoint access and one actual requested project read through the app.
   A successful health response alone is not proof of a file read. The read
   need not use a prescribed tool order, audit nonce, or three receipts.
5. Use the common temporary-chat flow with explicit Latest then requested
   effort (default Pro / 6 Pro). Enable temporary-chat personalization before submission; preserve account settings.
   Save the actual result before closing the owned tab.

Do not require a non-Pro preliminary run, a command canary, a fixed outcome
marker, or a recurring freshness test. An Oracle version change alone does not
prove native Latest/6 Pro support. The current compatibility carrier remains
`gpt-5.6-sol`, `model_strategy=current`, `thinking_time=pro`; the browser
must explicitly select Latest rather than the numeric older model.

## Diagnosis

Inspect only the failing layer: endpoint, authentication, approved root, app
access, model selection, or result capture. Recheck access after relevant
configuration changes or actual failure, not on every mission. Never repair
by deleting authentication, changing account settings, weakening approved-root
checks, or resubmitting an uncertain prompt.

Use the existing managed helper in `scripts/devspace_tailscale_setup.py` for
read-only diagnosis or explicitly authorized setup. Do not restart a shared
service while another task depends on it. Historical run records stay intact;
legacy recovery does not create a new submission.
