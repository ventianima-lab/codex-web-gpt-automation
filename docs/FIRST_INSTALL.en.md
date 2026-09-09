# First install

The app and consuming projects follow the [shared automation policy](AUTOMATION_POLICY.md).
This guide covers one-time setup, not a qualification loop before every run.

## Install and resume

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python install.py --dry-run
python install.py
python doctor.py
python onboard.py --lang en start --root <project-folder>
python onboard.py --lang en next
```

Use `python3` on macOS. Repeat `--root` for additional approved projects.
Resume existing setup with `python onboard.py resume`; do not reset successful
stages. Follow the current instruction from `next`, using
`python onboard.py confirm <stage-id>` after the required user action.

Tailscale Funnel is the managed default. Other providers need a stable HTTPS
`/mcp` endpoint. Preserve existing roots and configuration. Optional Local
Multi-GPT is separate from the normal execution path and defaults to disabled.

## User-controlled setup

The user handles provider login, DevSpace Owner credentials, Oracle ChatGPT
login, app registration and OAuth approval. Never request or record passwords,
tokens, cookies, or secrets. Do not automate ChatGPT account, personalization,
or permission settings.

Register the intended app (examples use `codex`) at the approved endpoint.
Change local-network policy only after explicit scoped consent, preserving
unrelated Chrome settings and profiles. If the UI or connection fails, diagnose
that specific failure; do not repeatedly recreate or refresh a working app.

## One access check

Verify endpoint reachability and one actual project read through the registered
app. HTTP 401 without authentication can establish endpoint reachability, but
cannot establish successful authenticated file access.

Use the normal temporary-chat flow. Explicitly select Latest, then the requested
effort (default Pro / 6 Pro). Automatically enable temporary-chat personalization before submission.
Oracle's current compatibility carrier is `gpt-5.6-sol` with
`model_strategy=current` and `thinking_time=pro`; it must click Latest, not the
numeric GPT-5.6 row. Do not invent `gpt-6` or `latest` CLI model identifiers.

Save the complete answer before closing the owned tab. A timeout retains that
same run; never automatically resend. Read access does not require a prescribed
tool sequence, audit nonce, three receipts, or a magic result marker. Report
missing access or incomplete results honestly.

Project rules reference the shared policy while retaining project-specific
tests and safety constraints. Source installation, connection setup, and a
successful actual read are distinct states; report the state actually reached.

See [managed DevSpace setup](DEVSPACE_TAILSCALE_SETUP.md) for helper details,
and [한국어](FIRST_INSTALL.md) for the Korean guide.
