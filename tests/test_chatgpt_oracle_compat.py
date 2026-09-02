from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_compat.py"
CI_PREP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_oracle_018_ci.py"


def load_compat():
    name = "chatgpt_oracle_compat_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_ci_prepare():
    name = "prepare_oracle_018_ci_test"
    spec = importlib.util.spec_from_file_location(name, CI_PREP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def make_package_archive(path: Path, files: dict[str, bytes]) -> tuple[Path, str]:
    archive = path / "oracle.tgz"
    with tarfile.open(archive, mode="w:gz") as package:
        for relative, content in sorted(files.items()):
            member = tarfile.TarInfo(f"package/{relative}")
            member.size = len(content)
            member.mode = 0o644
            member.mtime = 0
            package.addfile(member, io.BytesIO(content))
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode("ascii")
    return archive, integrity


def test_exact_version_patch_is_hash_gated_idempotent_and_backed_up(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.18.0"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {"sample.txt": {"patch": "sample.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    compat.patch_root = lambda version=compat.SUPPORTED_VERSION: patches
    backup = tmp_path / "backup"

    first = compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=package, backup_root=backup)
    second = compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=package, backup_root=backup)

    assert first["changed"] == ["sample.txt"]
    assert second["already_patched"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_hash_specific_legacy_patch_migrates_without_backup(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.18.0"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"middle\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    (patches / "legacy.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+middle\n",
        encoding="utf-8",
    )
    legacy_hash = digest(b"middle\n")
    compat.PATCHES = {
        "sample.txt": {
            "patch": "sample.patch",
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
            "legacy_patched": [legacy_hash],
            "legacy_patches": {legacy_hash: "legacy.patch"},
        }
    }
    compat.patch_root = lambda version=compat.SUPPORTED_VERSION: patches
    backup = tmp_path / "backup"

    result = compat.ensure_oracle_compatibility(
        "oracle 0.18.0",
        package_root=package,
        backup_root=backup,
    )

    assert result["changed"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_unknown_oracle_version_or_file_hash_fails_closed(tmp_path: Path) -> None:
    compat = load_compat()
    with pytest.raises(compat.OracleCompatError) as version:
        compat.ensure_oracle_compatibility("oracle 0.17.0", package_root=tmp_path)
    assert version.value.code == "ORACLE_VERSION_UNVALIDATED"
    with pytest.raises(compat.OracleCompatError) as scoped_version_without_profile:
        compat.ensure_oracle_compatibility("oracle 0.18.1", package_root=tmp_path)
    assert scoped_version_without_profile.value.code == "ORACLE_VERSION_UNVALIDATED"
    assert compat.SUPPORTED_VERSION in scoped_version_without_profile.value.evidence["supported"]

    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"unknown\n")
    compat.LKG_PATCHES = {"sample.txt": {"patch": "missing.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    with pytest.raises(compat.OracleCompatError) as mismatch:
        compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package)
    assert mismatch.value.code == "ORACLE_FILE_HASH_MISMATCH"


def test_scoped_oracle_version_requires_its_profile_and_uses_its_own_contract(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    package_json = json.dumps({"version": "0.18.0"}).encode("utf-8")
    (package / "package.json").write_bytes(package_json)
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    other = package / "other.js"
    other.write_bytes(b"export const trusted = true;\n")
    dependency = package / "node_modules" / "dependency" / "index.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_bytes(b"export const externalDependency = true;\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    compat.SCOPED_PATCHES = {
        "webjjonku-linux": {
            "0.18.0": {
                "sample.txt": {
                    "patch": "sample.patch",
                    "pristine": digest(b"before\n"),
                    "patched": digest(b"after\n"),
                }
            }
        }
    }
    archive, integrity = make_package_archive(tmp_path, {
        "package.json": package_json,
        "sample.txt": b"before\n",
        "other.js": b"export const trusted = true;\n",
    })
    compat.SCOPED_PACKAGE_INTEGRITIES = {"webjjonku-linux": {"0.18.0": integrity}}
    compat.patch_root = lambda version=compat.SUPPORTED_VERSION: patches

    compat.PATCHES = {}
    default_path = compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=package)
    assert default_path["version"] == "0.18.0"

    with pytest.raises(compat.OracleCompatError) as unknown_profile:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0",
            profile="other-linux",
            package_root=package,
            package_archive=archive,
        )
    assert unknown_profile.value.code == "ORACLE_COMPAT_PROFILE_UNVALIDATED"

    with pytest.raises(compat.OracleCompatError) as missing_root:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0",
            profile="webjjonku-linux",
            package_archive=archive,
        )
    assert missing_root.value.code == "ORACLE_PACKAGE_ROOT_REQUIRED"

    result = compat.ensure_scoped_oracle_compatibility(
        "oracle 0.18.0",
        profile="webjjonku-linux",
        package_root=package,
        package_archive=archive,
        backup_root=tmp_path / "backup",
    )

    assert result["version"] == "0.18.0"
    assert result["profile"] == "webjjonku-linux"
    assert result["changed"] == ["sample.txt"]
    assert result["package_integrity"] == integrity
    assert target.read_bytes() == b"after\n"
    assert dependency.is_file()
    other.write_bytes(b"export const trusted = false;\n")
    with pytest.raises(compat.OracleCompatError) as tree_mismatch:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0",
            profile="webjjonku-linux",
            package_root=package,
            package_archive=archive,
            backup_root=tmp_path / "backup",
        )
    assert tree_mismatch.value.code == "ORACLE_PACKAGE_TREE_MISMATCH"
    other.write_bytes(b"export const trusted = true;\n")
    (package / "injected.js").write_bytes(b"export const injected = true;\n")
    with pytest.raises(compat.OracleCompatError) as extra_file:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0",
            profile="webjjonku-linux",
            package_root=package,
            package_archive=archive,
            backup_root=tmp_path / "backup",
        )
    assert extra_file.value.code == "ORACLE_PACKAGE_TREE_MISMATCH"
    assert extra_file.value.evidence["path"] == "injected.js"


def test_scoped_package_tree_rejects_directory_links_and_junctions(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    link = package / "dist"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(external)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr
    else:
        link.symlink_to(external, target_is_directory=True)

    with pytest.raises(compat.OracleCompatError) as unsafe_tree:
        compat._scan_installed_package_tree(package.resolve(strict=True))
    assert unsafe_tree.value.code == "ORACLE_PACKAGE_TREE_MISMATCH"
    assert unsafe_tree.value.evidence["path"] == "dist"


def test_ci_extractor_rejects_windows_escape_and_reserved_paths_before_writing(tmp_path: Path) -> None:
    prepare = load_ci_prepare()
    for index, unsafe_name in enumerate((r"package/a\..\..\..\escaped.txt", "package/CON.txt")):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as package:
            content = b"unsafe\n"
            member = tarfile.TarInfo(unsafe_name)
            member.size = len(content)
            package.addfile(member, io.BytesIO(content))
        destination = tmp_path / f"extract-{index}"
        with pytest.raises(RuntimeError, match="unsafe path"):
            prepare.extract_verified(stream.getvalue(), destination)
    assert not (tmp_path / "escaped.txt").exists()


def test_scoped_cli_requires_explicit_version(capsys: pytest.CaptureFixture[str]) -> None:
    compat = load_compat()
    assert compat.main(["--profile", "webjjonku-linux"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "ORACLE_VERSION_REQUIRED"


def test_scoped_profile_rejects_missing_node_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    compat = load_compat()
    monkeypatch.setattr(compat.shutil, "which", lambda _name: None)
    with pytest.raises(compat.OracleCompatError) as unsupported:
        compat._verify_scoped_node_runtime("webjjonku-linux")
    assert unsupported.value.code == "ORACLE_NODE_VERSION_UNSUPPORTED"
    assert unsupported.value.evidence["required"] == ">=24 <27"


@pytest.mark.parametrize(
    ("node", "stdout", "returncode"),
    [
        (None, "", 0),
        ("node", "v23.11.1\n", 0),
        ("node", "v27.0.0\n", 0),
        ("node", "not-a-version\n", 0),
        ("node", "v24.0.0\n", 1),
    ],
)
def test_current_oracle_rejects_unvalidated_node_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, node: str | None, stdout: str, returncode: int
) -> None:
    compat = load_compat()
    monkeypatch.setattr(compat.shutil, "which", lambda _name: node)
    called: list[object] = []
    monkeypatch.setattr(
        compat.subprocess,
        "run",
        lambda *_args, **_kwargs: called.append(object())
        or type("NodeResult", (), {"returncode": returncode, "stdout": stdout})(),
    )
    with pytest.raises(compat.OracleCompatError) as unsupported:
        compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=tmp_path)
    assert unsupported.value.code == "ORACLE_NODE_VERSION_UNSUPPORTED"
    assert unsupported.value.evidence["contract"] == "current:0.18.0"
    assert unsupported.value.evidence["required"] == ">=24 <27"
    assert bool(called) is (node is not None)


def test_lkg_recovery_does_not_inherit_current_node_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compat = load_compat()
    observed: dict[str, object] = {}
    monkeypatch.setattr(compat.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        compat,
        "_apply_oracle_compatibility",
        lambda version, **kwargs: observed.update(version=version, **kwargs) or {"ok": True, "version": version},
    )
    result = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=tmp_path)
    assert result == {"ok": True, "version": "0.17.1"}
    assert observed["contracts"] is compat.LKG_PATCHES


