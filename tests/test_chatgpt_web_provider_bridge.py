from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bin" / "chatgpt_web_provider_bridge.py"


def load_module():
    name = "chatgpt_web_provider_bridge_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bridge():
    return load_module()


def write_config(bridge, tmp_path: Path, **overrides):
    root = tmp_path / "project"
    root.mkdir()
    python = tmp_path / "python.exe"
    dispatch = tmp_path / "dispatch.py"
    python.write_text("", encoding="utf-8")
    dispatch.write_text("", encoding="utf-8")
    value = {
        "schema": bridge.SCHEMA,
        "host": "127.0.0.1",
        "port": 10101,
        "auth_token": "x" * 40,
        "project_root": str(root),
        "app_name": "codex",
        "reasoning_level": "Very High",
        "python_executable": str(python),
        "dispatch_script": str(dispatch),
        "request_root": str(root / ".codex-tmp" / "web-chatgpt-provider"),
        "log_root": str(tmp_path / "logs"),
        "keepalive_seconds": 5,
        **overrides,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, root


def test_load_config_is_loopback_and_project_scoped(bridge, tmp_path: Path) -> None:
    path, root = write_config(bridge, tmp_path)
    config = bridge.load_config(path)
    assert config.host == "127.0.0.1"
    assert config.request_root.is_relative_to(root)

    value = json.loads(path.read_text(encoding="utf-8"))
    value["request_root"] = str(tmp_path / "outside")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(bridge.BridgeError, match="request_root"):
        bridge.load_config(path)


def test_conversation_uses_only_user_and_assistant_text(bridge) -> None:
    messages = bridge.conversation_messages({
        "messages": [
            {"role": "system", "content": "secret system plumbing"},
            {"role": "user", "content": [{"type": "text", "text": "build it"}]},
            {"role": "tool", "content": "tool bytes"},
            {"role": "assistant", "content": "working"},
            {"role": "user", "content": "finish"},
        ]
    })
    assert messages == [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": "working"},
        {"role": "user", "content": "finish"},
    ]


def test_mission_binds_exact_root_and_latest_request(bridge, tmp_path: Path) -> None:
    path, root = write_config(bridge, tmp_path)
    config = bridge.load_config(path)
    mission = bridge.build_mission(config, "request-1", [{"role": "user", "content": "do work"}])
    assert f"Exact project root: `{root}`" in mission
    assert "do work" in mission
    assert "TASK_OUTCOME: EXECUTED" in mission


def test_run_oracle_returns_text_and_preserves_evidence(bridge, tmp_path: Path) -> None:
    path, _ = write_config(bridge, tmp_path)
    config = bridge.load_config(path)
    output = tmp_path / "answer.md"
    output.write_text("done\nTASK_OUTCOME: EXECUTED\n", encoding="utf-8")

    class FakeProcess:
        def __init__(self, command, **kwargs):
            assert "--mode" in command and command[command.index("--mode") + 1] == "direct"
            payload = {"ok": True, "run": {"ok": True, "run_dir": str(tmp_path / "run"), "result": {"status": "complete", "artifacts": {"output": str(output)}}}}
            kwargs["stdout"].write(json.dumps(payload).encode("utf-8"))
            kwargs["stdout"].flush()

        def poll(self):
            return 0

        def wait(self):
            return 0

    answer, evidence = bridge.run_oracle(
        config,
        [{"role": "user", "content": "test"}],
        popen_factory=FakeProcess,
    )
    assert answer == "done"
    assert evidence["status"] == "complete"
    assert list(config.request_root.glob("*/mission.md"))


def test_run_oracle_surfaces_generation_error_without_generic_recovery_message(bridge, tmp_path: Path) -> None:
    path, _ = write_config(bridge, tmp_path)
    config = bridge.load_config(path)

    class FailedProcess:
        def __init__(self, _command, **kwargs):
            payload = {
                "ok": False,
                "run": {
                    "status": "provider_generation_failed",
                    "result": {
                        "status": "attention_required",
                        "transport_status": "post_submit_generation_failed",
                    },
                },
            }
            kwargs["stdout"].write(json.dumps(payload).encode("utf-8"))
            kwargs["stdout"].flush()

        def poll(self):
            return 1

        def wait(self):
            return 1

    with pytest.raises(bridge.BridgeError) as exc:
        bridge.run_oracle(
            config,
            [{"role": "user", "content": "test"}],
            popen_factory=FailedProcess,
        )

    assert exc.value.code == "WEB_CHATGPT_GENERATION_FAILED"
    assert "stopped instead of waiting indefinitely" in str(exc.value)


def test_completion_shape_is_openai_chat_compatible(bridge) -> None:
    value = bridge.completion_object("hello", bridge.MODEL_ID, "chatcmpl_test")
    assert value["choices"][0]["message"] == {"role": "assistant", "content": "hello"}
    assert value["choices"][0]["finish_reason"] == "stop"


def test_stream_handler_closes_connection_after_done() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert 'self.send_header("Connection", "close")' in text
    assert "self.close_connection = True" in text
