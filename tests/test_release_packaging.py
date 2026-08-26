import json
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

RETIRED_PATHS = {
    'bin/chatgpt_browser_runtime.py',
    'bin/chatgpt_browser_runtime_server.py',
    'bin/chatgpt_browser_runtime_worker.py',
    'bin/chatgpt_execution_evidence.py',
    'bin/chatgpt_mode_evidence.py',
    'bin/chatgpt_question_contract.py',
    'bin/chatgpt_rate_limit_modal_watcher.py',
    'bin/codexpro_connector_supervisor.ps1',
    'bin/codexpro_debug_create_submit.py',
    'bin/codexpro_developer_app_cdp.py',
    'bin/codexpro_developer_app_reconcile.mjs',
    'bin/codexpro_ensure_project_app.py',
    'bin/codexpro_ensure_project_app.ps1',
    'skills/chatgpt-pro-browser/scripts/export_result.py',
    'skills/chatgpt-pro-browser/scripts/official_search_evidence.py',
    'skills/chatgpt-pro-browser/scripts/profile_manager.py',
    'skills/chatgpt-pro-browser/scripts/recover_live_dom_transcript.py',
    'skills/chatgpt-pro-browser/scripts/report_writer.py',
    'skills/chatgpt-pro-browser/scripts/selectors.py',
    'skills/chatgpt-pro-browser/scripts/self_heal_repair_executor.py',
    'skills/chatgpt-pro-browser/scripts/self_heal_supervisor.py',
    'skills/chatgpt-pro-browser/scripts/self_heal_types.py',
    'skills/chatgpt-pro-browser/scripts/tab_lease_registry.py',
    'skills/chatgpt-pro-browser/scripts/utils.py',
    'skills/chatgpt-pro-browser/scripts/weblatch_sidecar.py',
    'skills/chatgpt-pro-browser/tests/test_chatgpt_browser_runtime.py',
    'skills/chatgpt-pro-browser/tests/test_chatgpt_mode_evidence.py',
    'skills/chatgpt-pro-browser/tests/test_codexpro_developer_app_ui_contract.py',
    'skills/chatgpt-pro-browser/tests/test_generated_image_capture.py',
    'skills/chatgpt-pro-browser/tests/test_mode_selection_helpers.py',
    'skills/chatgpt-pro-browser/tests/test_module_isolation.py',
    'skills/chatgpt-pro-browser/tests/test_profile_manager.py',
    'skills/chatgpt-pro-browser/tests/test_self_heal_supervisor.py',
    'skills/chatgpt-pro-browser/tests/test_weblatch_sidecar.py',
    'tests/test_chatgpt_execution_evidence.py',
}

