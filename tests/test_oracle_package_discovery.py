from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "chatgpt_oracle_compat.py"


def load_compat():
    name = "chatgpt_oracle_package_discovery_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def discovery_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    compat = load_compat()
    local = tmp_path / "local"
    home = tmp_path / "home"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("ORACLE_PACKAGE_ROOT", raising=False)
    monkeypatch.delenv("npm_config_cache", raising=False)
    monkeypatch.delenv("NPM_CONFIG_CACHE", raising=False)
    monkeypatch.setattr(compat.Path, "home", classmethod(lambda cls: home))
    return compat, local, home


def make_package(cache_root: Path, key: str, *, mtime: int) -> Path:
    package = cache_root / "_npx" / key / "node_modules" / "@steipete" / "oracle"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "0.18.0"}), encoding="utf-8")
    os.utime(package, (mtime, mtime))
    return package.resolve()


@pytest.mark.parametrize("cache_env", ["npm_config_cache", "NPM_CONFIG_CACHE"])
def test_discovers_configured_and_standard_npm_caches(
    discovery_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_env: str
) -> None:
    compat, local, home = discovery_env
    configured_cache = tmp_path / "configured-cache"
    monkeypatch.setenv(cache_env, str(configured_cache))

    expected = [
        make_package(home / ".npm", "home", mtime=40),
        make_package(local / "npm-cache", "local", mtime=30),
        make_package(configured_cache, "configured", mtime=20),
    ]

    assert compat._candidate_roots() == expected


def test_discovers_bounded_codex_packaged_cache(discovery_env) -> None:
    compat, local, _ = discovery_env
    package = make_package(
        local / "Packages" / "OpenAI.Codex_2p2nqsd0c76g0" / "LocalCache" / "Local" / "npm-cache",
        "f56035b9e5b35c96",
        mtime=10,
    )
    outside_pattern = make_package(
        local / "Packages" / "Other.App_123" / "LocalCache" / "Local" / "npm-cache",
        "ignored",
        mtime=20,
    )

    roots = compat._candidate_roots()

    assert roots == [package]
    assert outside_pattern not in roots


def test_package_root_override_remains_exclusive(discovery_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat, local, _ = discovery_env
    discovered = make_package(local / "npm-cache", "default", mtime=20)
    override = tmp_path / "override" / "oracle"
    monkeypatch.setenv("ORACLE_PACKAGE_ROOT", str(override))

    roots = compat._candidate_roots()

    assert roots == [override.resolve()]
    assert discovered not in roots


def test_candidate_roots_are_deduplicated_and_newest_first(discovery_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compat, local, _ = discovery_env
    shared_cache = local / "npm-cache"
    monkeypatch.setenv("npm_config_cache", str(shared_cache))
    monkeypatch.setenv("NPM_CONFIG_CACHE", str(shared_cache))
    older = make_package(shared_cache, "older", mtime=10)
    newer = make_package(shared_cache, "newer", mtime=20)

    assert compat._candidate_roots() == [newer, older]
