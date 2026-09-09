from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")
PRESERVED_PATHS = (
    "skills/mcp-update-guard/SKILL.md",
    "skills/mcp-update-guard/agents/openai.yaml",
)
NORMAL_PATH = "bin/normal.txt"
ALL_PATHS = (*PRESERVED_PATHS, NORMAL_PATH)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def seed_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(ROOT / "install.ps1", repo / "install.ps1")
    for relative in ALL_PATHS:
        _write(repo / relative, f"package:{relative}\n".encode())
    (repo / "install-manifest.json").write_text(
        json.dumps(
            {
                "schema": "codexpro.install-manifest/v1",
                "version": "preserve-test",
                "include": list(ALL_PATHS),
                "retire": {"receipt_owned_files": []},
                "optional_components": {
                    "local_multi_gpt": {"include": [], "default_install": False}
                },
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    for relative in ALL_PATHS:
        _write(home / relative, f"local:{relative}\n".encode())
    return repo, home


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_installer(
    repo: Path,
    home: Path,
    *,
    preserve: tuple[str, ...] = (),
    what_if: bool = False,
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell installer compatibility runs on Windows")
    command = (
        f"& {_ps_literal(str(repo / 'install.ps1'))} "
        f"-CodexHome {_ps_literal(str(home))} "
        "-SkipDependencyInstall -DisableLocalMultiGpt"
    )
    if preserve:
        values = ",".join(_ps_literal(value) for value in preserve)
        command += f" -PreserveExistingPath @({values})"
    if what_if:
        command += " -WhatIf"
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def latest_receipt(home: Path) -> tuple[Path, dict[str, object]]:
    path = max(
        (home / "receipts").glob("codexpro-automation-*.json"),
        key=lambda candidate: candidate.stat().st_mtime_ns,
    )
    return path, json.loads(path.read_text(encoding="utf-8-sig"))


def assert_no_installer_writes(home: Path, before: dict[str, bytes]) -> None:
    assert {relative: (home / relative).read_bytes() for relative in before} == before
    assert not (home / "receipts").exists()
    assert not (home / "backups").exists()


def test_preserves_explicit_files_separately_and_installs_normal_file(tmp_path: Path) -> None:
    repo, home = seed_fixture(tmp_path)
    preserved_before = {relative: (home / relative).read_bytes() for relative in PRESERVED_PATHS}

    result = run_installer(repo, home, preserve=PRESERVED_PATHS)

    assert result.returncode == 0, result.stderr
    assert {relative: (home / relative).read_bytes() for relative in PRESERVED_PATHS} == preserved_before
    assert (home / NORMAL_PATH).read_bytes() == (repo / NORMAL_PATH).read_bytes()
    _, receipt = latest_receipt(home)
    expected_preserved = [
        {
            "path": relative,
            "preserved_sha256": hashlib.sha256(preserved_before[relative]).hexdigest(),
            "reason": "explicit-local-override",
        }
        for relative in sorted(PRESERVED_PATHS)
    ]
    assert receipt["preserved_files"] == expected_preserved
    assert [record["path"] for record in receipt["files"]] == [NORMAL_PATH]
    journal = json.loads(Path(receipt["wal"]).read_text(encoding="utf-8-sig"))
    assert [record["path"] for record in journal["files"]] == [NORMAL_PATH]
    backup = Path(receipt["backup"])
    assert all(not (backup / relative).exists() for relative in PRESERVED_PATHS)


@pytest.mark.parametrize("kind", ["traversal", "rooted", "wildcard", "unknown"])
def test_invalid_paths_fail_before_destination_mutation(tmp_path: Path, kind: str) -> None:
    repo, home = seed_fixture(tmp_path)
    before = {relative: (home / relative).read_bytes() for relative in ALL_PATHS}
    invalid = {
        "traversal": "../outside.txt",
        "rooted": str((home / PRESERVED_PATHS[0]).resolve()),
        "wildcard": "skills/*/SKILL.md",
        "unknown": "bin/unknown.txt",
    }[kind]
    if kind == "unknown":
        _write(home / invalid, b"existing but unmanaged\n")

    result = run_installer(repo, home, preserve=(PRESERVED_PATHS[0], invalid))

    assert result.returncode != 0
    assert_no_installer_writes(home, before)


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_non_file_or_missing_target_fails_before_destination_mutation(
    tmp_path: Path, kind: str
) -> None:
    repo, home = seed_fixture(tmp_path)
    target = home / PRESERVED_PATHS[0]
    target.unlink()
    if kind == "directory":
        target.mkdir()
    before = {relative: (home / relative).read_bytes() for relative in ALL_PATHS[1:]}

    result = run_installer(repo, home, preserve=(PRESERVED_PATHS[0],))

    assert result.returncode != 0
    assert_no_installer_writes(home, before)


@pytest.mark.skipif(os.name != "nt", reason="junction reparse-point test is Windows-only")
def test_reparse_target_fails_before_destination_mutation(tmp_path: Path) -> None:
    repo, home = seed_fixture(tmp_path)
    target = home / PRESERVED_PATHS[0]
    target.unlink()
    junction_target = tmp_path / "junction-target"
    junction_target.mkdir()
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(junction_target)],
        text=True,
        capture_output=True,
    )
    assert created.returncode == 0, created.stderr
    before = {relative: (home / relative).read_bytes() for relative in ALL_PATHS[1:]}

    result = run_installer(repo, home, preserve=(PRESERVED_PATHS[0],))

    assert result.returncode != 0
    assert "symlink/reparse point" in result.stderr
    assert_no_installer_writes(home, before)


def test_default_install_still_manages_every_manifest_file(tmp_path: Path) -> None:
    repo, home = seed_fixture(tmp_path)
    preserved = run_installer(repo, home, preserve=PRESERVED_PATHS)
    assert preserved.returncode == 0, preserved.stderr

    result = run_installer(repo, home)

    assert result.returncode == 0, result.stderr
    assert all((home / relative).read_bytes() == (repo / relative).read_bytes() for relative in ALL_PATHS)
    _, receipt = latest_receipt(home)
    assert receipt["preserved_files"] == []
    assert {record["path"] for record in receipt["files"]} == set(ALL_PATHS)


def test_whatif_reports_preservation_without_writes(tmp_path: Path) -> None:
    repo, home = seed_fixture(tmp_path)
    before = {relative: (home / relative).read_bytes() for relative in ALL_PATHS}

    result = run_installer(repo, home, preserve=PRESERVED_PATHS, what_if=True)

    assert result.returncode == 0, result.stderr
    for relative in PRESERVED_PATHS:
        assert f"Would preserve existing user-owned file {relative}" in result.stdout
        assert f"Would stage and install {relative}" not in result.stdout
    assert f"Would stage and install {NORMAL_PATH}" in result.stdout
    assert_no_installer_writes(home, before)


def test_pending_wal_intersection_fails_without_recovery_mutation(tmp_path: Path) -> None:
    repo, home = seed_fixture(tmp_path)
    relative = PRESERVED_PATHS[0]
    preserved_hash = hashlib.sha256((home / relative).read_bytes()).hexdigest()
    journal_path = home / "backups" / "incomplete" / "install.wal.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(
        json.dumps(
            {
                "schema": "codexpro.install-wal/v1",
                "status": "ACTIVE",
                "backup": str(journal_path.parent),
                "files": [
                    {
                        "path": relative,
                        "action": "created",
                        "installed_sha256": preserved_hash,
                        "backup_sha256": None,
                        "phase": "MUTATED",
                        "transitions": ["INTENT", "MUTATED"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = {
        path.relative_to(home).as_posix(): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }

    result = run_installer(repo, home, preserve=(relative,))

    assert result.returncode != 0
    assert "INSTALL_PRESERVATION_PENDING_RECOVERY_CONFLICT" in result.stderr
    assert relative in result.stderr
    after = {
        path.relative_to(home).as_posix(): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (home / "receipts").exists()
