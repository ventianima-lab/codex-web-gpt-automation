from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_compat.py"


def load_compat():
    name = "chatgpt_oracle_compat_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_exact_version_patch_is_hash_gated_idempotent_and_backed_up(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.16.1"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n"
        "--- a/sample.txt\n"
        "+++ b/sample.txt\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    compat.patch_root = lambda: patches
    backup = tmp_path / "backup"

    first = compat.ensure_oracle_compatibility("oracle 0.16.1", package_root=package, backup_root=backup)
    second = compat.ensure_oracle_compatibility("oracle 0.16.1", package_root=package, backup_root=backup)

    assert first["changed"] == ["sample.txt"]
    assert second["already_patched"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_unknown_oracle_version_or_file_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    with pytest.raises(compat.OracleCompatError) as version:
        compat.ensure_oracle_compatibility("oracle 0.17.0", package_root=tmp_path)
    assert version.value.code == "ORACLE_VERSION_UNVALIDATED"

    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.16.1"}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"unknown\n")
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    with pytest.raises(compat.OracleCompatError) as mismatch:
        compat.ensure_oracle_compatibility("oracle 0.16.1", package_root=package)
    assert mismatch.value.code == "ORACLE_FILE_HASH_MISMATCH"


def test_all_matching_npx_cache_roots_are_patched_and_legacy_is_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compat = load_compat()
    roots = [tmp_path / "cache-new", tmp_path / "cache-old"]
    for root in roots:
        root.mkdir()
        (root / "package.json").write_text(json.dumps({"version": "0.16.1"}), encoding="utf-8")
    (roots[0] / "sample.txt").write_bytes(b"before\n")
    (roots[1] / "sample.txt").write_bytes(b"legacy\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n"
        "--- a/sample.txt\n"
        "+++ b/sample.txt\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
            "legacy_patched": [digest(b"legacy\n")],
        }
    }
    compat.patch_root = lambda: patches
    monkeypatch.setattr(compat, "_candidate_roots", lambda: roots)
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "sample.txt").write_bytes(b"before\n")

    result = compat.ensure_oracle_compatibility("oracle 0.16.1", backup_root=backup)

    assert result["package_roots"] == [str(root) for root in roots]
    assert all((root / "sample.txt").read_bytes() == b"after\n" for root in roots)
    assert len(result["changed"]) == 2


def test_prompt_composer_app_pill_probe_uses_the_composer_form_scope() -> None:
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / "promptComposer.patch"
    ).read_text(encoding="utf-8")

    assert "root.closest('form') || root.parentElement || root" in patch
    assert "scope.querySelectorAll(" in patch
    assert "target.click();" in patch
    assert "group.querySelectorAll('*')" in patch
    assert "if (pill) return true;" in patch
    assert "return !Array.from(document.querySelectorAll(" in patch
    assert "App mention confirmation diagnostic:" in patch
    assert 'logDomFailure(runtime, logger, "app-mention-pill-missing")' in patch
    assert "diagnostic.result?.value ?? null" in patch
    assert "__oracleAppApprovalWatcher" in patch
    assert "이 대화에 기억" in patch
    assert "remember for this chat" in patch
    assert "allowLabels.has" in patch


def test_app_mention_ui_observation_is_a_warning_not_a_hard_block() -> None:
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / "promptComposer.patch"
    ).read_text(encoding="utf-8")

    # The app is routed by the literal @name text in the submitted prompt, so an
    # unobservable suggestion overlay or pill must not fail the run.
    for removed in (
        'BrowserAutomationError("ChatGPT app mention suggestion did not appear."',
        'BrowserAutomationError("Exact ChatGPT app suggestion could not be clicked."',
        "BrowserAutomationError(`ChatGPT app mention was not confirmed in the composer",
    ):
        assert removed not in patch

    assert "let mentionUiConfirmed = true;" in patch
    assert patch.count("mentionUiConfirmed = false;") == 3
    assert "was sent as literal text without UI confirmation" in patch
    assert "confirmed in the composer.`" in patch


