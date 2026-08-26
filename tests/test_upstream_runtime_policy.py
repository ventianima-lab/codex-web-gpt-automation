from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_upstream_runtime_policy.py"
POLICY_PATH = ROOT / "upstream-runtime-policy.json"


def load_module():
    spec = importlib.util.spec_from_file_location("upstream_runtime_policy_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def registry(policy: dict[str, object], runtime: str, latest: str | None = None) -> tuple[str, dict[str, object]]:
    config = policy["runtimes"][runtime]
    current = config["current"]
    lkg = config["last_known_good"]
    chosen_latest = latest or current["version"]
    releases = {
        current["version"]: {"dist": {"integrity": current["integrity"], "tarball": f"https://registry.example/{runtime}-{current['version']}.tgz"}},
        lkg["version"]: {"dist": {"integrity": lkg["integrity"], "tarball": f"https://registry.example/{runtime}-{lkg['version']}.tgz"}},
    }
    if chosen_latest not in releases:
        releases[chosen_latest] = {"dist": {"integrity": current["integrity"], "tarball": f"https://registry.example/{runtime}-{chosen_latest}.tgz"}}
    return config["registry"], {"dist-tags": {"latest": chosen_latest}, "versions": releases}


def fixture(policy: dict[str, object], oracle_latest: str | None = None, devspace_latest: str | None = None) -> dict[str, object]:
    oracle_url, oracle = registry(policy, "oracle", oracle_latest)
    devspace_url, devspace = registry(policy, "devspace", devspace_latest)
    return {"registries": {oracle_url: oracle, devspace_url: devspace}}


def test_checked_in_policy_separates_reporter_from_gated_maintainer_mutation() -> None:
    module = load_module()
    policy = module.validate_policy(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
    assert policy["promotion"]["mode"] == "newest-validated-stable"
    assert policy["promotion"]["watcher_may_promote"] is False
    assert policy["promotion"]["watcher_may_install"] is False
    assert policy["promotion"]["watcher_may_restart"] is False
    assert policy["promotion"]["maintainer_may_promote_after_gates"] is True
    assert policy["promotion"]["maintainer_may_install_after_release"] is True
    assert policy["promotion"]["maintainer_may_restart_in_safe_window"] is True
    assert policy["promotion"]["candidate_reporter"] == "scheduled-read-only-watcher"
    assert policy["promotion"]["promotion_owner"] == "scheduled-codex-maintainer-automation"
    assert policy["promotion"]["validation_owner"] == "scheduled-codex-maintainer-automation-plus-required-ci"
    assert policy["promotion"]["installation_owner"] == "scheduled-codex-maintainer-automation"
    assert policy["promotion"]["restart_owner"] == "scheduled-codex-maintainer-automation-safe-window"
    assert policy["promotion"]["routine_approval"] == "standing-after-all-gates"
    assert policy["promotion"]["exception_approval"] == "explicit-user"
    assert policy["promotion"]["candidate_detection_sla_hours"] == 6
    assert policy["promotion"]["validation_start_sla_hours"] == 24
    assert policy["promotion"]["promotion_target_sla_hours"] == 48
    assert policy["promotion"]["drift_issue_assignee"] == "ventianima-lab"
    assert policy["promotion"]["promotion_automation_name"] == "Validate upstream runtime drift"
    assert policy["promotion"]["promotion_automation_schedule"] == "every-6-hours"
    assert "devspace-open-workspace-same-id-read" in policy["promotion"]["required_gates"]
    assert policy["runtimes"]["oracle"]["current"]["version"] == "0.18.0"
    assert policy["runtimes"]["devspace"]["current"]["version"] == "1.0.8"
    assert policy["runtimes"]["devspace"]["last_known_good"]["version"] == "1.0.7"


def test_offline_fixture_reports_sync_and_hash_bound_archives() -> None:
    module = load_module()
    policy = module.validate_policy(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
    report = module.check(policy, fixture(policy), timeout=0.01)
    assert report["in_sync"] is True
    assert report["drifted"] == []
    assert report["errors"] == []
    assert report["runtimes"]["oracle"]["current"]["integrity"] == policy["runtimes"]["oracle"]["current"]["integrity"]
    assert report["runtimes"]["devspace"]["last_known_good"]["integrity"] == policy["runtimes"]["devspace"]["last_known_good"]["integrity"]


def test_offline_fixture_reports_drift_without_promoting_or_mutating() -> None:
    module = load_module()
    policy = module.validate_policy(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
    report = module.check(policy, fixture(policy, oracle_latest="0.18.1"), timeout=0.01)
    assert report["in_sync"] is False
    assert report["drifted"] == ["oracle"]
    assert report["runtimes"]["oracle"]["current"]["version"] == "0.18.0"
    assert report["runtimes"]["oracle"]["latest"]["version"] == "0.18.1"


def test_rejects_extra_policy_keys_and_integrity_mismatch() -> None:
    module = load_module()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["unexpected"] = True
    try:
        module.validate_policy(policy)
    except module.PolicyError as exc:
        assert "keys must be exactly" in str(exc)
    else:
        raise AssertionError("extra policy keys must fail closed")

    policy = module.validate_policy(json.loads(POLICY_PATH.read_text(encoding="utf-8")))
    bad = fixture(policy)
    oracle_registry = policy["runtimes"]["oracle"]["registry"]
    current = policy["runtimes"]["oracle"]["current"]["version"]
    bad["registries"][oracle_registry]["versions"][current]["dist"]["integrity"] = "sha512-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="
    report = module.check(policy, bad, timeout=0.01)
    assert report["errors"] == ["oracle"]
    assert "current archive integrity" in report["runtimes"]["oracle"]["error"]


def test_rejects_missing_promotion_owner_or_closed_gate() -> None:
    module = load_module()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["promotion"]["promotion_owner"] = "nobody"
    try:
        module.validate_policy(policy)
    except module.PolicyError as exc:
        assert "promotion_owner" in str(exc)
    else:
        raise AssertionError("promotion ownership must fail closed")

    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["promotion"]["required_gates"].remove("devspace-open-workspace-same-id-read")
    try:
        module.validate_policy(policy)
    except module.PolicyError as exc:
        assert "closed gate set" in str(exc)
    else:
        raise AssertionError("missing read-route canary must fail closed")


def test_main_allows_drift_for_workflow_issue_handling(tmp_path: Path, capsys) -> None:
    module = load_module()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture(policy, devspace_latest="1.0.9")), encoding="utf-8")
    output = tmp_path / "report.json"
    assert module.main(["--fixture", str(fixture_path), "--output", str(output), "--allow-drift"]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["drifted"] == ["devspace"]
    assert "devspace" in capsys.readouterr().out


def test_main_fails_drift_without_the_explicit_workflow_override(tmp_path: Path) -> None:
    module = load_module()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture(policy, oracle_latest="0.18.1")), encoding="utf-8")
    assert module.main(["--fixture", str(fixture_path)]) == 2
