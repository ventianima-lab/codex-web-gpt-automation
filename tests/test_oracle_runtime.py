from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def oracle_package(
    root: Path,
    *,
    name: str = "@steipete/oracle",
    version: str = "0.18.0",
    entry: str = "dist/bin/oracle-cli.js",
) -> Path:
    root.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": name, "version": version, "bin": {"oracle": entry}}),
        encoding="utf-8",
    )
    executable = root / "dist" / "bin" / "oracle-cli.js"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/usr/bin/env node\nconsole.log('0.18.0');\n", encoding="utf-8")
    return root


def completed(command: list[str], *, stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_thin_path_discovers_the_bundled_codex_node_runtime(tmp_path: Path) -> None:
    runtime = load("chatgpt_oracle_runtime_thin_path_test", ROOT / "bin" / "chatgpt_oracle_runtime.py")
    package = oracle_package(tmp_path / "npm-cache" / "_npx" / "exact" / "node_modules" / "@steipete" / "oracle")
    local_app_data = tmp_path / "Local App Data"
    node = local_app_data / "Codex" / "Runtimes" / "node-v24.20.0-win-x64" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"node")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        return completed(list(command), stdout="v24.20.0\n")

    command = runtime._resolve_default_oracle_command(
        package_resolver=lambda version: package if version == "0.18.0" else None,
        which=lambda _name: None,
        environment={"LOCALAPPDATA": str(local_app_data), "PATH": ""},
        run_factory=runner,
        platform_name="nt",
    )

    assert command == [str(node.resolve()), str((package / "dist/bin/oracle-cli.js").resolve())]
    assert calls[0][0] == [str(node.resolve()), "--version"]
    assert calls[0][1]["timeout"] == runtime.NODE_PROBE_TIMEOUT_SECONDS


def test_unicode_and_spaces_remain_separate_absolute_argv(tmp_path: Path) -> None:
    runtime = load("chatgpt_oracle_runtime_unicode_test", ROOT / "bin" / "chatgpt_oracle_runtime.py")
    package = oracle_package(tmp_path / "캐시 공간" / "node modules" / "@steipete" / "oracle")
    node = tmp_path / "앱 런타임" / "Node 24" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"node")

    command = runtime._resolve_default_oracle_command(
        package_resolver=lambda _version: package,
        which=lambda name: str(node) if name in {"node", "node.exe"} else None,
        environment={"PATH": ""},
        run_factory=lambda argv, **_kwargs: completed(list(argv), stdout="v24.20.0\n"),
        platform_name="nt",
    )

    assert command == [str(node.resolve()), str((package / "dist/bin/oracle-cli.js").resolve())]
    assert all(Path(value).is_absolute() for value in command)


@pytest.mark.parametrize(
    ("metadata", "expected_code"),
    [
        ({"name": "oracle", "version": "0.18.0", "entry": "dist/bin/oracle-cli.js"}, "ORACLE_PACKAGE_IDENTITY_MISMATCH"),
        ({"name": "@steipete/oracle", "version": "0.17.1", "entry": "dist/bin/oracle-cli.js"}, "ORACLE_PACKAGE_IDENTITY_MISMATCH"),
        ({"name": "@steipete/oracle", "version": "0.18.0", "entry": "dist/bin/not-oracle.js"}, "ORACLE_PACKAGE_IDENTITY_MISMATCH"),
    ],
)
def test_package_identity_mismatch_fails_closed(
    tmp_path: Path,
    metadata: dict[str, str],
    expected_code: str,
) -> None:
    runtime = load(f"chatgpt_oracle_runtime_mismatch_{metadata['name']}_{metadata['version']}_{metadata['entry']}", ROOT / "bin" / "chatgpt_oracle_runtime.py")
    package = oracle_package(
        tmp_path / "oracle",
        name=metadata["name"],
        version=metadata["version"],
        entry=metadata["entry"],
    )

    with pytest.raises(runtime.OracleRuntimeResolutionError) as exc:
        runtime._resolve_default_oracle_command(
            package_resolver=lambda _version: package,
            which=lambda _name: None,
            environment={"PATH": ""},
            run_factory=lambda *_args, **_kwargs: pytest.fail("mismatched package must not execute"),
            platform_name="nt",
        )

    assert exc.value.code == expected_code