def test_model_selection_verifies_the_family_row_and_defers_effort_to_thinking_time() -> None:
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / "modelSelection.patch"
    ).read_text(encoding="utf-8")

    # The 2026 picker exposes "GPT-5.6 Sol" as a family row whose children are
    # the selectable Medium/High/Extra High effort rows.  Requiring an exact
    # selectable label named after the model made every run fail before the
    # composer, so the family row now verifies the model and the separate
    # thinking-time step chooses the effort tier.
    assert "matchedVisibleSolFamily" in patch
    assert "versionFromLabel(match.normalizedText) === desiredVersion" in patch
    assert "aria-haspopup" in patch
    assert "resolve({ status: 'already-selected', label: match.label })" in patch
    assert "Medium/High/Extra High" in patch


def test_gpt56_heavy_selection_is_extra_high_verified_and_fails_closed() -> None:
    compat = load_compat()
    contract = compat.PATCHES["dist/src/browser/actions/thinkingTime.js"]
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / contract["patch"]
    ).read_text(encoding="utf-8")

    assert "strictRegularExtraHigh" in patch
    assert "requested=Very High resolved=Extra High verified=yes" in patch
    assert "refusing to submit without confirmed Extra High" in patch
    assert "'매우 높음'" in patch
    assert "\\\\uac00-\\\\ud7a3" in patch
    assert contract["pristine"] == "7d475ed81ccee29a5b4107ed166584bcd3b0266bfd25e02ca7743bf24301e7f0"
    assert contract["patched"] == "f526acb4d187b9833f423832e2ed9c2f001c0424e78af574da050ea50df0474a"


def test_deep_research_does_not_bypass_thinking_time_verification() -> None:
    compat = load_compat()
    contract = compat.PATCHES["dist/src/browser/index.js"]
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / contract["patch"]
    ).read_text(encoding="utf-8")

    assert patch.count("+        if (thinkingTime)") == 2
    assert patch.count("-        if (thinkingTime && !deepResearch)") == 2
    assert "including Deep Research" in patch
    assert contract["legacy_patched"] == [
        "9168df2b3e8c4d1c962d05b198ceab1a9df9e50c7573453673212905e2bc5eba"
    ]
    assert contract["patched"] == "bf9097d613baadc7b04f4bed6670857bd7c50a584289fbbf1e65a8ec962bca8c"


def test_copy_profile_recovery_patch_reuses_only_the_persisted_profile_seed() -> None:
    compat = load_compat()
    contract = compat.PATCHES["dist/src/browser/recoverConversation.js"]
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / contract["patch"]
    ).read_text(encoding="utf-8")

    assert "resolved.copyProfileSource" in patch
    assert "return copyProfileSource.trim();" in patch
    assert 'mkdtemp(path.join(os.tmpdir(), "oracle-recovery-"))' in patch
    assert "wrapEphemeralRecoveryChrome" in patch
    assert contract["pristine"] == "8c7d841bc078af20c8922ec435f62e00df7a40605583fbd89334696b3ddb386b"
    assert contract["patched"] == "650ffe9bdbbaf799510e8cacaa8ba8407322bbbb175e790a3cf7777fa14772fe"


def test_hidden_window_patch_supports_windows_without_headless_mode() -> None:
    compat = load_compat()
    contract = compat.PATCHES["dist/src/browser/chromeLifecycle.js"]
    patch = (
        MODULE_PATH.parent
        / "oracle-compat"
        / "0.16.1"
        / contract["patch"]
    ).read_text(encoding="utf-8")

    assert 'process.platform === "win32"' in patch
    assert "--window-position=-32000,-32000" in patch
    assert contract["pristine"] == "9eaffd8264051266581548ea9dbee1152bd94b7a6032ed0441b1ba3c11c5b5e9"
    assert contract["patched"] == "d852372c9c16c9a130a280001e62312542092b0c38397907897217f8af0c559d"
