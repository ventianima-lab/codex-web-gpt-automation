from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
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


def test_patch_chain_applies_base_and_post_patch(tmp_path: Path) -> None:
    compat = load_compat()
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    target = package / "sample.txt"
    target.write_bytes(b"before\n")
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "base.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-before\n+middle\n",
        encoding="utf-8",
    )
    (patches / "post.patch").write_text(
        "diff --git a/sample.txt b/sample.txt\n--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1 +1 @@\n-middle\n+after\n",
        encoding="utf-8",
    )
    compat.PATCHES = {
        "sample.txt": {
            "patch": "base.patch",
            "post_patches": ["post.patch"],
            "pristine": digest(b"before\n"),
            "patched": digest(b"after\n"),
        }
    }
    compat.patch_root = lambda: patches

    result = compat.ensure_oracle_compatibility(
        "oracle 0.17.1", package_root=package, backup_root=tmp_path / "backup"
    )

    assert result["changed"] == ["sample.txt"]
    assert target.read_bytes() == b"after\n"


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

    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    (package / "sample.txt").write_bytes(b"unknown\n")
    compat.PATCHES = {"sample.txt": {"patch": "missing.patch", "pristine": digest(b"before\n"), "patched": digest(b"after\n")}}
    with pytest.raises(compat.OracleCompatError) as mismatch:
        compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package)
    assert mismatch.value.code == "ORACLE_FILE_HASH_MISMATCH"


def test_posix_candidate_roots_use_the_npm_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat = load_compat()
    package = tmp_path / "_npx" / "cache-key" / "node_modules" / "@steipete" / "oracle"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "0.17.1"}), encoding="utf-8")
    monkeypatch.delenv("ORACLE_PACKAGE_ROOT", raising=False)
    monkeypatch.setenv("npm_config_cache", str(tmp_path))

    assert compat.resolve_package_root("0.17.1") == package.resolve()


def test_published_0171_patch_requires_extra_high_and_pro_selection_proof(tmp_path: Path) -> None:
    compat = load_compat()
    try:
        source = compat.resolve_package_root()
    except compat.OracleCompatError:
        pytest.skip("published Oracle 0.17.1 cache is unavailable")
    package = tmp_path / "oracle"
    shutil.copytree(source, package)
    backup = tmp_path / "backup"

    result = compat.ensure_oracle_compatibility("oracle 0.17.1", package_root=package, backup_root=backup)
    touched = set(result["changed"]) | set(result["already_patched"])
    assert set(compat.PATCHES) <= touched
    node = shutil.which("node")
    assert node is not None, "Node.js is required to validate the patched Oracle source"
    browser_target = package / "dist/src/browser/index.js"
    browser_text = browser_target.read_text(encoding="utf-8")
    assert "ensureFreshDeepResearchConversation" in browser_text
    assert "deep-research-fresh-conversation-unproven" in browser_text
    assert "assertFreshDeepResearchResult(researchResult.text, baselineAssistantText)" in browser_text
    assert "assistant-response-stale" in browser_text
    browser_syntax = subprocess.run(
        [node, "--check", str(browser_target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert browser_syntax.returncode == 0, browser_syntax.stderr
    assert compat.sha256_file(browser_target) == compat.PATCHES["dist/src/browser/index.js"]["patched"]
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
    assert "readPowerMaximum(view) ?? '?'" in source_text
    assert "`Power ${current} of 5`" not in source_text
    assert "const compact = Array.from(text)" in source_text
    assert "for (const maximum of [5, 4])" in source_text
    assert "String(candidate) + 'of' + String(maximum)" in source_text
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

    extra_high_script = slider_script.replace(
        "Pro,\\u200b 5\\u00a0of\\u202f5", "Extra High,\\u200b 4\\u00a0of\\u202f4"
    ).replace(
        "ensureThinkingTime(Runtime,'heavy'", "ensureThinkingTime(Runtime,'extra-high'"
    ).replace(
        "querySelector:()=>null,getAttribute:()=>null,getBoundingClientRect",
        "querySelector:()=>({getAttribute:(name)=>name==='aria-valuenow'?'3':null}),"
        "getAttribute:()=>null,getBoundingClientRect",
    )
    extra_high = subprocess.run(
        [node, "--input-type=module", "-e", extra_high_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert extra_high.returncode == 0, extra_high.stderr
    assert json.loads(extra_high.stdout) == [
        "[browser] Thinking time: Power 4 of 4 (already selected)"
    ]

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