def test_npx_fallback_is_exact_pinned_offline_and_proven(tmp_path: Path) -> None:
    runtime = load("chatgpt_oracle_runtime_npx_test", ROOT / "bin" / "chatgpt_oracle_runtime.py")
    package = oracle_package(tmp_path / "oracle")
    npx = tmp_path / "npm tools" / "npx.cmd"
    npx.parent.mkdir(parents=True)
    npx.write_text("@exit /b 0\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        return completed(list(command), stdout="0.18.0\n")

    command = runtime._resolve_default_oracle_command(
        package_resolver=lambda _version: package,
        which=lambda name: str(npx) if name in {"npx", "npx.cmd"} else None,
        environment={"PATH": str(npx.parent)},
        run_factory=runner,
        platform_name="nt",
    )

    assert command == [str(npx.resolve()), "--offline", "--yes", "@steipete/oracle@0.18.0"]
    assert len(calls) == 1
    assert calls[0][0] == [*command, "--version"]
    assert calls[0][1]["env"]["NPM_CONFIG_OFFLINE"] == "true"
    assert calls[0][1]["timeout"] == runtime.NPX_PROBE_TIMEOUT_SECONDS


def test_absent_cache_never_reaches_npx_or_network(tmp_path: Path) -> None:
    runtime = load("chatgpt_oracle_runtime_no_install_test", ROOT / "bin" / "chatgpt_oracle_runtime.py")
    npx = tmp_path / "npx.cmd"
    npx.write_text("@exit /b 0\n", encoding="utf-8")

    def missing(_version: str) -> Path:
        raise RuntimeError("cache miss")

    with pytest.raises(runtime.OracleRuntimeResolutionError) as exc:
        runtime._resolve_default_oracle_command(
            package_resolver=missing,
            which=lambda _name: str(npx),
            environment={"PATH": str(tmp_path)},
            run_factory=lambda *_args, **_kwargs: pytest.fail("cache miss must not execute npx"),
            platform_name="nt",
        )

    assert exc.value.code == "ORACLE_PACKAGE_NOT_FOUND"


def test_explicit_oracle_command_bypasses_state_default_resolution(tmp_path: Path, monkeypatch) -> None:
    state = load("chatgpt_oracle_state_explicit_runtime_test", ROOT / "bin" / "chatgpt_oracle_state.py")
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(tmp_path.parent / f"{tmp_path.name}-host-state"))
    monkeypatch.setattr(
        state,
        "default_oracle_command",
        lambda *_args, **_kwargs: pytest.fail("explicit oracle_command must bypass default resolver"),
    )
    manifest = tmp_path / "job.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.oracle-run/v1",
                "project_root": str(tmp_path.resolve()),
                "mission_path": str(mission.resolve()),
                "app_name": "DevSpace",
                "mode": "browser",
                "oracle_command": ["oracle"],
            }
        ),
        encoding="utf-8",
    )

    config = state.load_manifest(manifest)

    assert config.oracle_command == ("oracle",)


def test_default_manifest_load_remains_logical_and_host_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load("chatgpt_oracle_state_logical_default_test", ROOT / "bin" / "chatgpt_oracle_state.py")
    mission = tmp_path / "mission.md"
    mission.write_text("work", encoding="utf-8")
    monkeypatch.setenv("CODEX_ORACLE_STATE_ROOT", str(tmp_path.parent / f"{tmp_path.name}-host-state"))
    runtime_module = getattr(state, "RUNTIME", None)
    if runtime_module is not None:
        monkeypatch.setattr(
            runtime_module,
            "resolve_default_oracle_command",
            lambda: pytest.fail("manifest loading must not resolve or execute the host runtime"),
        )
    manifest = tmp_path / "job.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.oracle-run/v1",
                "project_root": str(tmp_path.resolve()),
                "mission_path": str(mission.resolve()),
                "app_name": "DevSpace",
                "mode": "browser",
            }
        ),
        encoding="utf-8",
    )

    config = state.load_manifest(manifest, platform_name="nt")

    assert config.oracle_command == ("npx.cmd", "-y", "@steipete/oracle@0.18.0")