def test_published_0180_followup_timeout_patch_preserves_parent_unless_cli_overrides(tmp_path: Path) -> None:
    compat = load_compat()
    configured = os.environ.get("ORACLE_018_PACKAGE_ROOT", "").strip()
    configured_archive = os.environ.get("ORACLE_018_PACKAGE_ARCHIVE", "").strip()
    source = Path(configured) if configured else Path("__oracle_018_cache_unset__")
    archive = Path(configured_archive) if configured_archive else Path("__oracle_018_archive_unset__")
    if not source.is_dir() or not archive.is_file():
        pytest.skip("published Oracle 0.18.0 package root and archive are unavailable")
    package = tmp_path / "oracle"
    shutil.copytree(source, package)

    result = compat.ensure_scoped_oracle_compatibility(
        "oracle 0.18.0",
        profile="webjjonku-linux",
        package_root=package,
        package_archive=archive,
        backup_root=tmp_path / "backup",
    )

    relative = "dist/bin/oracle-cli.js"
    assert relative in set(result["changed"]) | set(result["already_patched"])
    target = package / relative
    source_text = target.read_text(encoding="utf-8")
    assert 'getSource("browserTimeout") !== "cli"' in source_text
    assert "...browserFollowup.browserConfig" in source_text
    assert "timeoutMs: cliConfig.timeoutMs" in source_text
    assert compat.sha256_file(target) == compat.SCOPED_PATCHES["webjjonku-linux"]["0.18.0"][relative]["patched"]
    node = shutil.which("node")
    assert node is not None
    syntax = subprocess.run([node, "--check", str(target)], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stderr


def test_published_0180_default_contract_applies_every_current_patch(tmp_path: Path) -> None:
    compat = load_compat()
    configured = os.environ.get("ORACLE_018_PACKAGE_ROOT", "").strip()
    source = Path(configured) if configured else Path("__oracle_018_cache_unset__")
    if not source.is_dir():
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the exact published Oracle 0.18.0 package")
        pytest.skip("published Oracle 0.18.0 package root is unavailable")
    package = tmp_path / "oracle-default"
    shutil.copytree(source, package)

    result = compat.ensure_oracle_compatibility(
        "oracle 0.18.0", package_root=package, backup_root=tmp_path / "backup-default"
    )
    touched = set(result["changed"]) | set(result["already_patched"])
    assert touched == set(compat.PATCHES)
    node = shutil.which("node")
    assert node is not None
    for relative, contract in compat.PATCHES.items():
        target = package / relative
        assert compat.sha256_file(target) == contract["patched"]
        syntax = subprocess.run([node, "--check", str(target)], capture_output=True, text=True, check=False)
        assert syntax.returncode == 0, f"{relative}: {syntax.stderr}"
    chrome_lifecycle = package / "dist/src/browser/chromeLifecycle.js"
    chrome_lifecycle_text = chrome_lifecycle.read_text(encoding="utf-8")
    assert chrome_lifecycle_text.count('"--disable-session-crashed-bubble"') == 1
    session_manager = package / "dist/src/sessionManager.js"
    session_manager_text = session_manager.read_text(encoding="utf-8")
    assert 'new Set(["EPERM", "EACCES", "EBUSY"])' in session_manager_text
    assert "process.platform !== \"win32\"" in session_manager_text
    assert "WINDOWS_ATOMIC_RENAME_RETRY_DELAYS_MS[attempt]" in session_manager_text
    assert "await renameMetadataAtomically(temporaryPath, targetPath);" in session_manager_text
    retry_probe = subprocess.run(
        [
            node,
            "--input-type=module",
            "--eval",
            """
import fs from "node:fs/promises";
const WINDOWS_ATOMIC_RENAME_RETRY_CODES = new Set(["EPERM", "EACCES", "EBUSY"]);
const WINDOWS_ATOMIC_RENAME_RETRY_DELAYS_MS = [25, 50, 100, 200, 400, 800, 1600];
async function renameMetadataAtomically(temporaryPath, targetPath) {
  for (let attempt = 0;; attempt += 1) {
    try {
      await fs.rename(temporaryPath, targetPath);
      return;
    } catch (error) {
      const code = typeof error === "object" && error !== null && "code" in error ? error.code : undefined;
      if (process.platform !== "win32" || !WINDOWS_ATOMIC_RENAME_RETRY_CODES.has(code) || attempt >= WINDOWS_ATOMIC_RENAME_RETRY_DELAYS_MS.length)
        throw error;
      await new Promise((resolve) => setTimeout(resolve, WINDOWS_ATOMIC_RENAME_RETRY_DELAYS_MS[attempt]));
    }
  }
}
Object.defineProperty(process, "platform", { value: "win32", configurable: true });
let attempts = 0;
fs.rename = async () => {
  attempts += 1;
  if (attempts < 3) {
    const error = new Error("transient Windows metadata lock");
    error.code = "EPERM";
    throw error;
  }
};
await renameMetadataAtomically("temporary", "target");
if (attempts !== 3) throw new Error(`expected 3 rename attempts, observed ${attempts}`);

attempts = 0;
fs.rename = async () => {
  attempts += 1;
  const error = new Error("persistent Windows metadata lock");
  error.code = "EPERM";
  throw error;
};
try {
  await renameMetadataAtomically("temporary", "target");
  throw new Error("persistent EPERM unexpectedly succeeded");
} catch (error) {
  if (error.code !== "EPERM" || attempts !== 8) throw error;
}

Object.defineProperty(process, "platform", { value: "linux", configurable: true });
attempts = 0;
fs.rename = async () => {
  attempts += 1;
  const error = new Error("non-Windows metadata error");
  error.code = "EPERM";
  throw error;
};
try {
  await renameMetadataAtomically("temporary", "target");
  throw new Error("non-Windows EPERM unexpectedly retried");
} catch (error) {
  if (error.code !== "EPERM" || attempts !== 1) throw error;
}
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert retry_probe.returncode == 0, retry_probe.stderr


def test_published_0180_pro_effort_is_strict_for_current_five_row_menu(tmp_path: Path) -> None:
    compat = load_compat()
    configured = os.environ.get("ORACLE_018_PACKAGE_ROOT", "").strip()
    source = Path(configured) if configured else Path("__oracle_018_cache_unset__")
    if not source.is_dir():
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the exact published Oracle 0.18.0 package")
        pytest.skip("published Oracle 0.18.0 package root is unavailable")
    package = tmp_path / "oracle-pro-effort"
    shutil.copytree(source, package)
    compat.ensure_oracle_compatibility(
        "oracle 0.18.0", package_root=package, backup_root=tmp_path / "backup-pro-effort"
    )
    target = package / "dist/src/browser/actions/thinkingTime.js"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oracle-0180-gpt56-sol-effort-menu.json")
        .read_text(encoding="utf-8")
    )
    labels = [item["label"] for item in fixture["items"]]
    assert labels == ["Instant", "Medium", "High", "Extra High", "Pro"]
    assert "Heavy" not in labels
    node = shutil.which("node")
    assert node is not None
    source_text = target.read_text(encoding="utf-8")
    source_text = source_text.replace(
        'import { MENU_CONTAINER_SELECTOR, MENU_ITEM_SELECTOR, MODEL_BUTTON_SELECTOR, } from "../constants.js";',
        'const MENU_CONTAINER_SELECTOR=""; const MENU_ITEM_SELECTOR=""; const MODEL_BUTTON_SELECTOR="";',
    ).replace(
        'import { logDomFailure } from "../domDebug.js";',
        'const logDomFailure=async()=>{};',
    ).replace(
        'import { buildClickDispatcher } from "./domEvents.js";',
        'const buildClickDispatcher=()=>"";',
    ).replace(
        'import { BrowserAutomationError } from "../../oracle/errors.js";',
        'class BrowserAutomationError extends Error { constructor(message, details) { super(message); this.details=details; } }',
    )
    test_module = tmp_path / "thinkingTime-current-contract.mjs"
    test_module.write_text(source_text, encoding="utf-8")
    assert not source_text.startswith("import ")
    script = f"""
import {{ ensureThinkingTime }} from {json.dumps(test_module.as_uri())};
const diagnostic = {json.dumps(fixture)};
const runCase = async (firstResult) => {{
  let calls = 0;
  const Runtime = {{ evaluate: async () => ({{ result: {{ value: calls++ === 0 ? firstResult : null }} }}) }};
  const logs = [];
  try {{
    await ensureThinkingTime(Runtime, 'pro', (message) => logs.push(message), 'gpt-5.6-sol');
    return {{ ok: true, logs }};
  }} catch (error) {{
    return {{ ok: false, message: error.message, logs }};
  }}
}};
const selected = await runCase({{ status: 'already-selected', label: 'Pro', modelKind: 'gpt56', diagnostic }});
const missing = await runCase({{ status: 'option-not-found', modelKind: 'gpt56', diagnostic }});
const unverified = await runCase({{ status: 'selection-unverified', modelKind: 'gpt56', diagnostic }});
const unavailable = await runCase({{
  status: 'option-disabled', label: 'Pro', notice: 'temporarily unavailable', modelKind: 'gpt56', diagnostic
}});
console.log(JSON.stringify({{ selected, missing, unverified, unavailable }}));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["selected"] == {
        "ok": True,
        "logs": ["[browser] Thinking time: Pro (already selected)"],
    }
    for case in ("missing", "unverified", "unavailable"):
        assert result[case]["ok"] is False
        assert "refusing to submit without confirmed Pro" in result[case]["message"]


def test_published_0180_pro_power_slider_current_ui_is_verified_and_fail_closed(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    configured = os.environ.get("ORACLE_018_PACKAGE_ROOT", "").strip()
    source = Path(configured) if configured else Path("__oracle_018_cache_unset__")
    if not source.is_dir():
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the exact published Oracle 0.18.0 package")
        pytest.skip("published Oracle 0.18.0 package root is unavailable")
    package = tmp_path / "oracle-pro-power-slider"
    shutil.copytree(source, package)
    compat.ensure_oracle_compatibility(
        "oracle 0.18.0", package_root=package, backup_root=tmp_path / "backup-pro-power-slider"
    )
    target = package / "dist/src/browser/actions/thinkingTime.js"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oracle-0180-gpt56-sol-power-slider.json")
        .read_text(encoding="utf-8")
    )
    one_based_fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oracle-0180-gpt56-sol-power-slider-one-based.json")
        .read_text(encoding="utf-8")
    )
    delayed_model_fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oracle-0180-gpt56-sol-power-slider-delayed-model.json")
        .read_text(encoding="utf-8")
    )
    assert fixture["model_button"]["text"] == "Thinking effort"
    assert fixture["simple_view"]["text"].startswith("Pro, 5 of 5")
    assert fixture["slider_control"] == {
        "role": "slider",
        "ariaValueMin": 0,
        "ariaValueMax": 4,
        "ariaValueNow": 4,
    }
    assert one_based_fixture["slider_control"] == {
        "role": "slider",
        "ariaValueMin": 1,
        "ariaValueMax": 5,
        "ariaValueNow": 4,
    }
    assert fixture["model_rows"][0] == {
        "role": "menuitemradio",
        "label": "GPT-5.6 Sol",
        "ariaChecked": True,
    }
    node = shutil.which("node")
    assert node is not None
    source_text = target.read_text(encoding="utf-8")
    assert "selectGpt56ProPowerSlider" in source_text
    assert "selectedModelIsExactGpt56Sol" in source_text
    assert "readPowerState" in source_text
    assert "aria-valuemin" in source_text and "aria-valuemax" in source_text
    assert "displayOrdinal !== ordinal || displayTotal !== total" in source_text
    assert "waitForStableReadyState" in source_text
    assert "TARGET_LEVEL !== 'pro'" in source_text
    source_text = source_text.replace(
        'import { MENU_CONTAINER_SELECTOR, MENU_ITEM_SELECTOR, MODEL_BUTTON_SELECTOR, } from "../constants.js";',
        'const MENU_CONTAINER_SELECTOR="[role=menu]"; '
        'const MENU_ITEM_SELECTOR="[role=menuitem],[role=menuitemradio]"; '
        'const MODEL_BUTTON_SELECTOR=".model-button";',
    ).replace(
        'import { logDomFailure } from "../domDebug.js";',
        'const logDomFailure=async()=>{};',
    ).replace(
        'import { buildClickDispatcher } from "./domEvents.js";',
        'const buildClickDispatcher=()=>"";',
    ).replace(
        'import { BrowserAutomationError } from "../../oracle/errors.js";',
        'class BrowserAutomationError extends Error { constructor(message, details) { super(message); this.details=details; } }',
    )
    test_module = tmp_path / "thinkingTime-power-slider-contract.mjs"
    test_module.write_text(source_text, encoding="utf-8")
    fixture_literal = json.dumps(fixture)
    one_based_fixture_literal = json.dumps(one_based_fixture)
    delayed_model_fixture_literal = json.dumps(delayed_model_fixture)
    script = f"""
import {{ ensureThinkingTime }} from {json.dumps(test_module.as_uri())};
const fixture = {fixture_literal};
const oneBasedFixture = {one_based_fixture_literal};
const delayedModelFixture = {delayed_model_fixture_literal};
class FakeElement extends EventTarget {{
  constructor(text, attrs = {{}}, visible = true) {{
    super(); this._text = text; this.attrs = attrs; this.visible = visible;
    this.queryOne = () => null; this.queryMany = () => [];
  }}
  get textContent() {{ return typeof this._text === 'function' ? this._text() : this._text; }}
  getAttribute(name) {{
    if (!Object.prototype.hasOwnProperty.call(this.attrs, name)) return null;
    const value = this.attrs[name];
    return typeof value === 'function' ? value() : value;
  }}
  getBoundingClientRect() {{ return this.visible ? {{width: 240, height: 40}} : {{width: 0, height: 0}}; }}
  querySelector(selector) {{ return this.queryOne(selector); }}
  querySelectorAll(selector) {{ return this.queryMany(selector); }}
  matches(selector) {{ return selector === 'button.__composer-pill' || selector === '.model-button'; }}
  closest() {{ return null; }}
  focus() {{}}
  get isConnected() {{ return true; }}
}}
globalThis.HTMLElement = FakeElement;
globalThis.window = globalThis;
    globalThis.MouseEvent = class extends Event {{ constructor(type, init) {{ super(type, init); }} }};
    globalThis.PointerEvent = globalThis.MouseEvent;
    globalThis.KeyboardEvent = class extends Event {{
      constructor(type, init) {{ super(type, init); this.key=init.key; this.code=init.code; }}
    }};

const runCase = async ({{rangeFixture = fixture, validModel = true, controlledFragment = false,
  modelRowsMountAfter = 0, contradictoryDisplay = false, duplicateExplicitMenu = false}} = {{}}) => {{
  const sliderFixture = rangeFixture.slider_control;
  let rawValue = sliderFixture.ariaValueNow;
  let keydowns = 0;
  let modelReads = 0;
  const ordinal = () => rawValue - sliderFixture.ariaValueMin + 1;
  const total = sliderFixture.ariaValueMax - sliderFixture.ariaValueMin + 1;
  const pill = new FakeElement(fixture.model_button.text, {{
    'aria-haspopup': fixture.model_button.ariaHaspopup,
    'aria-expanded': fixture.model_button.ariaExpanded,
    'aria-controls': controlledFragment ? 'controlled-effort-fragment' : null,
  }});
  const view = new FakeElement(() =>
    (rawValue === sliderFixture.ariaValueMax || contradictoryDisplay ? 'Pro' : 'Extra High') + ', ' +
      (contradictoryDisplay ? total : ordinal()) + ' of ' + total +
      '.Use Left and Right arrow keys to adjust power.',
    {{'data-testid': fixture.simple_view.testid}},
  );
  const slider = new FakeElement('', {{
    role: 'slider',
    'aria-valuemin': String(sliderFixture.ariaValueMin),
    'aria-valuemax': String(sliderFixture.ariaValueMax),
    'aria-valuenow': () => String(rawValue),
  }});
  view.queryOne = (selector) => selector.includes('[role="slider"]') ? slider : null;
  const power = new FakeElement('', {{role: 'menuitem', 'aria-label': fixture.power_control.ariaLabel}});
  slider.addEventListener('keydown', (event) => {{
    if (event.key === 'ArrowRight') {{
      rawValue = Math.min(sliderFixture.ariaValueMax, rawValue + 1);
      keydowns += 1;
    }}
  }});
  const model56 = new FakeElement(fixture.model_rows[0].label, {{
    role: 'menuitemradio', 'aria-checked': validModel ? 'true' : 'false',
    'data-state': validModel ? 'checked' : null,
  }});
  const model55 = new FakeElement(fixture.model_rows[1].label, {{
    role: 'menuitemradio', 'aria-checked': validModel ? 'false' : 'true',
    'data-state': validModel ? null : 'checked',
  }});
  const menu = new FakeElement('ProPro, 5 of 5.GPT-5.6 SolGPT-5.5', {{
    role: 'menu', 'data-testid': 'composer-intelligence-picker-content',
  }});
  const fragment = new FakeElement('Pro, 5 of 5.', {{
    role: 'menu', 'data-testid': 'composer-intelligence-picker-content',
  }});
  fragment.queryOne = (selector) =>
    selector.includes('composer-model-picker-slider-simple-view') ? view : null;
  fragment.queryMany = () => [];
  menu.queryOne = (selector) =>
    selector.includes('composer-model-picker-slider-simple-view') ? view :
    selector.includes('composer-intelligence-picker-content') ? menu : null;
  menu.queryMany = (selector) =>
    selector === '[role="menuitemradio"]' ?
      (++modelReads <= modelRowsMountAfter ? [] : [model56, model55]) :
    selector.includes('[role="menuitem"], button') ? [power] :
    selector.includes('[role="menuitem"]') ? [power] :
    selector.includes('[role="menuitemradio"]') ? [model56, model55] :
    selector.includes('[data-testid]') ? [view] : [];
  const duplicateMenu = new FakeElement(menu.textContent, {{
    role: 'menu', 'data-testid': 'composer-intelligence-picker-content',
  }});
  duplicateMenu.queryOne = menu.queryOne;
  duplicateMenu.queryMany = menu.queryMany;
  globalThis.document = {{
    body: new FakeElement('body'),
    querySelector: (selector) =>
      selector === '.model-button' ? pill :
      selector.includes('composer-intelligence-picker-content') ? menu : null,
    querySelectorAll: (selector) =>
      selector.includes('button.__composer-pill') ? [pill] :
      selector === '[role=menu]' ? (duplicateExplicitMenu ? [menu, duplicateMenu] : [menu]) :
      selector.includes('form button[aria-haspopup="menu"]') ? [pill] : [],
    getElementById: (id) => id === 'controlled-effort-fragment' ? fragment : null,
    dispatchEvent: () => true,
  }};
  const logs = [];
  const Runtime = {{evaluate: async ({{expression}}) => ({{result: {{value: await eval(expression)}}}})}};
  try {{
    await ensureThinkingTime(Runtime, 'pro', (message) => logs.push(message), 'gpt-5.6-sol');
    return {{ok: true, logs, rawValue, ordinal: ordinal(), keydowns}};
  }} catch (error) {{
    return {{ok: false, message: error.message, logs, rawValue, ordinal: ordinal(), keydowns}};
  }}
}};
console.log(JSON.stringify({{
  selectedZeroBased: await runCase(),
  controlledPortal: await runCase({{controlledFragment: true}}),
  delayedModelRows: await runCase({{
    modelRowsMountAfter: delayedModelFixture.model_rows_mount_after_queries,
  }}),
  switchedOneBased: await runCase({{rangeFixture: oneBasedFixture}}),
  switchedFromMedium: await runCase({{
    rangeFixture: {{...fixture, slider_control: {{...fixture.slider_control, ariaValueNow: 1}}}},
  }}),
  contradictoryRangeLabel: await runCase({{
    rangeFixture: {{...fixture, slider_control: {{...fixture.slider_control, ariaValueNow: 3}}}},
    contradictoryDisplay: true,
  }}),
  duplicateExplicitMenu: await runCase({{duplicateExplicitMenu: true}}),
  wrongModel: await runCase({{validModel: false}}),
}}));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["selectedZeroBased"] == {
        "ok": True,
        "logs": ["[browser] Thinking time: Pro, 5 of 5 (already selected)"],
        "rawValue": 4,
        "ordinal": 5,
        "keydowns": 0,
    }
    assert result["controlledPortal"] == {
        "ok": True,
        "logs": ["[browser] Thinking time: Pro, 5 of 5 (already selected)"],
        "rawValue": 4,
        "ordinal": 5,
        "keydowns": 0,
    }
    assert result["delayedModelRows"] == {
        "ok": True,
        "logs": ["[browser] Thinking time: Pro, 5 of 5 (already selected)"],
        "rawValue": 4,
        "ordinal": 5,
        "keydowns": 0,
    }
    assert result["switchedOneBased"] == {
        "ok": True,
        "logs": ["[browser] Thinking time: Pro, 5 of 5"],
        "rawValue": 5,
        "ordinal": 5,
        "keydowns": 1,
    }
    assert result["switchedFromMedium"] == {
        "ok": True,
        "logs": ["[browser] Thinking time: Pro, 5 of 5"],
        "rawValue": 4,
        "ordinal": 5,
        "keydowns": 3,
    }
    assert result["wrongModel"]["ok"] is False
    assert "refusing to submit without confirmed Pro" in result["wrongModel"]["message"]
    for case in ("contradictoryRangeLabel", "duplicateExplicitMenu"):
        assert result[case]["ok"] is False
        assert "refusing to submit without confirmed Pro" in result[case]["message"]


def test_published_0180_pro_power_slider_migrates_known_exact_bytes(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    configured = os.environ.get("ORACLE_018_PACKAGE_ROOT", "").strip()
    source = Path(configured) if configured else Path("__oracle_018_cache_unset__")
    if not source.is_dir():
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the exact published Oracle 0.18.0 package")
        pytest.skip("published Oracle 0.18.0 package root is unavailable")
    relative = "dist/src/browser/actions/thinkingTime.js"
    contract = compat.PATCHES[relative]
    legacy_hashes = list(contract["legacy_patched"])
    assert legacy_hashes == [
        "978f754ba4011957790530474d27d629a8d353dd449f8e2636e02a9abd27b81a",
        "a19ce77fe57b4fa1a290e130da323377ed69b6e51b1ad133b1ab5355ead59345",
    ]
    legacy_patches = {
        legacy_hash: str(contract.get("legacy_patches", {}).get(legacy_hash) or contract["legacy_patch"])
        for legacy_hash in legacy_hashes
    }
    assert legacy_patches[legacy_hashes[1]] == (
        "thinkingTime.gpt56-pro-power-slider.pre-aria-range.patch"
    )

    for index, legacy_hash in enumerate(legacy_hashes):
        package = tmp_path / f"oracle-pro-power-slider-legacy-{index}"
        shutil.copytree(source, package)
        target = package / relative
        current = compat.sha256_file(target)
        if current != contract["pristine"]:
            if current == contract["patched"]:
                source_patch = str(contract["patch"])
            elif current in legacy_patches:
                source_patch = legacy_patches[current]
            else:
                pytest.fail(f"unexpected published Oracle thinkingTime.js hash: {current}")
            compat._apply_patch(
                package, compat.patch_root("0.18.0") / source_patch, reverse=True
            )
        assert compat.sha256_file(target) == contract["pristine"]

        compat._apply_patch(
            package, compat.patch_root("0.18.0") / legacy_patches[legacy_hash]
        )
        assert compat.sha256_file(target) == legacy_hash

        backup = tmp_path / f"backup-pro-power-slider-legacy-{index}"
        first = compat.ensure_oracle_compatibility(
            "oracle 0.18.0", package_root=package, backup_root=backup
        )
        second = compat.ensure_oracle_compatibility(
            "oracle 0.18.0", package_root=package, backup_root=backup
        )

        assert relative in first["changed"]
        assert relative in second["already_patched"]
        assert compat.sha256_file(target) == contract["patched"]
        assert compat.sha256_file(backup / relative) == contract["pristine"]
        node = shutil.which("node")
        assert node is not None
        syntax = subprocess.run(
            [node, "--check", str(target)], capture_output=True, text=True, check=False
        )
        assert syntax.returncode == 0, syntax.stderr


def test_oracle_session_metadata_retry_patch_is_bounded_to_windows_transient_errors() -> None:
    patch_text = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "oracle-compat"
        / "0.18.0"
        / "sessionManager.windows-atomic-rename-retry.patch"
    ).read_text(encoding="utf-8")

    assert 'new Set(["EPERM", "EACCES", "EBUSY"])' in patch_text
    assert "process.platform !== \"win32\"" in patch_text
    assert "attempt >= WINDOWS_ATOMIC_RENAME_RETRY_DELAYS_MS.length" in patch_text
    assert "throw error;" in patch_text
    assert "fs.rm(temporaryPath, { force: true })" in patch_text


def test_published_0171_patch_requires_extra_high_and_pro_selection_proof(tmp_path: Path) -> None:
    compat = load_compat()
    source = (
        Path.home()
        / "AppData"
        / "Local"
        / "npm-cache"
        / "_npx"
        / "0a10f56e3ba43148"
        / "node_modules"
        / "@steipete"
        / "oracle"
    )
    if not source.is_dir():
        pytest.skip("published Oracle 0.17.1 cache is unavailable")
    package = tmp_path / "oracle"
    shutil.copytree(source, package)
    backup = tmp_path / "backup"

    result = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)
    touched = set(result["changed"]) | set(result["already_patched"])
    assert set(compat.LKG_PATCHES) <= touched
    node = shutil.which("node")
    assert node is not None, "Node.js is required to validate the patched Oracle source"
    followup = package / "dist/src/cli/followup.js"
    followup_text = followup.read_text(encoding="utf-8")
    assert "resumeArchivedConversation: parentWasArchived" in followup_text
    assert 'archiveConversations: parentWasArchived ? "always" : "never"' in followup_text
    archive_action = package / "dist/src/browser/actions/archiveConversation.js"
    archive_text = archive_action.read_text(encoding="utf-8")
    assert "FOLLOWUP_ARCHIVED_PARENT_UNARCHIVE_FAILED" in archive_text
    assert "exact-conversation-url-mismatch" in archive_text
    assert "unarchive-menu-ambiguous" in archive_text
    assert "direct-control" in archive_text
    assert "[role=\"menu\"],[role=\"dialog\"]" in archive_text
    assert "typeof PointerEvent === 'function'" in archive_text
    assert "waitForUniqueUnarchive(3000)" in archive_text
    assert "waitForDirectRestoreConfirmation(5000)" in archive_text
    assert "composerReady() && directUnarchiveCandidates().length === 0" in archive_text
    browser_index = package / "dist/src/browser/index.js"
    browser_index_text = browser_index.read_text(encoding="utf-8")
    assert browser_index_text.count("restoreArchivedFollowupBeforeComposer(Runtime") == 4
    assert 'stage: "followup-unarchive-before-composer"' in browser_index_text
    assert "composerSubmitAttempted: false" in browser_index_text
    assert "turnCountAfter" in browser_index_text
    navigation = package / "dist/src/browser/actions/navigation.js"
    navigation_text = navigation.read_text(encoding="utf-8")
    assert "hydrationAttempt <= 2" in navigation_text
    assert "retrying the same exact conversation once" in navigation_text
    assert "actualConversationId !== expectedConversationId" in navigation_text
    assert compat.sha256_file(navigation) == compat.PATCHES["dist/src/browser/actions/navigation.js"]["patched"]
    for target in (followup, archive_action, browser_index, navigation):
        syntax = subprocess.run([node, "--check", str(target)], capture_output=True, text=True, check=False)
        assert syntax.returncode == 0, syntax.stderr
    browser_tabs = package / "dist/src/cli/browserTabs.js"
    browser_tabs_text = browser_tabs.read_text(encoding="utf-8")
    assert "ORACLE_LIVE_TERMINAL_TIMEOUT_MS" in browser_tabs_text
    assert "holdRecoveredConnection" in browser_tabs_text
    assert "recoveredContentDeadlineMs = holdRecoveredConnection" in browser_tabs_text
    assert compat.sha256_file(browser_tabs) == compat.PATCHES["dist/src/cli/browserTabs.js"]["patched"]
    browser_tabs_syntax = subprocess.run(
        [node, "--check", str(browser_tabs)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert browser_tabs_syntax.returncode == 0, browser_tabs_syntax.stderr
    recovery_target = package / "dist/src/browser/recoverConversation.js"
    recovery_text = recovery_target.read_text(encoding="utf-8")
    assert "copyProfileSource" in recovery_text
    assert "launching an isolated profile copy" in recovery_text
    assert "wrapEphemeralRecoveryChrome" in recovery_text
    assert 'return copyProfileSource.trim();' in recovery_text
    assert 'const chromeProfile = await copyChromeProfile(profileSource, userDataDir, config.chromeProfile);' in recovery_text
    recovery_syntax = subprocess.run(
        [node, "--check", str(recovery_target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert recovery_syntax.returncode == 0, recovery_syntax.stderr
    assert compat.sha256_file(recovery_target) == compat.PATCHES["dist/src/browser/recoverConversation.js"]["patched"]


def test_published_0171_terminal_marker_fallback_is_stable_exact_and_thinking_independent(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    source = (
        Path.home()
        / "AppData"
        / "Local"
        / "npm-cache"
        / "_npx"
        / "0a10f56e3ba43148"
        / "node_modules"
        / "@steipete"
        / "oracle"
    )
    if not source.is_dir():
        pytest.skip("published Oracle 0.17.1 cache is unavailable")
    package = tmp_path / "oracle-terminal-marker"
    shutil.copytree(source, package)
    compat.ensure_oracle_compatibility(
        "oracle 0.17.1", package_root=package, backup_root=tmp_path / "backup-terminal-marker"
    )

    assistant = package / "dist/src/browser/actions/assistantResponse.js"
    oracle_cli = package / "dist/bin/oracle-cli.js"
    browser_config = package / "dist/src/cli/browserConfig.js"
    thinking = package / "dist/src/browser/actions/thinkingStatus.js"
    assert compat.sha256_file(assistant) == compat.LKG_PATCHES[
        "dist/src/browser/actions/assistantResponse.js"
    ]["patched"]
    assert compat.sha256_file(thinking) == compat.LKG_PATCHES[
        "dist/src/browser/actions/thinkingStatus.js"
    ]["patched"]
    assert compat.sha256_file(oracle_cli) == compat.LKG_PATCHES["dist/bin/oracle-cli.js"]["patched"]
    assert compat.sha256_file(browser_config) == compat.LKG_PATCHES["dist/src/cli/browserConfig.js"]["patched"]
    assistant_text = assistant.read_text(encoding="utf-8")
    oracle_cli_text = oracle_cli.read_text(encoding="utf-8")
    assert "readAssistantSnapshot(Runtime, minTurnIndex, expectedConversationId)" in assistant_text
    assert "terminalMarker: hasContractTerminalMarker(normalized.text)" in assistant_text
    assert "ORACLE_TERMINAL_MARKER_CONFIRM_CYCLES" not in assistant_text
    assert "ORACLE_TERMINAL_MARKER_MIN_STABLE_MS" not in assistant_text
    assert "bindFollowupBrowserPort(browserFollowup.browserConfig" in oracle_cli_text
    browser_config_text = browser_config.read_text(encoding="utf-8")
    helper_match = re.search(
        r"export function bindFollowupBrowserPort\([\s\S]+?\n\}",
        browser_config_text,
    )
    assert helper_match is not None
    helper_source = helper_match.group(0).removeprefix("export ")

    node = shutil.which("node")
    assert node is not None
    script = f"""
import {{ createTerminalGateState, classifyTurnTerminal, hasContractTerminalMarker }} from {json.dumps(assistant.as_uri())};
import {{ shouldWarnThinkingStatusUndetected, shouldWarnThinkingStatusMissing, formatThinkingUndetectedWarningLog, formatThinkingStatusMissingWarningLog }} from {json.dumps(thinking.as_uri())};
{helper_source}
const config = {{
  barConfirmCycles: 3,
  minStableMs: 1200,
  markerConfirmCycles: 2,
  markerMinStableMs: 5000,
  taskOutcomeContract: 'v1',
}};
const sample = (now, text, extra = {{}}) => ({{
  now,
  len: text.length,
  contentKey: `new-turn::${{text}}`,
  stopVisible: false,
  barVisible: false,
  strongThinkingActive: false,
  terminalMarker: hasContractTerminalMarker(text),
  ...extra,
}});
const answer = `${{'x'.repeat(15800)}}\\nTASK_OUTCOME: EXECUTED\\n`;
let state = createTerminalGateState(0);
let first = classifyTurnTerminal(state, sample(0, answer), config); state = first.state;
let second = classifyTurnTerminal(state, sample(2500, answer), config); state = second.state;
let third = classifyTurnTerminal(state, sample(5000, answer), config);
let changed = classifyTurnTerminal(third.state, sample(5100, answer.replace('x', 'y')), config);
let active = classifyTurnTerminal(second.state, sample(6000, answer, {{ strongThinkingActive: true }}), config);
let stopped = classifyTurnTerminal(second.state, sample(6000, answer, {{ stopVisible: true }}), config);
let contractAbsentState = createTerminalGateState(0);
contractAbsentState = classifyTurnTerminal(contractAbsentState, sample(0, answer), {{ ...config, taskOutcomeContract: null }}).state;
contractAbsentState = classifyTurnTerminal(contractAbsentState, sample(3000, answer), {{ ...config, taskOutcomeContract: null }}).state;
let contractAbsent = classifyTurnTerminal(contractAbsentState, sample(6000, answer), {{ ...config, taskOutcomeContract: null }});
console.log(JSON.stringify({{
  exactMarker: hasContractTerminalMarker('answer\\nTASK_OUTCOME: BLOCKED\\n\\n'),
  renderedReferences: hasContractTerminalMarker('answer\\nTASK_OUTCOME: EXECUTED\\nevidence/a.json. ↩\\nAGENTS.md. ↩'),
  renderedReferenceList: hasContractTerminalMarker('answer\\nTASK_OUTCOME: EXECUTED\\nevidence/a.json; skills/check/SKILL.md. ↩'),
  renderedReferenceAnnotations: hasContractTerminalMarker('answer\\nTASK_OUTCOME: EXECUTED\\nevidence/a.json; evidence/raw/b.json. ↩\\nAGENTS.md, section "Computation delegation, user instruction 2026-08-24". ↩\\n.codex-tmp/lane-markout/run_markout.py; checksum-verified Binance bookTicker and aggTrades inputs under .codex-tmp/lane-markout/raw/. ↩'),
  tooManyRenderedReferences: hasContractTerminalMarker('TASK_OUTCOME: EXECUTED\\n' + Array(33).fill('evidence/a.json. ↩').join('\\n')),
  malformedRenderedReference: hasContractTerminalMarker('TASK_OUTCOME: EXECUTED\\nevidence/a.json.'),
  arbitraryBacklinkProse: hasContractTerminalMarker('TASK_OUTCOME: EXECUTED\\ncontinue observing ↩'),
  pathPrefixedArbitraryProse: hasContractTerminalMarker('TASK_OUTCOME: EXECUTED\\nAGENTS.md arbitrary imperative prose should not be accepted. ↩'),
  lowerMarker: hasContractTerminalMarker('answer\\nTASK_OUTCOME: executed'),
  trailingProse: hasContractTerminalMarker('TASK_OUTCOME: EXECUTED\\nafter'),
  duplicateMarker: hasContractTerminalMarker('TASK_OUTCOME: BLOCKED\\nTASK_OUTCOME: EXECUTED'),
  embeddedEarlierMarker: hasContractTerminalMarker('analysis says TASK_OUTCOME: BLOCKED earlier\\nTASK_OUTCOME: EXECUTED'),
  first: first.terminal,
  second: second.terminal,
  third: third.terminal,
  changed: changed.terminal,
  active: active.terminal,
  stopped: stopped.terminal,
  contractAbsent: contractAbsent.terminal,
  warnEarly: shouldWarnThinkingStatusUndetected(false, false, 299999),
  warnAtThreshold: shouldWarnThinkingStatusUndetected(false, false, 300000),
  warnAfterDetected: shouldWarnThinkingStatusUndetected(true, false, 600000),
  warnAfterLogged: shouldWarnThinkingStatusUndetected(false, true, 600000),
  warnMissingEarly: shouldWarnThinkingStatusMissing(1000, false, 300999),
  warnMissingAtThreshold: shouldWarnThinkingStatusMissing(1000, false, 301000),
  warnMissingAfterLogged: shouldWarnThinkingStatusMissing(1000, true, 600000),
  warningText: formatThinkingUndetectedWarningLog(0, 300000),
  warningAfterDetectedText: formatThinkingStatusMissingWarningLog(0, 601000, true, 301000),
  inheritedFollowupPort: bindFollowupBrowserPort({{ debugPort: 56527, profile: 'parent' }}, null),
  isolatedFollowupPort: bindFollowupBrowserPort({{ debugPort: 56527, profile: 'parent' }}, 56442),
}}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["exactMarker"] is True
    assert payload["renderedReferences"] is True
    assert payload["renderedReferenceList"] is True
    assert payload["renderedReferenceAnnotations"] is True
    assert payload["tooManyRenderedReferences"] is False
    assert payload["malformedRenderedReference"] is False
    assert payload["arbitraryBacklinkProse"] is False
    assert payload["pathPrefixedArbitraryProse"] is False
    assert payload["lowerMarker"] is False
    assert payload["trailingProse"] is False
    assert payload["duplicateMarker"] is False
    assert payload["embeddedEarlierMarker"] is False
    assert payload["first"] is False
    assert payload["second"] is False
    assert payload["third"] is True
    assert payload["changed"] is False
    assert payload["active"] is False
    assert payload["stopped"] is False
    assert payload["contractAbsent"] is False
    assert payload["warnEarly"] is False
    assert payload["warnAtThreshold"] is True
    assert payload["warnAfterDetected"] is False
    assert payload["warnAfterLogged"] is False
    assert payload["warnMissingEarly"] is False
    assert payload["warnMissingAtThreshold"] is True
    assert payload["warnMissingAfterLogged"] is False
    assert "independent terminal watchdog remains active" in payload["warningText"]
    assert "previously detected thinking status has been absent" in payload["warningAfterDetectedText"]
    assert payload["inheritedFollowupPort"] == {"debugPort": 56527, "profile": "parent"}
    assert payload["isolatedFollowupPort"] == {"debugPort": 56442, "profile": "parent"}


def test_archived_parent_direct_restore_requires_exact_control_and_composer_transition(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    source = (
        Path.home()
        / "AppData"
        / "Local"
        / "npm-cache"
        / "_npx"
        / "0a10f56e3ba43148"
        / "node_modules"
        / "@steipete"
        / "oracle"
    )
    if not source.is_dir():
        pytest.skip("published Oracle 0.17.1 cache is unavailable")
    package = tmp_path / "oracle-dom"
    shutil.copytree(source, package)
    backup = tmp_path / "backup-dom"
    compat.ensure_oracle_compatibility(
        "oracle 0.17.1", package_root=package, backup_root=backup
    )
    module_url = (package / "dist/src/browser/actions/archiveConversation.js").as_uri()
    script = f"""
import {{ buildUnarchiveConversationExpressionForTest }} from {json.dumps(module_url)};
class FakeElement {{
  constructor(label, onClick = () => {{}}) {{ this.textContent = label; this.onClick = onClick; }}
  getAttribute() {{ return null; }}
  getBoundingClientRect() {{ return {{ width: 120, height: 30, top: 20, right: 900, left: 780 }}; }}
  dispatchEvent(event) {{ if (event.type === 'click') this.onClick(); return true; }}
}}
globalThis.HTMLElement = FakeElement;
globalThis.MouseEvent = class {{ constructor(type) {{ this.type = type; }} }};
globalThis.PointerEvent = class {{ constructor(type) {{ this.type = type; }} }};
globalThis.KeyboardEvent = class {{ constructor(type) {{ this.type = type; }} }};
globalThis.getComputedStyle = () => ({{ visibility: 'visible', display: 'block' }});
globalThis.window = {{ innerWidth: 1000 }};
globalThis.location = {{ href: 'https://chatgpt.com/c/exact-parent' }};
let clock = 0;
Date.now = () => (clock += 1000);
const runCase = async (label, transition) => {{
  let restoreVisible = true;
  let composerVisible = false;
  const restore = new FakeElement(label, () => {{
    if (transition) {{ restoreVisible = false; composerVisible = true; }}
  }});
  const composer = new FakeElement('composer');
  globalThis.document = {{
    querySelectorAll(selector) {{
      if (selector.includes('#prompt-textarea')) return composerVisible ? [composer] : [];
      if (selector.includes('button') || selector.includes('[role="menuitem"]')) return restoreVisible ? [restore] : [];
      return [];
    }},
    dispatchEvent() {{ return true; }},
  }};
  return await eval(buildUnarchiveConversationExpressionForTest(location.href));
}};
const success = await runCase('아카이브 보관 취소하기', true);
clock = 0;
const noop = await runCase('아카이브 보관 취소하기', false);
clock = 0;
const unrelated = await runCase('복원', true);
console.log(JSON.stringify({{ success, noop, unrelated }}));
"""
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["success"]["status"] == "unarchived"
    assert result["success"]["method"] == "direct-control"
    assert result["noop"]["reason"] == "unarchive-not-confirmed"
    assert result["unrelated"]["reason"] == "conversation-menu-not-found"
    target = package / "dist/src/browser/actions/thinkingTime.js"
    source_text = target.read_text(encoding="utf-8")
    assert "strictGpt56Effort" in source_text
    assert 'level === "extra-high" || level === "heavy"' in source_text
    assert "strictRequestedEffort" in source_text
    assert "composer-model-picker-slider-simple-view" in source_text
    assert ").find(isVisible) ?? null" in source_text
    assert "label: 'Power ' + current + ' of 5'" in source_text
    assert "`Power ${current} of 5`" not in source_text
    assert "const compact = Array.from(text)" in source_text
    assert "compact.includes(String(candidate) + 'of5')" in source_text
    assert "targetPower: POWER_TARGET" in source_text
    assert "exactGpt56ProProof" in source_text
    assert "composer-model-picker-slider-advanced-view" in source_text
    assert "compact.includes('modelgpt56sol') && compact.includes('effortpro')" in source_text
    syntax = subprocess.run(
        [node, "--check", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert compat.sha256_file(target) == compat.LKG_PATCHES["dist/src/browser/actions/thinkingTime.js"]["patched"]
    slider_script = (
        f"import {{ ensureThinkingTime }} from {json.dumps(target.as_uri())};"
        "const hiddenView={textContent:'',querySelector:()=>null,"
        "getAttribute:(name)=>name==='aria-hidden'?'true':null,"
        "getBoundingClientRect:()=>({width:0,height:0}),focus:()=>{},dispatchEvent:()=>true};"
        "const view={textContent:'Pro,\\u200b 5\\u00a0of\\u202f5.Use Left and Right arrow keys to adjust power.',"
        "querySelector:()=>null,getAttribute:()=>null,getBoundingClientRect:()=>({width:224,height:40}),"
        "focus:()=>{},dispatchEvent:()=>true};"
        "globalThis.document={querySelector:()=>null,querySelectorAll:(selector)=>selector.includes("
        "'composer-model-picker-slider-simple-view')?[hiddenView,view]:[],dispatchEvent:()=>true,body:{}};"
        "globalThis.KeyboardEvent=class{constructor(type,init){this.type=type;Object.assign(this,init)}};"
        "globalThis.HTMLElement=class{};"
        "const logs=[];"
        "const Runtime={evaluate:async({expression})=>{const value=await eval(expression);"
        "return {result:{value}};}};"
        "await ensureThinkingTime(Runtime,'heavy',(message)=>logs.push(message),'gpt-5.6-sol');"
        "console.log(JSON.stringify(logs));"
    )
    slider = subprocess.run(
        [node, "--input-type=module", "-e", slider_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert slider.returncode == 0, slider.stderr
    assert json.loads(slider.stdout) == ["[browser] Thinking time: Power 5 of 5 (already selected)"]

    exact_diagnostic_script = (
        f"import {{ ensureThinkingTime }} from {json.dumps(target.as_uri())};"
        "let menuOpen=false;"
        "let advancedQueries=0;"
        "const hiddenView={textContent:'Pro, 5 of 5.Use Left and Right arrow keys to adjust power.',"
        "querySelector:()=>null,getAttribute:(name)=>name==='aria-hidden'?'true':null,"
        "getBoundingClientRect:()=>({width:0,height:0}),focus:()=>{},dispatchEvent:()=>true};"
        "const view={textContent:'Pro, 5 of 5.Use Left and Right arrow keys to adjust power.',"
        "querySelector:()=>({getAttribute:(name)=>name==='aria-valuenow'?'4':null}),"
        "getAttribute:()=>null,getBoundingClientRect:()=>({width:224,height:40}),"
        "focus:()=>{},dispatchEvent:()=>true};"
        "const intelligenceMenu={textContent:'Pro, 5 of 5.AdvancedFasterSmarterM',"
        "querySelector:()=>null,querySelectorAll:(selector)=>selector.includes("
        "'composer-model-picker-slider-simple-view')?[hiddenView,view]:selector.includes("
        "'composer-model-picker-slider-advanced-view')&&++advancedQueries>=3?"
        "[hiddenAdvanced,advanced]:[],getAttribute:()=>null,"
        "getBoundingClientRect:()=>({width:320,height:240})};"
        "const hiddenAdvanced={textContent:'ModelGPT-5.6 SolEffortPro',querySelector:()=>null,"
        "querySelectorAll:()=>[],getAttribute:(name)=>name==='aria-hidden'?'true':null,"
        "getBoundingClientRect:()=>({width:0,height:0})};"
        "const advanced={textContent:'ModelGPT-5.6 SolEffortPro',"
        "querySelector:()=>null,"
        "querySelectorAll:()=>[],getAttribute:()=>null,"
        "getBoundingClientRect:()=>({width:320,height:240})};"
        "const modelButton=new class extends EventTarget{"
        "get textContent(){return menuOpen?'Pro':'Extra High'}"
        "querySelector(){return null}matches(){return true}get isConnected(){return true}"
        "getAttribute(name){if(name==='aria-expanded')return menuOpen?'true':'false';return null}"
        "getBoundingClientRect(){return {width:120,height:36}}focus(){}"
        "dispatchEvent(){menuOpen=true;return true}};"
        "globalThis.window=globalThis;"
        "globalThis.MouseEvent=class{constructor(type,init){this.type=type;Object.assign(this,init)}};"
        "globalThis.document={querySelector:(selector)=>selector.includes("
        "'composer-intelligence-picker-content')?(menuOpen?intelligenceMenu:null):(selector.includes("
        "'model-switcher-dropdown-button')||selector.includes('__composer-pill'))?modelButton:null,"
        "querySelectorAll:(selector)=>selector.includes('composer-model-picker-slider-simple-view')?"
        "(menuOpen?[hiddenView]:[]):selector.includes('composer-model-picker-slider-advanced-view')?"
        "[]:(selector.includes('[role=\"menu\"]')?"
        "(menuOpen?[intelligenceMenu]:[]):[]),dispatchEvent:()=>true,body:{}};"
        "globalThis.KeyboardEvent=class{constructor(type,init){this.type=type;Object.assign(this,init)}};"
        "globalThis.HTMLElement=class{};"
        "const logs=[];"
        "const Runtime={evaluate:async({expression})=>{const value=await eval(expression);"
        "return {result:{value}};}};"
        "await ensureThinkingTime(Runtime,'heavy',(message)=>logs.push(message));"
        "console.log(JSON.stringify({logs,advancedQueries}));"
    )
    exact_diagnostic = subprocess.run(
        [node, "--input-type=module", "-e", exact_diagnostic_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert exact_diagnostic.returncode == 0, exact_diagnostic.stderr
    exact_payload = json.loads(exact_diagnostic.stdout)
    assert exact_payload["advancedQueries"] >= 4
    assert exact_payload["logs"] == [
        "[browser] Thinking time: Power 5 of 5 (Pro) (already selected)"
    ]

    browser_config = package / "dist/src/browser/config.js"
    browser_config_text = browser_config.read_text(encoding="utf-8")
    assert "config?.copyProfileSource" in browser_config_text
    assert compat.sha256_file(browser_config) == compat.LKG_PATCHES["dist/src/browser/config.js"]["patched"]
    config_syntax = subprocess.run(
        [node, "--check", str(browser_config)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert config_syntax.returncode == 0, config_syntax.stderr
    behavior = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            (
                f"import {{ resolveBrowserConfig }} from {json.dumps(browser_config.as_uri())}; "
                "console.log(JSON.stringify({"
                "copied:resolveBrowserConfig({copyProfileSource:'signed'}).manualLogin,"
                "explicit:resolveBrowserConfig({copyProfileSource:'signed',manualLogin:true}).manualLogin"
                "}));"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert behavior.returncode == 0, behavior.stderr
    assert json.loads(behavior.stdout) == {"copied": False, "explicit": True}

    profile_copy = package / "dist/src/browser/profileCopy.js"
    profile_copy_text = profile_copy.read_text(encoding="utf-8")
    assert 'process.platform === "win32"' in profile_copy_text
    assert "recursive: true" in profile_copy_text
    assert compat.sha256_file(profile_copy) == compat.LKG_PATCHES["dist/src/browser/profileCopy.js"]["patched"]
    profile_syntax = subprocess.run(
        [node, "--check", str(profile_copy)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert profile_syntax.returncode == 0, profile_syntax.stderr

    source_profile = tmp_path / "source-profile"
    (source_profile / "Default/Network").mkdir(parents=True)
    (source_profile / "Default/Cache").mkdir(parents=True)
    (source_profile / "Default/Service Worker/CacheStorage").mkdir(parents=True)
    (source_profile / "Local State").write_text(
        json.dumps({"profile": {"last_used": "Default"}}),
        encoding="utf-8",
    )
    (source_profile / "Default/Network/Cookies").write_text("signed-session", encoding="utf-8")
    (source_profile / "Default/Cache/ignored").write_text("cache", encoding="utf-8")
    (source_profile / "Default/Service Worker/CacheStorage/ignored").write_text("cache", encoding="utf-8")
    copied_profile = tmp_path / "copied-profile"
    copy_result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            (
                f"import {{ copyChromeProfile }} from {json.dumps(profile_copy.as_uri())}; "
                f"const selected = await copyChromeProfile({json.dumps(str(source_profile))}, "
                f"{json.dumps(str(copied_profile))}); console.log(selected);"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert copy_result.returncode == 0, copy_result.stderr
    assert copy_result.stdout.strip() == "Default"
    assert (copied_profile / "Local State").is_file()
    assert (copied_profile / "Default/Network/Cookies").read_text(encoding="utf-8") == "signed-session"
    assert not (copied_profile / "Default/Cache").exists()
    assert not (copied_profile / "Default/Service Worker/CacheStorage").exists()

    second = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)
    assert set(second["already_patched"]) == set(compat.LKG_PATCHES)


def make_raw_package_archive(
    path: Path,
    members: list[tuple[str, bytes, bytes | None]],
) -> tuple[Path, str]:
    archive = path / "raw-oracle.tgz"
    with tarfile.open(archive, mode="w:gz") as package:
        for name, member_type, content in members:
            member = tarfile.TarInfo(name)
            member.type = member_type
            member.mode = 0o644
            member.mtime = 0
            if member_type == tarfile.DIRTYPE:
                package.addfile(member)
            elif member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "package/target"
                package.addfile(member)
            else:
                payload = content or b""
                member.size = len(payload)
                package.addfile(member, io.BytesIO(payload))
    return archive, "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode("ascii")


def scoped_sample_fixture(tmp_path: Path, content: bytes = b"before\n") -> tuple[object, Path, Path, Path]:
    compat = load_compat()
    tmp_path.mkdir(parents=True, exist_ok=True)
    package = tmp_path / "package"
    package.mkdir()
    package_json = json.dumps({"version": "0.18.0"}).encode("utf-8")
    (package / "package.json").write_bytes(package_json)
    target = package / "sample.txt"
    target.write_bytes(content)
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    compat.SCOPED_PATCHES = {
        "webjjonku-linux": {
            "0.18.0": {
                "sample.txt": {
                    "patch": "sample.patch",
                    "pristine": digest(b"before\n"),
                    "patched": digest(b"after\n"),
                }
            }
        }
    }
    archive, integrity = make_package_archive(tmp_path, {"package.json": package_json, "sample.txt": content})
    compat.SCOPED_PACKAGE_INTEGRITIES = {"webjjonku-linux": {"0.18.0": integrity}}
    compat.patch_root = lambda version=compat.SUPPORTED_VERSION: patches
    return compat, package, target, archive


def stub_scoped_node(monkeypatch: pytest.MonkeyPatch, compat: object) -> None:
    monkeypatch.setattr(compat.shutil, "which", lambda _name: "node")
    original_run = compat.subprocess.run

    def run(command: object, *args: object, **kwargs: object) -> object:
        if command == ["node", "--version"]:
            return type("NodeResult", (), {"returncode": 0, "stdout": "v24.0.0\n"})()
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(
        compat.subprocess,
        "run",
        run,
    )


def test_scoped_archive_integrity_and_read_fail_before_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat, package, target, archive = scoped_sample_fixture(tmp_path)
    stub_scoped_node(monkeypatch, compat)
    applied: list[object] = []
    monkeypatch.setattr(compat, "_apply_patch", lambda *_args, **_kwargs: applied.append(object()))
    compat.SCOPED_PACKAGE_INTEGRITIES["webjjonku-linux"]["0.18.0"] = "sha512-not-the-archive"

    with pytest.raises(compat.OracleCompatError) as wrong_integrity:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0", profile="webjjonku-linux", package_root=package, package_archive=archive
        )
    assert wrong_integrity.value.code == "ORACLE_PACKAGE_INTEGRITY_MISMATCH"
    assert target.read_bytes() == b"before\n"
    assert not applied

    compat.SCOPED_PACKAGE_INTEGRITIES["webjjonku-linux"]["0.18.0"] = compat.sha512_integrity(archive)
    with pytest.raises(compat.OracleCompatError) as missing_archive:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0", profile="webjjonku-linux", package_root=package, package_archive=tmp_path / "missing.tgz"
        )
    assert missing_archive.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"
    assert target.read_bytes() == b"before\n"
    assert not applied

    original_read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == archive:
            raise OSError("synthetic archive read denial")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(compat.OracleCompatError) as unreadable_archive:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0", profile="webjjonku-linux", package_root=package, package_archive=archive
        )
    assert unreadable_archive.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"
    assert target.read_bytes() == b"before\n"
    assert not applied


@pytest.mark.parametrize("profile", ["other-linux", ""])
def test_scoped_api_rejects_unknown_or_empty_profile(profile: str, tmp_path: Path) -> None:
    compat = load_compat()
    with pytest.raises(compat.OracleCompatError) as invalid_profile:
        compat.ensure_scoped_oracle_compatibility("oracle 0.18.0", profile=profile, package_root=tmp_path)
    assert invalid_profile.value.code == "ORACLE_COMPAT_PROFILE_UNVALIDATED"


def test_scoped_api_rejects_none_profile(tmp_path: Path) -> None:
    compat = load_compat()
    with pytest.raises(compat.OracleCompatError) as captured:
        compat.ensure_scoped_oracle_compatibility("oracle 0.18.0", profile=None, package_root=tmp_path)  # type: ignore[arg-type]
    assert captured.value.code == "ORACLE_COMPAT_PROFILE_UNVALIDATED"


@pytest.mark.parametrize(
    ("node", "stdout", "returncode"),
    [
        (None, "", 0),
        ("node", "v23.9.0\n", 0),
        ("node", "v27.0.0\n", 0),
        ("node", "not-a-version\n", 0),
        ("node", "v24.0.0\n", 1),
    ],
)
def test_scoped_node_runtime_rejects_invalid_runtime(
    monkeypatch: pytest.MonkeyPatch, node: str | None, stdout: str, returncode: int
) -> None:
    compat = load_compat()
    monkeypatch.setattr(compat.shutil, "which", lambda _name: node)
    called: list[object] = []
    monkeypatch.setattr(
        compat.subprocess,
        "run",
        lambda *_args, **_kwargs: called.append(object()) or type("NodeResult", (), {"returncode": returncode, "stdout": stdout})(),
    )
    with pytest.raises(compat.OracleCompatError) as unsupported:
        compat._verify_scoped_node_runtime("webjjonku-linux")
    assert unsupported.value.code == "ORACLE_NODE_VERSION_UNSUPPORTED"
    assert bool(called) is (node is not None)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "/package/dist/absolute.js",
        "package/../traversal.js",
        "package/dist/control\x01.js",
        "outside-root.js",
        "package/COM1.txt",
        "package/dist/trailing-space ",
        "package/dist/trailing-dot.",
    ],
)
def test_scoped_archive_rejects_unsafe_member_names(tmp_path: Path, unsafe_name: str) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    archive, integrity = make_raw_package_archive(tmp_path, [(unsafe_name, tarfile.REGTYPE, b"unsafe\n")])
    with pytest.raises(compat.OracleCompatError) as unsafe_archive:
        compat._verify_scoped_package_archive(package, archive, expected_integrity=integrity, contracts={})
    assert unsafe_archive.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"


def test_scoped_archive_rejects_nul_member_name() -> None:
    compat = load_compat()
    with pytest.raises(compat.OracleCompatError) as unsafe_name:
        compat._safe_archive_relative("package/dist/nul\x00.js")
    assert unsafe_name.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_scoped_archive_rejects_link_members(tmp_path: Path, member_type: bytes) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    archive, integrity = make_raw_package_archive(tmp_path, [("package/dist/link.js", member_type, None)])
    with pytest.raises(compat.OracleCompatError) as link_member:
        compat._verify_scoped_package_archive(package, archive, expected_integrity=integrity, contracts={})
    assert link_member.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"


def test_scoped_archive_rejects_duplicate_and_casefolded_members(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    (package / "dist").mkdir(parents=True)
    (package / "dist" / "A.js").write_bytes(b"same\n")
    archive, integrity = make_raw_package_archive(
        tmp_path,
        [
            ("package/dist/A.js", tarfile.REGTYPE, b"same\n"),
            ("package/dist/A.js", tarfile.REGTYPE, b"same\n"),
        ],
    )
    with pytest.raises(compat.OracleCompatError) as duplicate:
        compat._verify_scoped_package_archive(package, archive, expected_integrity=integrity, contracts={})
    assert duplicate.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"

    archive, integrity = make_raw_package_archive(
        tmp_path,
        [
            ("package/dist/A.js", tarfile.REGTYPE, b"same\n"),
            ("package/dist/a.js", tarfile.REGTYPE, b"same\n"),
        ],
    )
    with pytest.raises(compat.OracleCompatError) as casefolded:
        compat._verify_scoped_package_archive(package, archive, expected_integrity=integrity, contracts={})
    assert casefolded.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"


def test_scoped_archive_rejects_directory_only_payload(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    archive, integrity = make_raw_package_archive(
        tmp_path, [("package", tarfile.DIRTYPE, None), ("package/dist", tarfile.DIRTYPE, None)]
    )
    with pytest.raises(compat.OracleCompatError) as empty_payload:
        compat._verify_scoped_package_archive(package, archive, expected_integrity=integrity, contracts={})
    assert empty_payload.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"


@pytest.mark.parametrize(("limit_name", "members"), [("SCOPED_ARCHIVE_MAX_FILES", 2), ("SCOPED_ARCHIVE_MAX_BYTES", 1)])
def test_scoped_archive_rejects_resource_ceiling_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit_name: str, members: int
) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    files = {f"file-{index}.js": b"xx" for index in range(members)}
    for relative, content in files.items():
        (package / relative).write_bytes(content)
    archive, integrity = make_package_archive(tmp_path, files)
    monkeypatch.setattr(compat, limit_name, 1)
    monkeypatch.setattr(compat, "_scan_installed_package_tree", lambda _root: {relative: package / relative for relative in files})
    with pytest.raises(compat.OracleCompatError) as exhausted:
        compat._verify_scoped_package_archive(package, archive, expected_integrity=integrity, contracts={})
    assert exhausted.value.code == "ORACLE_PACKAGE_ARCHIVE_INVALID"


def test_scoped_tree_rejects_symlink_root_and_nested_link(tmp_path: Path) -> None:
    compat = load_compat()
    external = tmp_path / "external"
    external.mkdir()
    root_link = tmp_path / "root-link"
    nested_root = tmp_path / "nested-root"
    nested_root.mkdir()
    nested_link = nested_root / "link.js"
    try:
        root_link.symlink_to(external, target_is_directory=True)
        nested_link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")

    empty_archive, integrity = make_package_archive(tmp_path, {})
    with pytest.raises(compat.OracleCompatError) as linked_root:
        compat._verify_scoped_package_archive(root_link, empty_archive, expected_integrity=integrity, contracts={})
    assert linked_root.value.code == "ORACLE_PACKAGE_TREE_MISMATCH"
    with pytest.raises(compat.OracleCompatError) as nested_link_error:
        compat._scan_installed_package_tree(nested_root.resolve(strict=True))
    assert nested_link_error.value.code == "ORACLE_PACKAGE_TREE_MISMATCH"
    assert nested_link_error.value.evidence["path"] == "link.js"


def test_scoped_tree_rejects_hardlinked_patch_target_before_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat, package, target, archive = scoped_sample_fixture(tmp_path)
    sibling = package / "hardlink-source.txt"
    try:
        os.link(target, sibling)
    except OSError as exc:
        pytest.skip(f"hardlink creation is unavailable on this platform: {exc}")
    stub_scoped_node(monkeypatch, compat)
    applied: list[object] = []
    monkeypatch.setattr(compat, "_apply_patch", lambda *_args, **_kwargs: applied.append(object()))
    with pytest.raises(compat.OracleCompatError) as hardlinked:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0", profile="webjjonku-linux", package_root=package, package_archive=archive
        )
    assert hardlinked.value.code == "ORACLE_PACKAGE_TREE_MISMATCH"
    assert target.read_bytes() == b"before\n"
    assert not applied


def test_scoped_patch_hash_gates_pristine_and_rolls_back_patched_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compat, package, target, archive = scoped_sample_fixture(tmp_path, content=b"unexpected\n")
    stub_scoped_node(monkeypatch, compat)
    with pytest.raises(compat.OracleCompatError) as pristine_mismatch:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0", profile="webjjonku-linux", package_root=package, package_archive=archive
        )
    assert pristine_mismatch.value.code == "ORACLE_PACKAGE_CONTRACT_MISMATCH"
    assert target.read_bytes() == b"unexpected\n"

    compat, package, target, archive = scoped_sample_fixture(tmp_path / "patched")
    stub_scoped_node(monkeypatch, compat)
    compat.SCOPED_PATCHES["webjjonku-linux"]["0.18.0"]["sample.txt"]["patched"] = digest(b"wrong\n")
    with pytest.raises(compat.OracleCompatError) as patched_mismatch:
        compat.ensure_scoped_oracle_compatibility(
            "oracle 0.18.0", profile="webjjonku-linux", package_root=package, package_archive=archive
        )
    assert patched_mismatch.value.code == "ORACLE_PATCH_HASH_MISMATCH"
    assert target.read_bytes() == b"before\n"


def test_default_path_remains_isolated_from_scoped_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat = load_compat()
    monkeypatch.setattr(compat, "SCOPED_PATCHES", None)
    monkeypatch.setattr(compat, "SCOPED_PACKAGE_INTEGRITIES", None)
    monkeypatch.setattr(compat, "SCOPED_NODE_MAJOR_RANGES", None)
    package = tmp_path / "default-package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.18.0"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "default-patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {"sample.txt": {"patch": "sample.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    compat.patch_root = lambda version=compat.SUPPORTED_VERSION: patches
    result = compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=package, backup_root=tmp_path / "backup")
    assert result["changed"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
