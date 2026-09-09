import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_public_docs_brand_assets_and_versions_are_consistent() -> None:
    assert MODULE.check_repository(ROOT) == []


def test_social_preview_has_github_recommended_dimensions() -> None:
    preview = ROOT / "docs" / "assets" / "brand" / "social-preview.png"
    assert MODULE._png_dimensions(preview) == (1280, 640)


def test_workspace_setup_uses_shared_lean_access_policy() -> None:
    text = (ROOT / "skills/chatgpt-workspace-setup/SKILL.md").read_text(encoding="utf-8")
    for required in (
        "docs/AUTOMATION_POLICY.md", "one actual requested project read",
        "model_strategy=current", "thinking_time=pro", "Latest",
        "temporary-chat", "prescribed tool order", "three receipts",
    ):
        assert required in text
    assert "fresh **regular, non-Pro**" not in text
    assert "A Pro submission must never be the first connectivity test" not in text
    assert "read plus no-op command canary" not in text


def test_install_docs_reference_common_policy_and_preserve_model_choice() -> None:
    for name in ("FIRST_INSTALL.md", "FIRST_INSTALL.en.md", "INSTALL_AGENT.md"):
        value = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "AUTOMATION_POLICY.md" in value
        assert "model_strategy=current" in value
        assert "thinking_time=pro" in value
        assert "6 Pro" in value


def test_common_policy_ships_and_preserves_project_validation() -> None:
    import json

    policy = (ROOT / "docs" / "AUTOMATION_POLICY.md").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "install-manifest.json").read_text(encoding="utf-8"))
    assert "docs/AUTOMATION_POLICY.md" in manifest["include"]
    assert "Projects retain relevant tests and domain checks" in policy
    assert "Do not automatically resend the prompt" in policy
    assert "Save the complete result durably before closing the owned tab" in policy
    assert "Historical run" in policy