def test_manifest_covers_runtime_and_schemas() -> None:
    manifest = json.loads((ROOT / 'install-manifest.json').read_text(encoding='utf-8'))
    assert manifest['schema'] == 'codexpro.install-manifest/v1'
    includes = set(manifest['include'])
    required = {
        'bin/chatgpt_agbrowse_bridge.py',
        'bin/chatgpt_agbrowse_run.py',
        'bin/chatgpt_web_multi_runtime.py',
        'bin/chatgpt_web_multi_upstream.py',
        'bin/codexpro_agbrowse_app.py',
        'bin/codexpro_fixed_runtime_watchdog.py',
        'bin/codexpro_harness.py',
        'bin/codexpro_cloudflared_launchd.py',
        'bin/codexpro_lifecycle.py',
        'bin/codexpro_macos_launchd.py',
        'bin/codexpro_posix_process.py',
        'bin/codexpro_project_cloudflare_bootstrap.ps1',
        'bin/codex_web_gpt_onboarding.py',
        'bin/codex_global_agents_setup.py',
        'docs/FIRST_INSTALL.md',
        'docs/ULTRA_ECONOMY_MODE.md',
        'bin/codex_runtime_identity.py',
        'docs/templates/codex-agents/scout.toml',
        'docs/templates/codex-agents/implementer.toml',
        'docs/templates/codex-agents/verifier.toml',
        'docs/templates/codex-agents/global-agents-policy.md',
        'skills/chatgpt-pro-browser/SKILL.md',
        'skills/chatgpt-pro-browser/agents/openai.yaml',
        'skills/chatgpt-pro-browser/scripts/build_project_context_packet.py',
        'skills/chatgpt-pro-browser/scripts/run_chatgpt_pro.py',
        'skills/chatgpt-pro-plan-handoff/scripts/run_pro_plan_handoff.py',
        'skills/chatgpt-pro-plan-handoff/schemas/*.json',
        'skills/ultra-economy-mode/SKILL.md',
        'skills/ultra-economy-mode/agents/openai.yaml',
        'scripts/run_v4_contract_tests.py',
        'scripts/run_harness_canary.py',
        'contracts/install/*.json',
        'contracts/gjc-interview-v1.schema.json',
        'marketplace/plugins/codexpro-harness/hooks/hooks.json',
        'tests/fixtures/planner-v7-app-trace-quiescent-incident.json',
        'tests/fixtures/planner-v8-app-trace-quiescent-incident.json',
    }
    assert required <= includes
    assert {
        'bin/oracle-compat/0.17.1/thinkingStatus.undetected-warning.patch',
        'bin/oracle-compat/0.17.1/thinkingStatus.undetected-warning.v1.19.2.patch',
    } <= includes
    assert not any('*' in path for path in includes if not (path.endswith('/schemas/*.json') or path == 'contracts/install/*.json'))
    package_files = set(json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['files'])
    assert {
        'README.md',
        'README.en.md',
        'CONTRIBUTING.md',
        'docs/',
        'skills/chatgpt-pro-browser/SKILL.md',
        'skills/chatgpt-pro-browser/agents/openai.yaml',
        'skills/chatgpt-pro-browser/scripts/build_project_context_packet.py',
        'skills/chatgpt-pro-browser/scripts/run_chatgpt_pro.py',
        'skills/chatgpt-pro-browser/scripts/run_pro_browser.py',
        'bin/codexpro_harness.py',
        'bin/codexpro_lifecycle.py',
        'marketplace/',
        'install.py',
        'doctor.py',
        'onboard.py',
    } <= package_files


def test_quiescent_app_trace_fixtures_never_authorize_replacement_work() -> None:
    expected = {
        'planner-v7-app-trace-quiescent-incident.json': ('v7', 'preserve the parent lock'),
        'planner-v8-app-trace-quiescent-incident.json': ('v8', 'exact persisted parent/child/session/target/canonical URL tuple'),
    }
    for filename, (planner_version, recovery_guard) in expected.items():
        fixture = json.loads((ROOT / 'tests' / 'fixtures' / filename).read_text(encoding='utf-8'))
        assert fixture['schema'] == 'codexpro.web-multi.app-trace-incident/v1'
        assert fixture['planner_version'] == planner_version
        assert fixture['state'] == 'quiescent'
        assert recovery_guard in fixture['expected_recovery']
        assert 'new' not in fixture['expected_recovery'].casefold()


def test_public_install_and_npm_surface_exclude_legacy_browser_engines() -> None:
    manifest = json.loads((ROOT / 'install-manifest.json').read_text(encoding='utf-8'))
    package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    install_paths = set(manifest['include'])
    package_paths = set(package['files'])
    assert RETIRED_PATHS.isdisjoint(install_paths)
    assert RETIRED_PATHS.isdisjoint(package_paths)
    assert {'bin/', 'skills/', 'bin/*.py', 'skills/**/scripts/*.py'}.isdisjoint(install_paths | package_paths)


def test_retired_automation_surface_is_absent_from_repository() -> None:
    assert not [path for path in RETIRED_PATHS if (ROOT / path).exists()]

def test_public_notices_and_no_vendoring() -> None:
    assert 'Copyright (c) 2026 ventianima-lab' in (ROOT / 'LICENSE').read_text(encoding='utf-8')
    notice = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    assert 'hehee9/multi-gpt@4f5e130' in notice and 'server.mjs' in notice
    assert 'missing' in notice and 'agbrowse@0.1.18' in notice
    assert not any((ROOT / name).exists() for name in ('node_modules', 'agbrowse', 'browser'))

