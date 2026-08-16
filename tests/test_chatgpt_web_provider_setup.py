from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "chatgpt_web_provider_setup.py"


def load_module():
    name = "chatgpt_web_provider_setup_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(tmp_path: Path):
    codex = tmp_path / ".codex"
    ocx = tmp_path / ".opencodex"
    project = tmp_path / "project"
    for relative in (
        "bin/chatgpt_oracle_dispatch.py",
        "bin/chatgpt_web_provider_bridge.py",
        "scripts/start_chatgpt_web_provider_bridge.ps1",
    ):
        target = codex / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test", encoding="utf-8")
    project.mkdir()
    ocx.mkdir()
    original = {
        "providers": {"existing": {"adapter": "openai-chat", "baseUrl": "https://example.test/v1", "apiKey": "preserve-me"}},
        "defaultProvider": "existing",
        "customModels": [{"id": "existing-id", "provider": "existing", "modelId": "model", "displayName": "Existing"}],
    }
    (ocx / "config.json").write_text(json.dumps(original), encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    return codex, ocx, project, python


def test_configure_adds_provider_without_changing_existing_defaults(tmp_path: Path) -> None:
    setup = load_module()
    codex, ocx, project, python = prepare(tmp_path)
    result = setup.configure(
        project_root=project,
        codex_home=codex,
        opencodex_home=ocx,
        python_executable=python,
        live_apply=lambda _provider: False,
    )
    assert result["ok"] is True
    config = json.loads((ocx / "config.json").read_text(encoding="utf-8"))
    assert config["defaultProvider"] == "existing"
    assert config["providers"]["existing"]["apiKey"] == "preserve-me"
    assert config["providers"][setup.PROVIDER_ID]["allowPrivateNetwork"] is True
    assert config["providers"][setup.PROVIDER_ID]["baseUrl"] == "http://127.0.0.1:10101/v1"
    assert any(item["provider"] == setup.PROVIDER_ID for item in config["customModels"])
    assert list((ocx / "backups").glob("config.pre-web-chatgpt-*.json"))
    bridge = json.loads((codex / "config" / "web-chatgpt-provider.json").read_text(encoding="utf-8"))
    assert len(bridge["auth_token"]) >= 32
    assert bridge["project_root"] == str(project.resolve())


def test_reconfigure_preserves_token_and_deduplicates_model(tmp_path: Path) -> None:
    setup = load_module()
    codex, ocx, project, python = prepare(tmp_path)
    setup.configure(project_root=project, codex_home=codex, opencodex_home=ocx, python_executable=python, live_apply=lambda _provider: False)
    first = json.loads((codex / "config" / "web-chatgpt-provider.json").read_text(encoding="utf-8"))["auth_token"]
    setup.configure(project_root=project, codex_home=codex, opencodex_home=ocx, python_executable=python, live_apply=lambda _provider: False)
    second = json.loads((codex / "config" / "web-chatgpt-provider.json").read_text(encoding="utf-8"))["auth_token"]
    config = json.loads((ocx / "config.json").read_text(encoding="utf-8"))
    assert first == second
    assert sum(item.get("provider") == setup.PROVIDER_ID for item in config["customModels"]) == 1


def test_non_windows_autostart_is_explicitly_external() -> None:
    setup = load_module()
    assert setup.register_autostart(platform_name="posix") == {"ok": True, "changed": False, "mode": "manual-non-windows"}


def test_live_apply_uses_local_admin_token(monkeypatch, tmp_path: Path) -> None:
    setup = load_module()
    token = "ocx_admin_" + "a" * 43
    token_path = tmp_path / ".opencodex" / "admin-api-token"
    token_path.parent.mkdir()
    token_path.write_text(token, encoding="utf-8")
    monkeypatch.setattr(setup.Path, "home", classmethod(lambda cls: tmp_path))
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"success":true}'

    def opener(request, timeout):
        seen["header"] = request.get_header("X-opencodex-api-key")
        seen["timeout"] = timeout
        return Response()

    assert setup.apply_live_provider({"adapter": "openai-chat", "baseUrl": "http://127.0.0.1:10101/v1"}, opener=opener)
    assert seen == {"header": token, "timeout": 15}


def test_launcher_is_hidden_and_mutex_guarded() -> None:
    text = (ROOT / "scripts" / "start_chatgpt_web_provider_bridge.ps1").read_text(encoding="utf-8")
    assert "Local\\CodexWebChatGPTBridge" in text
    assert "-WindowStyle Hidden" in text
    assert "-Wait -PassThru" in text
