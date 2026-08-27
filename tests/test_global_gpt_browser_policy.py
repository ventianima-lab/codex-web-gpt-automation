from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THINKING = ROOT / "skills" / "chatgpt-thinking-browser" / "SKILL.md"
PRO = ROOT / "skills" / "chatgpt-pro-browser" / "SKILL.md"
HANDOFF = ROOT / "skills" / "chatgpt-pro-plan-handoff" / "SKILL.md"
MULTI = ROOT / "skills" / "web-multi-gpt" / "SKILL.md"
RESEARCH = ROOT / "skills" / "chatgpt-deep-research-browser" / "SKILL.md"
ORACLE = ROOT / "skills" / "chatgpt-oracle-runtime" / "SKILL.md"
DESIGNER = ROOT / "skills" / "chatgpt-question-designer" / "SKILL.md"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_new_regular_modes_route_only_to_oracle_devspace() -> None:
    value = text(THINKING)
    assert "chatgpt_oracle_dispatch.py" in value
    assert "default `@codex`" in value
    assert "never attaches files" in value
    assert "create a new agbrowse run" in value
    assert "app picker" not in value.casefold()


def test_qualified_pro_requires_explicit_opt_in_and_uses_readonly_devspace() -> None:
    value = text(PRO)
    flat = " ".join(value.split())
    assert "Oracle is the only backend for a new Pro run" in flat
    assert "Pro is quota-limited" in flat
    assert "never infer Pro from task difficulty" in flat
    assert "exact absolute project root" in flat
    assert "read-only and limited to design, advice, or review" in flat
    assert "regular `GPT-5.6` `extra-high` DevSpace stage owns any required mutation or command" in flat
    assert "`pro-attachment` remains an explicit, read-only route" in flat
    assert "never an automatic fallback" in flat
    assert "There is no new agbrowse,\nCodexPro" in value
    handoff = text(HANDOFF)
    assert "allow_pro: true" in handoff
    assert "`pro-devspace-readonly`" in handoff
    assert "regular `GPT-5.6` `extra-high` DevSpace stage" in handoff
    assert "`pro-attachment`" in handoff
    assert "read-only immutable/external-evidence" in handoff
    assert "Persisted legacy `pro-devspace`" in handoff


def test_qualified_pro_has_exact_root_readonly_authority() -> None:
    value = text(PRO)
    flat = " ".join(value.split())
    assert "applicable `AGENTS.md` chain completely" in flat
    assert "Repository safety rules remain authoritative" in flat
    assert "must not create, edit, or remove files or run commands" in flat
    assert "must not change accounts, app settings, or external state" in flat


def test_qualified_pro_fails_closed_when_devspace_tools_are_not_exposed() -> None:
    value = text(PRO)
    flat = " ".join(value.split())
    assert "TASK_OUTCOME: EXECUTED|NOT_EXECUTED|BLOCKED" in flat
    assert "zero callable DevSpace tools" in flat
    assert "`NOT_EXECUTED`, never successful Pro work" in flat
    assert "at most one fresh retry with the same mission bytes and SHA-256" in flat
    assert "do not loop, manipulate ChatGPT app settings" in flat


def test_pro_requires_an_evidence_based_web_multi_decision_and_auto_handoff() -> None:
    value = text(PRO)
    assert "WEB_MULTI_NEEDED: YES|NO" in value
    assert "WEB_MULTI_REASON: evidence-based reason" in value
    assert "three to five materially independent" in value
    assert "ready-to-run Web Multi-GPT Very\nHigh mission" in value
    assert "same project maximum-context evidence and the durable Pro answer" in value
    assert "stable lane order, and synthesis/judge criteria" in value
    assert "automatically without a routine user\nchoice" in value
    assert "trivial, single-answer, or purely mechanical question" in value


def test_deep_research_uses_oracle_deep_without_silent_fallback() -> None:
    value = text(RESEARCH)
    assert "chatgpt_oracle_dispatch.py" in value
    assert "--mode deep-research" in value
    assert "--browser-research deep" in value
    assert '--reasoning-level "Very High"' in value
    assert "visible `Extra High`" in value
    assert "Do not silently replace Deep Research" in value


