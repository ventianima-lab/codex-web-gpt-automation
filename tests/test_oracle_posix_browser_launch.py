from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest


def load_module(name: str):
    path = Path(__file__).resolve().parents[1] / "bin" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(os.name == "nt", reason="Chromium Unix socket regression")
def test_deep_run_keeps_exact_profile_binding_and_can_bind_chromium_socket(tmp_path):
    state = load_module("chatgpt_oracle_state")
    roots = [tmp_path / ("deep-project-" * 8) / run / "browser-temp" for run in ("first", "second")]
    aliases = []
    try:
        for root in roots:
            env = state.browser_temp_environment(root, base_env={"DISPLAY": ":99"})
            alias = Path(env["TMPDIR"])
            aliases.append(alias)
            assert env["DISPLAY"] == ":99"
            assert alias.resolve() == root.resolve()
            profile = alias / "oracle-browser-abcdef"
            profile.mkdir()
            assert profile.resolve().is_relative_to(root.resolve())
            socket_dir = alias / "org.chromium.Chromium.abcdef"
            socket_dir.mkdir()
            with socket.socket(socket.AF_UNIX) as probe:
                probe.bind(str(socket_dir / "SingletonSocket"))
            assert state.browser_temp_environment(root)["TMPDIR"] == str(alias)
        assert aliases[0] != aliases[1]
    finally:
        for root in roots:
            assert state.cleanup_owned_browser_temp(root)
    assert all(not alias.parent.exists() for alias in aliases)


@pytest.mark.skipif(os.name == "nt", reason="POSIX alias ownership")
def test_cleanup_rejects_retargeted_alias_and_preserves_foreign_files(tmp_path):
    state = load_module("chatgpt_oracle_state")
    root = tmp_path / "run" / "browser-temp"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    saved = foreign / "user.txt"
    saved.write_text("keep")
    alias = Path(state.browser_temp_environment(root)["TMPDIR"])
    try:
        alias.unlink()
        alias.symlink_to(foreign, target_is_directory=True)
        assert not state.cleanup_owned_browser_temp(root)
        assert saved.read_text() == "keep"
        with pytest.raises(state.OracleStateError, match="exact run"):
            state.browser_temp_environment(root)
    finally:
        alias.unlink()
        alias.symlink_to(root, target_is_directory=True)
        assert state.cleanup_owned_browser_temp(root)


def test_windows_temp_path_is_unchanged(tmp_path):
    state = load_module("chatgpt_oracle_state")
    root = tmp_path / "browser-temp"
    env = state.browser_temp_environment(root, platform_name="nt", base_env={})
    assert env == dict.fromkeys(("TEMP", "TMP", "TMPDIR"), str(root.resolve()))
    assert "posix_temp_alias" not in json.loads((root / ".owner.json").read_text())
    assert state.cleanup_owned_browser_temp(root)


def test_compatibility_patches_the_active_npm_cache(tmp_path, monkeypatch):
    compat = load_module("chatgpt_oracle_compat")
    monkeypatch.delenv("ORACLE_PACKAGE_ROOT", raising=False)
    cache = tmp_path / "custom npm cache"
    package = cache / "_npx" / "current" / "node_modules" / "@steipete" / "oracle"
    package.mkdir(parents=True)
    wrong = tmp_path / "AppData" / "Local"
    (wrong / "npm-cache" / "_npx" / "stale" / "node_modules" / "@steipete" / "oracle").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(wrong))
    monkeypatch.setattr(compat.shutil, "which", lambda _: "/npm")

    def run(argv, **kwargs):
        assert argv == ["/npm", "config", "get", "cache"]
        return subprocess.CompletedProcess(argv, 0, str(cache) + "\n", "")

    monkeypatch.setattr(compat.subprocess, "run", run)
    assert compat._candidate_roots() == [package.resolve()]


def test_unresolved_npm_cache_does_not_fall_back_to_other_installation(monkeypatch):
    compat = load_module("chatgpt_oracle_compat")
    monkeypatch.delenv("ORACLE_PACKAGE_ROOT", raising=False)
    monkeypatch.setattr(compat.shutil, "which", lambda _: "/npm")
    monkeypatch.setattr(compat.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "failed"))
    with pytest.raises(compat.OracleCompatError) as error:
        compat._candidate_roots()
    assert error.value.code == "ORACLE_NPM_CACHE_UNRESOLVED"