def test_package_is_publishable_and_lockfile_matches() -> None:
    package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    assert package['private'] is False
    assert package['name'] == lock['name'] == lock['packages']['']['name']
    assert package['version'] == lock['version'] == lock['packages']['']['version']
    assert package['license'] == lock['packages']['']['license'] == 'MIT'
    assert package['repository']['url'] == 'git+https://github.com/ventianima-lab/codex-web-gpt-automation.git'
    assert package['homepage'].startswith('https://github.com/ventianima-lab/codex-web-gpt-automation')
    assert {
        'bin/chatgpt_agbrowse_bridge.py',
        'skills/chatgpt-thinking-browser/SKILL.md',
        'install.ps1',
        'LICENSE',
        'scripts/run_v4_contract_tests.py',
        'scripts/check_docs.py',
        'scripts/prepare_devspace_108_ci.py',
        'contracts/install/',
    } <= set(package['files'])


def test_release_workflow_installs_pytest_before_running_contract_runner() -> None:
    workflow = (ROOT / '.github/workflows/release-portability.yml').read_text(encoding='utf-8')
    install = workflow.index('python -m pip install "pytest>=8,<10"')
    run_tests = workflow.index('python scripts/run_v4_contract_tests.py --focused')
    assert install < run_tests


def test_release_workflow_runs_focused_and_full_contract_checks() -> None:
    workflow = (ROOT / '.github/workflows/release-portability.yml').read_text(encoding='utf-8')
    assert 'scripts/run_v4_contract_tests.py --focused' in workflow
    assert 'scripts/run_v3_contract_tests.py' in workflow
    assert 'scripts/run_v4_contract_tests.py --full' in workflow
    assert 'windows-latest' in workflow
    assert 'macos-14' in workflow
    assert 'scripts/prepare_devspace_108_ci.py' in workflow


def test_devspace_ci_preparer_pins_the_policy_archive_integrity() -> None:
    policy = json.loads((ROOT / "upstream-runtime-policy.json").read_text(encoding="utf-8"))
    helper = (ROOT / "scripts" / "prepare_devspace_108_ci.py").read_text(encoding="utf-8")
    expected = policy["runtimes"]["devspace"]["current"]["integrity"]
    assert f'DEVSPACE_INTEGRITY = "{expected}"' in helper


def test_manual_devspace_launch_docs_disable_optional_subagents_by_default() -> None:
    guide = (ROOT / "docs" / "FIRST_INSTALL.md").read_text(encoding="utf-8")
    managed_environment = guide[
        guide.index("DEVSPACE_TOOL_MODE=full") : guide.index(
            "임시 URL은 앱 등록 후 바뀌므로", guide.index("DEVSPACE_TOOL_MODE=full")
        )
    ]
    assert "아래 세 값을 유지합니다" in guide
    assert "DEVSPACE_OAUTH_SCOPES=devspace,offline_access" in managed_environment
    assert "DEVSPACE_SUBAGENTS=false" in managed_environment
    tailscale = (ROOT / "docs" / "DEVSPACE_TAILSCALE_SETUP.md").read_text(
        encoding="utf-8"
    )
    assert "`DEVSPACE_SUBAGENTS=false`" in tailscale
    assert "separately and explicitly approves" in tailscale


def test_bug_report_template_tracks_current_devspace_version() -> None:
    policy = json.loads((ROOT / "upstream-runtime-policy.json").read_text(encoding="utf-8"))
    current = policy["runtimes"]["devspace"]["current"]["version"]
    template = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug-report.yml").read_text(
        encoding="utf-8"
    )
    assert f"DevSpace {current}" in template


