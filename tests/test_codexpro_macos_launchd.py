from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "bin" / "codexpro_macos_launchd.py"
    spec = importlib.util.spec_from_file_location("codexpro_macos_launchd_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_launchd_services_use_unique_labels_and_exact_allowed_root(tmp_path: Path) -> None:
    module = load_module()
    codex_home = tmp_path / "codex"
    project = tmp_path / "project"
    project.mkdir()

    values = module.service_plists(codex_home=codex_home, project_root=project.resolve(), python="/usr/bin/python3", npx="/opt/homebrew/bin/npx")

    assert {value["Label"] for value in values.values()} == set(module.LABELS.values())
    assert all(value["CodexProManaged"] is True for value in values.values())
    assert values["supervisor"]["StartInterval"] == 60
    assert values["devspace"]["EnvironmentVariables"]["DEVSPACE_TOOL_MODE"] == "full"
    for value in values.values():
        assert plistlib.loads(plistlib.dumps(value))["Label"] == value["Label"]


def test_launchd_funnel_uses_standard_https_port_for_chatgpt_oauth(tmp_path: Path) -> None:
    module = load_module()
    codex_home = tmp_path / "codex"
    project = tmp_path / "project"
    project.mkdir()

    values = module.service_plists(codex_home=codex_home, project_root=project.resolve(), python="/usr/bin/python3", npx="/opt/homebrew/bin/npx")

    assert values["funnel"]["ProgramArguments"][-3:] == [str(project.resolve()), "--public-port", "443"]
    funnel_path = values["funnel"]["EnvironmentVariables"]["PATH"]
    assert funnel_path.startswith("/opt/homebrew/bin:/usr/local/bin:")
    assert "/Applications/Tailscale.app/" not in funnel_path
