from __future__ import annotations

import importlib.util
import json
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


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "oracle-profile"
    preferences = profile / "Default" / "Preferences"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(
        json.dumps(
            {
                "profile": {
                    "content_settings": {
                        "exceptions": {
                            "notifications": {"https://example.com:443,*": {"setting": 2}},
                            "local_network": {},
                            "loopback_network": {},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return profile


def test_profile_fallback_persists_both_split_permissions_and_preserves_unrelated_settings(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    result = module.enable_profile_permission(profile_dir=profile, codex_home=tmp_path / "codex-home")
    assert result["enabled"] is True
    assert result["mode"] == "oracle-seed-profile"
    status = module.browser_profile_network_status(profile)
    assert status["local_network"] is True
    assert status["loopback_network"] is True
    preferences = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
    exceptions = preferences["profile"]["content_settings"]["exceptions"]
    assert exceptions["notifications"]["https://example.com:443,*"]["setting"] == 2
    assert exceptions["local_network"]["https://chatgpt.com:443,*"]["setting"] == 1
    assert exceptions["loopback_network"]["https://chatgpt.com:443,*"]["setting"] == 1
    receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
    assert Path(receipt["backup_path"]).is_file()
    assert receipt["before_sha256"] != receipt["after_sha256"]


def test_permission_denial_uses_bounded_seed_profile_fallback(monkeypatch, tmp_path: Path, capsys) -> None:
    def denied(**_kwargs):
        raise PermissionError("denied")

    profile = _profile(tmp_path)
    monkeypatch.setattr(module, "enable_policy", denied)
    assert module.main(
        [
            "enable",
            "--profile-dir",
            str(profile),
            "--codex-home",
            str(tmp_path / "codex-home"),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert '"mode": "oracle-seed-profile"' in output
    assert module.browser_profile_loopback_allowed(profile) is True


def test_profile_fallback_refuses_a_live_seed_profile(monkeypatch, tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    before = (profile / "Default" / "Preferences").read_bytes()
    monkeypatch.setattr(module, "_profile_in_use", lambda _profile: True)
    try:
        module.enable_profile_permission(profile_dir=profile, codex_home=tmp_path / "codex-home")
    except RuntimeError as exc:
        assert str(exc) == "ORACLE_CHROME_PROFILE_IN_USE"
    else:
        raise AssertionError("a live Oracle seed profile must fail closed")
    assert (profile / "Default" / "Preferences").read_bytes() == before
