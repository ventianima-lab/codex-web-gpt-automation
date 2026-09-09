from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_portable_lifecycle_is_exact_inverse(tmp_path: Path) -> None:
    module = load("portable_lifecycle_test", ROOT / "bin" / "codexpro_lifecycle.py")
    codex_home = tmp_path / "codex"
    prior = codex_home / "bin" / "chatgpt_oracle_state.py"
    prior.parent.mkdir(parents=True)
    prior.write_bytes(b"user-owned-before\n")

    plan = module.install(ROOT, codex_home, dry_run=True)
    assert plan["ok"] and "bin/codexpro_harness.py" in plan["files"]
    assert not (codex_home / "receipts").exists()

    installed = module.install(ROOT, codex_home)
    assert installed["ok"] and installed["count"] > 120
    receipt = Path(installed["receipt"])
    runtime_command = [str(tmp_path / "runtime" / "node"), str(tmp_path / "runtime" / "oracle-cli.js")]
    probes = []

    def runtime_probe(command, **kwargs):
        assert command == [*runtime_command, "--version"]
        assert kwargs["timeout"] == module.ORACLE_VERSION_PROBE_TIMEOUT_SECONDS
        assert kwargs["stdin"] == subprocess.DEVNULL
        probes.append(command)
        return subprocess.CompletedProcess(command, 0, "oracle 0.18.0\n", "")

    diagnosis = module.doctor(
        codex_home, oracle_resolver=lambda: runtime_command, oracle_run_factory=runtime_probe,
    )
    assert diagnosis["status"] == "PASS", diagnosis
    assert probes == [[*runtime_command, "--version"]]

    rolled_back = module.rollback(codex_home, receipt)
    assert rolled_back == {"ok": True, "status": "COMPLETE", "receipt": str(receipt), "conflicts": []}
    assert prior.read_bytes() == b"user-owned-before\n"
    assert not (codex_home / "bin" / "codexpro_harness.py").exists()


def test_portable_rollback_preserves_modified_managed_file(tmp_path: Path) -> None:
    module = load("portable_lifecycle_conflict_test", ROOT / "bin" / "codexpro_lifecycle.py")
    codex_home = tmp_path / "codex"
    installed = module.install(ROOT, codex_home)
    managed = codex_home / "bin" / "codexpro_harness.py"
    managed.write_text("user changed\n", encoding="utf-8")

    result = module.rollback(codex_home, Path(installed["receipt"]))

    assert result["ok"] is False
    assert any(item["path"] == "bin/codexpro_harness.py" for item in result["conflicts"])
    assert managed.read_text(encoding="utf-8") == "user changed\n"


def test_portable_receipt_rejects_external_backup(tmp_path: Path) -> None:
    module = load("portable_lifecycle_forgery_test", ROOT / "bin" / "codexpro_lifecycle.py")
    codex_home = tmp_path / "codex"
    receipt = codex_home / "receipts" / "codexpro-automation-forged.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"schema": module.RECEIPT_SCHEMA, "backup": str(tmp_path / "outside"), "files": []}), encoding="utf-8")

    try:
        module.rollback(codex_home, receipt)
    except module.LifecycleError as exc:
        assert "backup must be owned" in str(exc)
    else:
        raise AssertionError("forged receipt was accepted")


def test_optional_component_prompt_follows_korean_and_english_locale() -> None:
    module = load("portable_lifecycle_locale_test", ROOT / "bin" / "codexpro_lifecycle.py")
    optional = json.loads((ROOT / "install-manifest.json").read_text(encoding="utf-8"))["optional_components"]["local_multi_gpt"]

    assert module.localized_optional_prompt(optional, {"LANG": "ko_KR.UTF-8"}) == optional["prompt_ko"]
    assert module.localized_optional_prompt(optional, {"LANG": "en_US.UTF-8"}) == optional["prompt_en"]
    assert module.localized_optional_prompt(optional, {"CODEX_ONBOARDING_LANG": "ko"}) == optional["prompt_ko"]