def test_web_multi_is_genuine_sessions_with_wave_cap_and_worktrees() -> None:
    value = text(MULTI)
    assert "chatgpt_oracle_multi.py" in value
    assert "waves of at most five" in value
    assert "worktree-write" in value
    assert "distinct pre-created worktree" in value
    assert "worktree-write" in value
    assert "all-lanes" in value
    assert "ancestor/descendant overlap" in value
    assert "single-GPT role simulation" in value


def test_comprehensive_is_web_native_relay_with_one_local_gate() -> None:
    value = text(HANDOFF)
    assert "chatgpt_oracle_comprehensive.py" in value
    assert "plan -> optional Pro or Oracle Web Multi -> review" in value
    assert "final web PASS plus a zero-exit local" in value
    assert "host validates" in value
    assert "never rewrites the semantic prompt" in value


def test_host_control_state_is_outside_devspace_project() -> None:
    value = text(ORACLE)
    assert "%USERPROFILE%\\.codex\\state\\chatgpt-oracle" in value
    source = text(ROOT / "bin" / "chatgpt_oracle_state.py")
    assert "HOST_STATE_OVERLAPS_PROJECT" in source


def test_oracle_recovery_is_exact_slug_no_restart_and_monotonic() -> None:
    value = text(THINKING)
    assert "stored slug" in value
    assert "never restarts/resubmits" in value
    assert "never downgrades durable COMPLETE" in value
    assert "exact persisted" in value
    assert "replacement" in value
    runtime = text(ORACLE)
    assert "`recovery_binding_unavailable`" in runtime
    assert "restore the\nexact persisted conversation URL" in runtime


def test_oracle_runs_use_isolated_profile_copies_and_owned_hidden_windows() -> None:
    value = text(THINKING)
    assert "throwaway" in value
    assert "per-run profile" in value
    assert "hide its owned window" in value


def test_install_inventory_contains_new_active_runtime_and_keeps_legacy_recovery() -> None:
    manifest = json.loads((ROOT / "install-manifest.json").read_text(encoding="utf-8"))
    include = set(manifest["include"])
    for path in (
        "bin/chatgpt_oracle_dispatch.py",
        "bin/chatgpt_oracle_multi.py",
        "bin/chatgpt_oracle_comprehensive.py",
        "bin/devspace-compat/1.0.8/oauth-refresh-replay.patch",
        "bin/devspace-compat/1.0.8/artifact-audit-readonly.patch",
        "bin/devspace-compat/1.0.8/workspace-write-and-read-bridge.patch",
        "bin/devspace-compat/1.0.8/tool-read-receipts.patch",
        "bin/devspace-compat/1.0.8/widget-domain.patch",
        "bin/devspace-compat/1.0.8/workspaces.patch",
        "bin/devspace-compat/1.0.7/tool-read-receipts.patch",
        "bin/devspace-compat/1.0.4/directory-read.patch",
        "bin/devspace-compat/1.0.4/delete-file.patch",
        "bin/devspace-compat/1.0.4/trash-file.patch",
        "bin/devspace-compat/1.0.4/directory-read-to-file-safety.patch",
        "bin/devspace-compat/1.0.4/file-safety-to-read-chunk.patch",
        "bin/oracle-compat/0.18.0/oracle-cli.followup-port-and-timeout.patch",
        "bin/oracle-compat/0.18.0/browserConfig.followup-port-binding.patch",
        "bin/oracle-compat/0.18.0/browserConfig.copy-profile-windows.patch",
        "upstream-runtime-policy.json",
        "scripts/check_upstream_runtime_policy.py",
        "skills/chatgpt-workspace-setup/SKILL.md",
    ):
        assert path in include
    assert "bin/chatgpt_agbrowse_run.py" in include
    assert manifest["routing"] == {
        "new_work_engine": "oracle",
        "regular_workspace_transport": "devspace",
        "pro_transport": "oracle-devspace-readonly-explicit",
        "historical_pro_devspace": "persisted-run-recovery-only",
        "pro_attachment_transport": "oracle-attachment-readonly-explicit",
        "agbrowse": "persisted-run-recovery-only",
        "codexpro": "persisted-run-recovery-only",
    }
    assert manifest["external"]["oracle"]["license"] == "MIT"
    assert manifest["external"]["oracle"]["tested_version"] == "0.18.0"
    assert manifest["external"]["oracle"]["last_known_good"]["version"] == "0.17.1"
    assert manifest["external"]["devspace"]["license"] == "MIT"
    assert manifest["external"]["devspace"]["tested_version"] == "1.0.8"
    assert manifest["external"]["devspace"]["last_known_good"]["version"] == "1.0.7"
    assert manifest["external"]["agbrowse"]["role"] == "persisted-run-recovery-only"
    assert manifest["external"]["agbrowse"]["default_install"] is False
    assert manifest["external"]["codexpro"]["frozen"] is True


