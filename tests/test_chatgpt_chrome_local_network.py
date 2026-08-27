from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "chatgpt_chrome_local_network_test", ROOT / "bin" / "chatgpt_chrome_local_network.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_exact_origin_is_required() -> None:
    assert module.policy_contains_origin({"1": "https://chatgpt.com"}) is True
    assert module.policy_contains_origin({"1": "https://chatgpt.com/"}) is True
    assert module.policy_contains_origin({"1": "https://evilchatgpt.com"}) is False
    assert module.policy_contains_origin({"1": "*"}) is False


def test_new_entry_preserves_sparse_existing_names() -> None:
    assert module.next_policy_value_name({"1": "https://example.com", "3": "https://other.example"}) == "2"


def test_split_policy_requires_loopback_allow_and_honors_blockers(monkeypatch) -> None:
    policies = {
        module.POLICY_SUBKEYS["legacy_allow"]: {"1": "https://chatgpt.com"},
        module.POLICY_SUBKEYS["legacy_block"]: {},
        module.POLICY_SUBKEYS["local_allow"]: {"1": "https://chatgpt.com"},
        module.POLICY_SUBKEYS["local_block"]: {},
        module.POLICY_SUBKEYS["loopback_allow"]: {"1": "https://chatgpt.com:443"},
        module.POLICY_SUBKEYS["loopback_block"]: {},
    }
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "_read_windows_policy", lambda subkey: policies[subkey])
    allowed = module.policy_status()
    assert allowed["enabled"] is True
    assert allowed["effective_permission"] == "loopback_network"
    assert allowed["effective_policy"] == "allowed"

    policies[module.POLICY_SUBKEYS["loopback_block"]] = {"1": "https://chatgpt.com"}
    blocked = module.policy_status()
    assert blocked["enabled"] is False
    assert blocked["effective_policy"] == "blocked"


def test_split_local_policy_cannot_authorize_loopback(monkeypatch) -> None:
    policies = {subkey: {} for subkey in module.POLICY_SUBKEYS.values()}
    policies[module.POLICY_SUBKEYS["local_allow"]] = {"1": "https://chatgpt.com"}
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "_read_windows_policy", lambda subkey: policies[subkey])
    status = module.policy_status()
    assert status["enabled"] is False
    assert status["effective_policy"] == "unset"


def test_permission_denial_returns_bounded_manual_fallback(monkeypatch, capsys) -> None:
    def denied(**_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(module, "enable_policy", denied)
    assert module.main(["enable"]) == 2
    output = capsys.readouterr().out
    assert "CHROME_POLICY_WRITE_DENIED" in output
    assert "dedicated Oracle browser profile" in output