def test_optional_component_prompt_uses_system_ui_locale(monkeypatch) -> None:
    module = load("portable_lifecycle_system_locale_test", ROOT / "bin" / "codexpro_lifecycle.py")
    optional = json.loads((ROOT / "install-manifest.json").read_text(encoding="utf-8"))["optional_components"]["local_multi_gpt"]
    for name in ("CODEX_ONBOARDING_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module.locale_module, "getlocale", lambda: ("ko_KR", "UTF-8"))

    assert module.localized_optional_prompt(optional) == optional["prompt_ko"]

    monkeypatch.setenv("CODEX_ONBOARDING_LANG", "en")
    assert module.localized_optional_prompt(optional) == optional["prompt_en"]


def test_windows_doctor_accepts_the_active_python_executable(monkeypatch) -> None:
    module = load("portable_lifecycle_windows_python_test", ROOT / "bin" / "codexpro_lifecycle.py")
    real_which = module.shutil.which

    def without_python_commands(name: str):
        if name in {"python3", "python", "py"}:
            return None
        return real_which(name)

    monkeypatch.setattr(module.shutil, "which", without_python_commands)

    result = module._resolve_python_tool(platform_name="nt", active_executable=sys.executable)

    assert result == str(Path(sys.executable).resolve())


def retirement_repo(tmp_path: Path, retired_path: str) -> Path:
    repo = tmp_path / "repo"
    current = repo / "bin" / "current.py"
    current.parent.mkdir(parents=True)
    current.write_text("current\n", encoding="utf-8")
    (repo / "install-manifest.json").write_text(
        json.dumps({
            "schema": "codexpro.install-manifest/v1",
            "version": "test",
            "include": ["bin/current.py"],
            "retire": {"receipt_owned_files": [retired_path]},
        }),
        encoding="utf-8",
    )
    return repo


def seed_owned_retirement(module, codex_home: Path, relative: str, content: bytes) -> Path:
    destination = codex_home / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(content)
    backup = codex_home / "backups" / "prior"
    backup.mkdir(parents=True)
    receipt = codex_home / "receipts" / "codexpro-automation-prior.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "schema": module.RECEIPT_SCHEMA,
        "backup": str(backup),
        "files": [{
            "path": relative,
            "action": "created",
            "installed_sha256": module.sha256_file(destination),
            "backup_sha256": None,
        }],
    }), encoding="utf-8")
    return destination


def test_receipt_owned_unchanged_file_is_retired_and_rollback_restores_it(tmp_path: Path) -> None:
    module = load("portable_lifecycle_retirement_test", ROOT / "bin" / "codexpro_lifecycle.py")
    relative = "skills/legacy-mode/SKILL.md"
    repo = retirement_repo(tmp_path, relative)
    codex_home = tmp_path / "codex"
    destination = seed_owned_retirement(module, codex_home, relative, b"old managed skill\n")

    installed = module.install(repo, codex_home)

    assert installed["retired"] == [relative]
    assert not destination.exists()
    receipt = json.loads(Path(installed["receipt"]).read_text(encoding="utf-8"))
    record = next(item for item in receipt["files"] if item["path"] == relative)
    assert record["action"] == "retired"
    assert record["retired_sha256"] == record["backup_sha256"]

    health = module.doctor(codex_home, oracle_resolver=lambda: [sys.executable],
                           oracle_run_factory=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "0.18.0\n", ""))
    assert not [issue for issue in health["issues"] if issue.get("path") == relative]

    result = module.rollback(codex_home, Path(installed["receipt"]))
    assert result["ok"] is True
    assert destination.read_bytes() == b"old managed skill\n"


@pytest.mark.parametrize("owned", [False, True])
def test_unowned_or_modified_retirement_conflicts_before_install_mutation(tmp_path: Path, owned: bool) -> None:
    module = load(f"portable_lifecycle_retirement_conflict_{owned}", ROOT / "bin" / "codexpro_lifecycle.py")
    relative = "skills/legacy-mode/SKILL.md"
    repo = retirement_repo(tmp_path, relative)
    codex_home = tmp_path / "codex"
    if owned:
        destination = seed_owned_retirement(module, codex_home, relative, b"old managed skill\n")
        destination.write_bytes(b"locally modified\n")
    else:
        destination = codex_home / relative
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"user owned\n")

    with pytest.raises(module.LifecycleError, match="RETIREMENT_CONFLICT"):
        module.install(repo, codex_home)

    assert destination.exists()
    assert not (codex_home / "bin" / "current.py").exists()
    assert not list((codex_home / "backups").glob("codexpro-automation-*"))


def test_rollback_preserves_path_recreated_after_retirement(tmp_path: Path) -> None:
    module = load("portable_lifecycle_retirement_recreated_test", ROOT / "bin" / "codexpro_lifecycle.py")
    relative = "skills/legacy-mode/SKILL.md"
    repo = retirement_repo(tmp_path, relative)
    codex_home = tmp_path / "codex"
    destination = seed_owned_retirement(module, codex_home, relative, b"old managed skill\n")
    installed = module.install(repo, codex_home)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"new user file\n")

    health = module.doctor(codex_home, oracle_resolver=lambda: [sys.executable],
                           oracle_run_factory=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "0.18.0\n", ""))
    assert {"code": "RETIRED_FILE_REAPPEARED", "path": relative} in health["issues"]

    result = module.rollback(codex_home, Path(installed["receipt"]))

    assert result["ok"] is False
    assert {"path": relative, "action": "preserved_recreated_retired_path"} in result["conflicts"]
    assert destination.read_bytes() == b"new user file\n"
    assert (codex_home / "bin" / "current.py").read_text(encoding="utf-8").splitlines() == ["current"]
