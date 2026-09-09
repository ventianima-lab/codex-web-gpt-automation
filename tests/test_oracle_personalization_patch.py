from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "bin" / "chatgpt_oracle_compat.py"
RELATIVE = Path("dist/src/browser/actions/thinkingTime.js")
PRISTINE_HASH = "3d9d06b08417bca3b2d646eb4d46887d26c5de7c068d1e995c73b6b6e2f61199"
PRE_PERSONALIZATION_HASH = "43b866d19344f9e2a3e7cd9bdaac46faead998fbae978c1c63c6a3b183bd1af8"
PERSONALIZATION_HASH = "b11673daaaf45e1ad749c44b3119c1ab71f8d84c0217f379c6e79b03d7b49761"
ARCHIVE_PATCH = "thinkingTime.gpt56-pro-power-slider.pre-personalization.patch"


def load_compat():
    name = "chatgpt_oracle_compat_personalization_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def pristine_fixture() -> Path:
    configured = os.environ.get("ORACLE_018_PACKAGE_ROOT", "").strip()
    source = Path(configured) if configured else Path("__oracle_018_cache_unset__")
    if not source.is_dir():
        if os.environ.get("CI"):
            pytest.fail("CI must prepare the exact published Oracle 0.18.0 package")
        pytest.skip("published Oracle 0.18.0 package root is unavailable")
    return source


def copy_minimal_package(source: Path, destination: Path) -> Path:
    target = destination / RELATIVE
    target.parent.mkdir(parents=True)
    shutil.copy2(source / "package.json", destination / "package.json")
    shutil.copy2(source / RELATIVE, target)
    return target


def apply_single_contract(compat, package: Path, backup: Path):
    contract = compat.PATCHES[RELATIVE.as_posix()]
    return compat._apply_oracle_compatibility(
        "0.18.0",
        package_root=package,
        backup_root=backup,
        contracts={RELATIVE.as_posix(): contract},
        patches=compat.patch_root("0.18.0"),
    )


def test_personalization_patch_migrates_pristine_and_pre_personalization_bytes(
    tmp_path: Path,
) -> None:
    compat = load_compat()
    source = pristine_fixture()
    assert compat.sha256_file(source / RELATIVE) == PRISTINE_HASH
    contract = compat.PATCHES[RELATIVE.as_posix()]
    assert contract["pristine"] == PRISTINE_HASH
    assert contract["patched"] == PERSONALIZATION_HASH
    assert PRE_PERSONALIZATION_HASH in contract["legacy_patched"]
    assert contract["legacy_patches"][PRE_PERSONALIZATION_HASH] == ARCHIVE_PATCH

    pristine_package = tmp_path / "pristine"
    pristine_target = copy_minimal_package(source, pristine_package)
    pristine_result = apply_single_contract(
        compat, pristine_package, tmp_path / "pristine-backup"
    )
    assert RELATIVE.as_posix() in pristine_result["changed"]
    assert compat.sha256_file(pristine_target) == PERSONALIZATION_HASH

    legacy_package = tmp_path / "legacy"
    legacy_target = copy_minimal_package(source, legacy_package)
    compat._apply_patch(
        legacy_package,
        compat.patch_root("0.18.0") / ARCHIVE_PATCH,
    )
    assert compat.sha256_file(legacy_target) == PRE_PERSONALIZATION_HASH
    legacy_backup = tmp_path / "legacy-backup"
    legacy_result = apply_single_contract(compat, legacy_package, legacy_backup)
    assert RELATIVE.as_posix() in legacy_result["changed"]
    assert compat.sha256_file(legacy_target) == PERSONALIZATION_HASH
    assert compat.sha256_file(legacy_backup / RELATIVE) == PRISTINE_HASH

    source_text = pristine_target.read_text(encoding="utf-8")
    hook = source_text.index(
        'process.env.CODEX_ORACLE_TEMPORARY_PERSONALIZATION === "enabled"'
    )
    effort = source_text.index(
        "const result = await evaluateThinkingTimeSelection(Runtime, level, desiredModel);"
    )
    assert hook < effort

    node = shutil.which("node")
    assert node is not None
    source_text = source_text.replace(
        'import { MENU_CONTAINER_SELECTOR, MENU_ITEM_SELECTOR, MODEL_BUTTON_SELECTOR, } from "../constants.js";',
        'const MENU_CONTAINER_SELECTOR=""; const MENU_ITEM_SELECTOR=""; const MODEL_BUTTON_SELECTOR="";',
    ).replace(
        'import { logDomFailure } from "../domDebug.js";',
        "const logDomFailure=async()=>{};",
    ).replace(
        'import { buildClickDispatcher } from "./domEvents.js";',
        'const buildClickDispatcher=()=>"";',
    ).replace(
        'import { BrowserAutomationError } from "../../oracle/errors.js";',
        "class BrowserAutomationError extends Error { constructor(message, details) { super(message); this.details=details; } }",
    )
    test_module = tmp_path / "thinkingTime-personalization-contract.mjs"
    test_module.write_text(source_text, encoding="utf-8")
    assert not source_text.startswith("import ")
    helper = tmp_path / "temporary-personalization-helper.mjs"
    helper.write_text(
        "export async function ensureTemporaryChatPersonalization(Runtime, logger) {"
        " Runtime.events.push('personalization'); logger('[test] personalization'); }\n",
        encoding="utf-8",
    )
    script = tmp_path / "verify-personalization-hook.mjs"
    script.write_text(
        f"""
import {{ ensureThinkingTime }} from {json.dumps(test_module.as_uri())};
const evaluate = async function () {{
  this.events.push("effort");
  return {{ result: {{ value: {{ status: "already-selected", label: "Standard" }} }} }};
}};
process.env.CODEX_ORACLE_TEMPORARY_PERSONALIZATION = "enabled";
process.env.CODEX_ORACLE_TEMPORARY_PERSONALIZATION_HELPER = {json.dumps(helper.as_uri())};
const enabled = {{ events: [], evaluate }};
await ensureThinkingTime(enabled, "standard", () => {{}});
delete process.env.CODEX_ORACLE_TEMPORARY_PERSONALIZATION;
process.env.CODEX_ORACLE_TEMPORARY_PERSONALIZATION_HELPER = "not-a-file-uri";
const disabled = {{ events: [], evaluate }};
await ensureThinkingTime(disabled, "standard", () => {{}});
console.log(JSON.stringify({{ enabled: enabled.events, disabled: disabled.events }}));
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "enabled": ["personalization", "effort"],
        "disabled": ["effort"],
    }