def test_no_new_skill_routes_to_chrome_playwright_or_in_app_fallback() -> None:
    combined = "\n".join(text(path) for path in (THINKING, HANDOFF, MULTI, RESEARCH)).casefold()
    assert "@chrome" not in combined
    assert "falls back to\nagbrowse, playwright, in-app browser, or chrome" in combined


def test_readme_declares_manual_one_time_registration_not_ui_automation() -> None:
    value = text(ROOT / "README.md")
    assert "최초 한 번 수동 등록" in value
    assert "ChatGPT 설정·앱 목록·권한·삭제·선택 UI를 자동화하지 않습니다" in value
    assert "실행 신원으로 정확히 복구" in value
    assert "최초 설치 가이드" in value
    assert "ChatGPT 앱 `codex` 등록" in value


def test_english_readme_maps_modes_to_the_same_oracle_routes() -> None:
    value = text(ROOT / "README.en.md")
    assert "Oracle + DevSpace" in value
    assert "`orchestrator` / orchestrator" in value
    assert "`deep-research` / deep research" in value
    assert "comprehensive mode" in value
    assert "Web Multi-GPT" in value
    assert "Pro is quota-limited, never auto-selected" in value
    assert "Oracle + read-only DevSpace" in value
    assert "regular `GPT-5.6` `extra-high` DevSpace stage performs any file creation" in value
    assert "never resubmits the task" in value


def test_question_designer_cannot_route_new_work_through_codexpro_or_legacy_sessions() -> None:
    value = text(DESIGNER)
    assert "CodexPro is frozen for new work" in value
    assert "never design a new prompt around CodexPro" in value
    assert "Every new Oracle stage is a one-shot session" in value
    assert "Do not add legacy `session_policy`" in value
    assert "verified CodexPro live connector context remains the default" not in value


def test_agent_metadata_exposes_oracle_active_routes() -> None:
    thinking = text(ROOT / "skills" / "chatgpt-thinking-browser" / "agents" / "openai.yaml")
    multi = text(ROOT / "skills" / "web-multi-gpt" / "agents" / "openai.yaml")
    pro = text(ROOT / "skills" / "chatgpt-pro-browser" / "agents" / "openai.yaml")
    designer = text(ROOT / "skills" / "chatgpt-question-designer" / "agents" / "openai.yaml")
    runtime = text(ROOT / "skills" / "chatgpt-oracle-runtime" / "agents" / "openai.yaml")
    assert "Oracle and DevSpace" in thinking
    assert "parallel Oracle GPT sessions" in multi
    assert "read-only DevSpace" in pro
    assert "allow_implicit_invocation: false" in pro
    assert "read-only Pro" in designer and "read/write Pro" not in designer
    assert "read-only Pro" in runtime and "read/write Pro" not in runtime


def test_question_designer_preserves_explicit_readonly_attachment_route() -> None:
    value = text(DESIGNER)
    assert "A separately explicit `pro-attachment` request" in value
    assert "never an automatic fallback from DevSpace" in value
    assert "attachment evidence is a persisted-legacy recovery concern, not a new-work route" not in value


def test_standalone_pro_never_transitions_into_comprehensive_implementation() -> None:
    pro = text(PRO)
    assert "standalone read-only Pro conversation route" in pro
    assert "After each durable\nanswer it returns control to Codex and stops" in pro
    assert "only an explicit user request may\nadd another bounded round to that same conversation" in pro
    assert "never starts a\nreview-to-implementation chain" in pro
    assert "If the user asks for comprehensive mode, use `chatgpt-pro-plan-handoff`" in pro
