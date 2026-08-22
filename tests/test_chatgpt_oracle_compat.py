from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
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
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "sample.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {"sample.txt": {"patch": "sample.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    compat.patch_root = lambda: patches
    backup = tmp_path / "backup"

    first = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)
    second = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)

    assert first["changed"] == ["sample.txt"]
    assert second["already_patched"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"
    assert (backup / "sample.txt").read_bytes() == b"before\n"


def test_hash_specific_legacy_patch_migrates_without_backup(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
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
    compat.patch_root = lambda: patches
    backup = tmp_path / "backup"

    result = compat.ensure_oracle_compatibility(
        "oracle 0.17.1",
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
        compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=tmp_path)
    assert scoped_version_without_profile.value.code == "ORACLE_VERSION_UNVALIDATED"
    assert scoped_version_without_profile.value.evidence["supported"] == compat.SUPPORTED_VERSION

    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"unknown\n")
    compat.PATCHES = {"sample.txt": {"patch": "missing.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
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

    with pytest.raises(compat.OracleCompatError) as default_path:
        compat.ensure_oracle_compatibility("oracle 0.18.0", package_root=package)
    assert default_path.value.code == "ORACLE_VERSION_UNVALIDATED"

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
    assert set(compat.PATCHES) <= touched
    node = shutil.which("node")
    assert node is not None, "Node.js is required to validate the patched Oracle source"
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
    assert compat.sha256_file(target) == compat.PATCHES["dist/src/browser/actions/thinkingTime.js"]["patched"]
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
    assert compat.sha256_file(browser_config) == compat.PATCHES["dist/src/browser/config.js"]["patched"]
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
    assert compat.sha256_file(profile_copy) == compat.PATCHES["dist/src/browser/profileCopy.js"]["patched"]
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
    assert set(second["already_patched"]) == set(compat.PATCHES)
