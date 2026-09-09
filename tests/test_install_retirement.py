from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")


def seed_repo_and_home(tmp_path: Path, *, modified: bool = False) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(ROOT / "install.ps1", repo / "install.ps1")
    shutil.copy2(ROOT / "rollback.ps1", repo / "rollback.ps1")
    current = repo / "bin" / "current.py"
    current.parent.mkdir()
    current.write_bytes(b"current\n")
    relative = "skills/legacy-mode/SKILL.md"
    (repo / "install-manifest.json").write_text(
        json.dumps({
            "schema": "codexpro.install-manifest/v1",
            "version": "test",
            "include": ["bin/current.py"],
            "retire": {"receipt_owned_files": [relative]},
            "optional_components": {"local_multi_gpt": {"include": [], "default_install": False}},
        }),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    retired = home / relative
    retired.parent.mkdir(parents=True)
    retired.write_bytes(b"locally modified\n" if modified else b"old managed skill\n")
    prior_backup = home / "backups" / "prior"
    prior_backup.mkdir(parents=True)
    prior_receipt = home / "receipts" / "codexpro-automation-prior.json"
    prior_receipt.parent.mkdir(parents=True)
    prior_receipt.write_text(json.dumps({
        "schema": "codexpro.install-receipt/v3",
        "backup": str(prior_backup),
        "files": [{
            "path": relative,
            "action": "created",
            "installed_sha256": hashlib.sha256(b"old managed skill\n").hexdigest(),
            "backup_sha256": None,
        }],
    }), encoding="utf-8")
    return repo, home, retired


def run_script(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell lifecycle compatibility runs on Windows")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path), *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def test_powershell_retirement_and_rollback_are_exact_inverse(tmp_path: Path) -> None:
    repo, home, retired = seed_repo_and_home(tmp_path)

    installed = run_script(
        repo / "install.ps1", "-CodexHome", str(home), "-SkipDependencyInstall", "-DisableLocalMultiGpt"
    )

    assert installed.returncode == 0, installed.stderr
    assert not retired.exists()
    receipt_path = max((home / "receipts").glob("codexpro-automation-*.json"), key=lambda path: path.stat().st_mtime_ns)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    record = next(item for item in receipt["files"] if item["path"] == "skills/legacy-mode/SKILL.md")
    assert record["action"] == "retired"
    assert record["retired_sha256"] == record["backup_sha256"]

    rolled_back = run_script(
        repo / "rollback.ps1", "-CodexHome", str(home), "-Receipt", str(receipt_path)
    )
    assert rolled_back.returncode == 0, rolled_back.stderr
    assert retired.read_bytes() == b"old managed skill\n"


def test_powershell_modified_retirement_conflicts_before_install_mutation(tmp_path: Path) -> None:
    repo, home, retired = seed_repo_and_home(tmp_path, modified=True)

    installed = run_script(
        repo / "install.ps1", "-CodexHome", str(home), "-SkipDependencyInstall", "-DisableLocalMultiGpt"
    )

    assert installed.returncode != 0
    assert "RETIREMENT_CONFLICT" in installed.stderr
    assert retired.read_bytes() == b"locally modified\n"
    assert not (home / "bin" / "current.py").exists()


def test_powershell_retirement_conflict_precedes_all_rollback_mutation(tmp_path: Path) -> None:
    repo, home, retired = seed_repo_and_home(tmp_path)
    installed = run_script(repo / "install.ps1", "-CodexHome", str(home), "-SkipDependencyInstall", "-DisableLocalMultiGpt")
    assert installed.returncode == 0, installed.stderr
    receipt_path = max((home / "receipts").glob("codexpro-automation-*.json"), key=lambda path: path.stat().st_mtime_ns)
    current = home / "bin" / "current.py"
    original_current = current.read_bytes()
    retired.write_bytes(b"user recreated file\n")
    result = run_script(repo / "rollback.ps1", "-CodexHome", str(home), "-Receipt", str(receipt_path))
    assert result.returncode == 2, result.stderr
    assert current.read_bytes() == original_current
    assert retired.read_bytes() == b"user recreated file\n"


def test_powershell_retired_restore_is_idempotent(tmp_path: Path) -> None:
    repo, home, retired = seed_repo_and_home(tmp_path)
    installed = run_script(repo / "install.ps1", "-CodexHome", str(home), "-SkipDependencyInstall", "-DisableLocalMultiGpt")
    assert installed.returncode == 0, installed.stderr
    receipt_path = max((home / "receipts").glob("codexpro-automation-*.json"), key=lambda path: path.stat().st_mtime_ns)
    for _ in range(2):
        result = run_script(repo / "rollback.ps1", "-CodexHome", str(home), "-Receipt", str(receipt_path))
        assert result.returncode == 0, result.stderr
        assert retired.read_bytes() == b"old managed skill\n"