def test_devspace_ci_preparer_rejects_archive_escape_and_links(tmp_path: Path) -> None:
    path = ROOT / "scripts" / "prepare_devspace_108_ci.py"
    spec = importlib.util.spec_from_file_location("prepare_devspace_108_ci_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def archive(member: tarfile.TarInfo, content: bytes = b"") -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as package:
            member.size = len(content)
            package.addfile(member, io.BytesIO(content) if content else None)
        return stream.getvalue()

    traversal = tarfile.TarInfo("package/../escape.txt")
    with pytest.raises(RuntimeError, match="unsafe path"):
        module.extract_verified(archive(traversal, b"escape"), tmp_path / "traversal")

    link = tarfile.TarInfo("package/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "package.json"
    with pytest.raises(RuntimeError, match="non-file entry"):
        module.extract_verified(archive(link), tmp_path / "link")


def test_tag_push_workflow_publishes_only_validated_annotated_release() -> None:
    workflow = (ROOT / '.github/workflows/publish-release.yml').read_text(encoding='utf-8')
    assert 'tags:' in workflow and '"v*"' in workflow
    assert 'workflow_dispatch:' in workflow
    assert 'ref: ${{ inputs.tag || github.ref }}' in workflow
    assert 'contents: write' in workflow
    assert 'test "${tag}" = "v${version}"' in workflow
    assert 'git fetch --force origin "refs/tags/${tag}:refs/tags/${tag}"' in workflow
    assert 'git cat-file -t "refs/tags/${tag}"' in workflow
    assert 'git rev-parse HEAD' in workflow and 'git rev-list -n 1' in workflow
    assert 'scripts/check_docs.py --root .' in workflow
    assert 'actions: read' in workflow
    assert 'pull-requests: read' in workflow
    assert 'Require reviewed validation PR and exact-commit portability CI' in workflow
    assert 'release-portability.yml/runs?head_sha=${release_commit}&event=push' in workflow
    assert '.head_branch == "main"' in workflow
    assert '.conclusion == "success"' in workflow
    assert 'commits/${release_commit}/pulls?per_page=100' in workflow
    assert '.merge_commit_sha == $sha' in workflow
    assert '.commit_id == $sha' in workflow
    assert '.state == "CHANGES_REQUESTED"' in workflow
    assert '.state == "APPROVED"' in workflow
    assert '.state == "COMMENTED"' in workflow
    assert 'INDEPENDENT_REVIEW: PASS' in workflow
    assert 'gsub("^\\\\s+|\\\\s+$"; "") | length) >= 40' in workflow
    assert 'timeout-minutes: 50' in workflow
    assert 'gh release create "${RELEASE_TAG}" --verify-tag --generate-notes' in workflow
    assert 'releases/tags/${RELEASE_TAG}' in workflow


def test_update_guard_never_confuses_version_bump_with_published_release() -> None:
    skill = (ROOT / 'skills/mcp-update-guard/SKILL.md').read_text(encoding='utf-8')
    checklist = (ROOT / 'docs/RELEASE_CHECKLIST.md').read_text(encoding='utf-8')
    for text in (skill, checklist):
        assert 'release incomplete' in text
        assert 'releases/latest' in text
        assert 'peeled remote tag' in text
        assert 'source/install' in text
    assert 'A version bump is only release metadata preparation' in skill
    assert 'Never call a version bump, commit, push, or successful branch CI' in skill


def test_readme_release_badges_use_published_tags() -> None:
    for name in ('README.md', 'README.en.md'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert '/releases/latest' in text
        assert 'img.shields.io/github/v/tag/ventianima-lab/codex-web-gpt-automation' in text
        assert 'img.shields.io/github/v/release/ventianima-lab/codex-web-gpt-automation' not in text


def test_rebrand_keeps_legacy_plugin_id_but_updates_human_facing_names() -> None:
    plugin = json.loads(
        (ROOT / 'marketplace/plugins/codexpro-harness/.codex-plugin/plugin.json').read_text(encoding='utf-8')
    )
    hooks = (ROOT / 'marketplace/plugins/codexpro-harness/hooks/hooks.json').read_text(encoding='utf-8')
    skill = (ROOT / 'marketplace/plugins/codexpro-harness/skills/codexpro-ultrawork/SKILL.md').read_text(encoding='utf-8')
    assert plugin['name'] == 'codexpro-harness'
    assert plugin['interface']['displayName'] == 'Codex Web GPT Harness'
    assert 'CodexPro' not in plugin['description']
    assert 'CodexPro' not in hooks
    assert '# Codex Web GPT Ultrawork Router' in skill
