from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

SUPPORTED_VERSION = "0.17.1"
CREATE_NO_WINDOW = 0x08000000
# Retained only to document the old package lineage.  New work validates and
# patches the published 0.17.1 package below; 0.16.1 is not accepted anymore.
PATCHES_0161 = {
    "dist/src/cli/browserTabs.js": {
        "patch": "browserTabs.patch",
        "pristine": "05256692ffa9b35415346963adde5ff42aeacd78ce46dd6f484496678f5d0281",
        "patched": "9329e259f030ecb4a935fb9e368bf55074bf0afe7ed5e5a0c6206a5f2bbacee4",
        "legacy_patched": ["1a6d3b9d7044d84300f630fe669b16d9cfec3925c427cfb4c3d1291205406dab"],
        "legacy_patch": "browserTabs.pre-readiness.patch",
    },
    "dist/src/browser/chromeLifecycle.js": {
        "patch": "chromeLifecycle.patch",
        "pristine": "9eaffd8264051266581548ea9dbee1152bd94b7a6032ed0441b1ba3c11c5b5e9",
        "patched": "d852372c9c16c9a130a280001e62312542092b0c38397907897217f8af0c559d",
    },
    "dist/src/browser/recoverConversation.js": {
        "patch": "recoverConversation.patch",
        "pristine": "8c7d841bc078af20c8922ec435f62e00df7a40605583fbd89334696b3ddb386b",
        "patched": "168d665fa7c6cc0ef5094a990e94e7a3ae57f2d3bebcc5c2625cb6cff0cb89b1",
        "legacy_patched": ["650ffe9bdbbaf799510e8cacaa8ba8407322bbbb175e790a3cf7777fa14772fe"],
    },
    "dist/src/browser/profileCopy.js": {
        "patch": "profileCopy.patch",
        "pristine": "06c692861f8a4c1a8769f957b9c582426a13bf4972262c47c1f24a87b239064f",
        "patched": "71459a25b7c46f57bae6f23a5498301f6f6a1d39addf0c1cd4eee1d99b03372c",
    },
    "dist/src/cli/browserConfig.js": {
        "patch": "browserConfig.patch",
        "pristine": "2e4bbf102eed4276e105671244c5bfcdf6870ea72f304d8bb7d2ede10b8872c6",
        "patched": "52de31fff0ee71a9f9ed96400565104ff19226274335433732dfc4a932ff2e93",
    },
    "dist/src/browser/index.js": {
        "patch": "browserIndex.patch",
        "pristine": "ea7461262078363517ad16fe57ac8766f06f037782954987520375dcbfd1c7af",
        "patched": "5f7bc607dae4667ad860d2aa125c138c053190e33f206237c24f5c6aab4bf14c",
        "legacy_patched": [
            "9168df2b3e8c4d1c962d05b198ceab1a9df9e50c7573453673212905e2bc5eba",
        ],
    },
    "dist/src/browser/actions/assistantResponse.js": {
        "patch": "assistantResponse.patch",
        "pristine": "0bbc106f79c6abf253690c83794a2dab1b432378f57e16542d15cfcd5365e16d",
        "patched": "18661304c7fb545bc327876d38045818cbd23257488137836d43661be8742af4",
    },
    "dist/src/browser/actions/promptComposer.js": {
        "patch": "promptComposer.patch",
        "pristine": "db090a5fb6d13c4c88a68b5e474a53a19c3857295a64c3ba4a0eef1868d06000",
        "patched": "02874d0f2fcd0f45c2c50385893a210e2be5822e1831fa81b99944728ed1cb79",
        "legacy_patched": [
            "a4bd36940deca4fd4ca63536df863fff330e438c82d47871834d56cdc064f2b5",
            "3eb83d2b1fc0d9e097a26eca282dc36c93dac1e8485410f8c3617c826538e409",
            # Pre-downgrade build that hard-failed a run when the app mention
            # overlay or pill could not be observed.
            "446ee00d52111c6ba41eea9b60752374603bd4ae3e52985e129e9f5625dadb28",
        ],
    },
    "dist/src/browser/actions/modelSelection.js": {
        "patch": "modelSelection.patch",
        "pristine": "62351158216c0f9f81652f072413487d2db12cd20a1cf7c21575a3f3a2074573",
        "patched": "7e19a5bfd10668929d24961259c4ddedfdd8c26bc85b3ac4672c29f1f40f74fc",
    },
}

PATCHES = {
    "dist/src/browser/actions/assistantResponse.js": {
        "patch": "assistantResponse.generation-error.patch",
        "pristine": "0bbc106f79c6abf253690c83794a2dab1b432378f57e16542d15cfcd5365e16d",
        "patched": "939f840d74843967c1ec2e914185423e76ec01d7c5caa742085d0fbac41d4aa6",
    },
    "dist/src/browser/chromeLifecycle.js": {
        "patch": "chromeLifecycle.windows-hidden.patch",
        "pristine": "312b45c44d4cd69a3a057e7bd1584b58182b4b37bc88f6ce6c7d11e216267c81",
        "patched": "b5a1a04417d2703921274af78c112d6dad03d69f502c778771cea10e5061256b",
    },
    "dist/src/browser/recoverConversation.js": {
        "patch": "recoverConversation.copy-profile.patch",
        "pristine": "d7e39d21acf07e6d227e761944519e11cd8d93930629cc87555d7de75a42d1ca",
        "patched": "31a17e1a2c3dccddf09db42b290fad517638b88c9a659e9fb5dba7307c588a61",
    },
    "dist/src/browser/profileCopy.js": {
        "patch": "profileCopy.windows-native.patch",
        "pristine": "06c692861f8a4c1a8769f957b9c582426a13bf4972262c47c1f24a87b239064f",
        "patched": "71459a25b7c46f57bae6f23a5498301f6f6a1d39addf0c1cd4eee1d99b03372c",
    },
    "dist/src/browser/config.js": {
        "patch": "browserConfig.copy-profile-windows.patch",
        "pristine": "aa4cd86b70217d08a3178c52c20eff621b553ef18b8d1adff9b930ad8e9c3b4a",
        "patched": "9d2dbde2801ab39be9d81f2e84646592ce45063df571c480b09475a2861769c5",
    },
    "dist/src/browser/actions/thinkingTime.js": {
        "patch": "thinkingTime.strict.patch",
        "pristine": "508f1fbc175b82e6bfd4c978da6199306800615f432e28d7721c155c402795ca",
        "patched": "f2244ec9e113cffbae073673c9adc3cfc70adb58a4fc64b241f85700ff3def8c",
        "legacy_patched": [
            "536571fccc3f8137bfbf0ea96dfd827f1eabdaf92f93fe7cff92af242ef01d53",
            "fe6db3c1d48ccf7eff212dab7e69a2b3c7439f44b5cc823d474aa4fbd0925151",
            "ce0fa250ba4b28aeff9e3e80267b3f55bd08f7d25c9890a0eb09debcae447b8b",
            "686e80ee7480686622eab7bc8863eccdf3ad57e64f662bfcbfbc4852802c7aaa",
            "4e73e1c1d9c04e7bea7811a5e32bf17c559a2e1171581dc4cc33f48163ef28e7",
            "374f0fabd62ea82ecf359c3050995da7a3de2d791905d04742f91ebe098d910a",
            "864f8365ecbd0aef9b631f7ae61c80b3e43424dc37c34cdfd5c6e5aa06b0c1b3",
            "d8fbe1394314efaa38343539ad7be519212fd5301f74e4aa92336f6925e3b5fd",
            "9ac1cab3200fb848ca2f88c07f98b19d94c7d4ad5a9b2e578c1c5a9dee4df15f",
            "2baba20f9162eea8b4659ff42d85c26064d037bb18dd90f2022cf4764ddd710d",
            "0cb7bf4774e5507fb97682cf4e350fea03998c2a44548065bf8e9eb57fe16707",
            "b55897a9d90627b226e39e77339819e446927ffc66f78181f5c2851cbcfe5f97",
            "3f969712b184588d1f34ef4f55b439c86256d112bb0fa1688bb473b61fd3dcc3",
            "d01b1d254041c35a300d44f7b3940a89d5406bdf30027bdc7c579a074c5ca5f3",
        ],
        "legacy_patch": "thinkingTime.strict.pre-power.patch",
        "legacy_patches": {
            "fe6db3c1d48ccf7eff212dab7e69a2b3c7439f44b5cc823d474aa4fbd0925151":
                "thinkingTime.strict.broken-power.patch",
            "ce0fa250ba4b28aeff9e3e80267b3f55bd08f7d25c9890a0eb09debcae447b8b":
                "thinkingTime.strict.double-escaped-power.patch",
            "686e80ee7480686622eab7bc8863eccdf3ad57e64f662bfcbfbc4852802c7aaa":
                "thinkingTime.strict.single-escaped-power.patch",
            "4e73e1c1d9c04e7bea7811a5e32bf17c559a2e1171581dc4cc33f48163ef28e7":
                "thinkingTime.strict.regex-power.patch",
            "374f0fabd62ea82ecf359c3050995da7a3de2d791905d04742f91ebe098d910a":
                "thinkingTime.strict.compact-power.patch",
            "864f8365ecbd0aef9b631f7ae61c80b3e43424dc37c34cdfd5c6e5aa06b0c1b3":
                "thinkingTime.strict.hidden-slider.patch",
            "d8fbe1394314efaa38343539ad7be519212fd5301f74e4aa92336f6925e3b5fd":
                "thinkingTime.strict.pro-proof-model-bound.patch",
            "9ac1cab3200fb848ca2f88c07f98b19d94c7d4ad5a9b2e578c1c5a9dee4df15f":
                "thinkingTime.strict.null-model-menu-closed.patch",
            "2baba20f9162eea8b4659ff42d85c26064d037bb18dd90f2022cf4764ddd710d":
                "thinkingTime.strict.pre-outer-model-proof.patch",
            "0cb7bf4774e5507fb97682cf4e350fea03998c2a44548065bf8e9eb57fe16707":
                "thinkingTime.strict.pre-visible-advanced-proof.patch",
            "b55897a9d90627b226e39e77339819e446927ffc66f78181f5c2851cbcfe5f97":
                "thinkingTime.strict.pre-advanced-view-sibling.patch",
            "3f969712b184588d1f34ef4f55b439c86256d112bb0fa1688bb473b61fd3dcc3":
                "thinkingTime.strict.pre-stable-pro-proof.patch",
            "d01b1d254041c35a300d44f7b3940a89d5406bdf30027bdc7c579a074c5ca5f3":
                "thinkingTime.strict.pre-menu-scoped-proof.patch",
        },
    },
}


class OracleCompatError(RuntimeError):
    def __init__(self, code: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_version(package_root: Path) -> str:
    try:
        value = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OracleCompatError("ORACLE_PACKAGE_INVALID", "Oracle package.json is unreadable", {"root": str(package_root)}) from exc
    return str(value.get("version") or "").strip()


def _candidate_roots() -> list[Path]:
    override = str(os.environ.get("ORACLE_PACKAGE_ROOT") or "").strip()
    if override:
        return [Path(override).expanduser().resolve()]
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    roots = list((local / "npm-cache" / "_npx").glob("*/node_modules/@steipete/oracle"))
    return sorted((path.resolve() for path in roots if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)


def resolve_package_root(version: str = SUPPORTED_VERSION) -> Path:
    candidates = _candidate_roots()
    matching = [path for path in candidates if package_version(path) == version]
    if not matching:
        raise OracleCompatError(
            "ORACLE_PACKAGE_NOT_FOUND",
            "The tested Oracle package is not installed in the npx cache",
            {"version": version, "candidates": [str(path) for path in candidates[:8]]},
        )
    return matching[0]


def resolve_package_roots(version: str = SUPPORTED_VERSION) -> list[Path]:
    candidates = _candidate_roots()
    matching = [path for path in candidates if package_version(path) == version]
    if not matching:
        raise OracleCompatError(
            "ORACLE_PACKAGE_NOT_FOUND",
            "The tested Oracle package is not installed in the npx cache",
            {"version": version, "candidates": [str(path) for path in candidates[:8]]},
        )
    return matching


def patch_root() -> Path:
    return Path(__file__).resolve().parent / "oracle-compat" / SUPPORTED_VERSION


def _git_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": startup}


def _apply_patch(package_root: Path, patch_path: Path, *, reverse: bool = False) -> None:
    isolated_env = os.environ.copy()
    # `git apply` also works outside a repository. Prevent a package root used
    # inside a test/worktree from being silently rebound to that parent repo.
    isolated_env["GIT_CEILING_DIRECTORIES"] = str(package_root.parent)
    patch_bytes = patch_path.read_bytes().replace(b"\r\n", b"\n")
    check = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--ignore-space-change", *( ["-R"] if reverse else [] ), "--check", "-"],
        cwd=str(package_root),
        input=patch_bytes,
        capture_output=True,
        check=False,
        env=isolated_env,
        **_git_kwargs(),
    )
    if check.returncode != 0:
        raise OracleCompatError(
            "ORACLE_PATCH_CHECK_FAILED",
            "Oracle compatibility patch no longer matches the installed package",
            {"patch": str(patch_path), "stderr": (check.stderr or b"").decode("utf-8", errors="replace").strip()[-1200:]},
        )
    applied = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--ignore-space-change", *( ["-R"] if reverse else [] ), "-"],
        cwd=str(package_root),
        input=patch_bytes,
        capture_output=True,
        check=False,
        env=isolated_env,
        **_git_kwargs(),
    )
    if applied.returncode != 0:
        raise OracleCompatError(
            "ORACLE_PATCH_APPLY_FAILED",
            "Oracle compatibility patch could not be applied",
            {"patch": str(patch_path), "stderr": (applied.stderr or b"").decode("utf-8", errors="replace").strip()[-1200:]},
        )


def _migrate_known_legacy_patch(
    package_root: Path,
    target: Path,
    relative: str,
    patch_path: Path,
    pristine_hash: str,
) -> None:
    """Restore one known former patch level to its verified pristine bytes."""
    with tempfile.TemporaryDirectory(prefix="oracle-compat-migrate-") as temporary:
        staged_root = Path(temporary)
        staged_target = staged_root / Path(relative)
        staged_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, staged_target)
        _apply_patch(staged_root, patch_path, reverse=True)
        if sha256_file(staged_target) != pristine_hash:
            raise OracleCompatError(
                "ORACLE_LEGACY_PATCH_RESTORE_INVALID",
                "Known legacy Oracle patch did not restore the exact pristine bytes",
                {"path": str(target), "patch": str(patch_path)},
            )
        shutil.copy2(staged_target, target)


def ensure_oracle_compatibility(
    resolved_version: str,
    *,
    package_root: Path | None = None,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    version = resolved_version.strip().removeprefix("oracle ").strip()
    if version != SUPPORTED_VERSION:
        raise OracleCompatError(
            "ORACLE_VERSION_UNVALIDATED",
            "Oracle compatibility is validated only for the tested version",
            {"resolved": resolved_version, "supported": SUPPORTED_VERSION},
        )
    roots = resolve_package_roots(version) if package_root is None else [package_root.expanduser().resolve(strict=True)]
    patches = patch_root()
    backup = backup_root or (Path.home() / ".codex" / "state" / "oracle-compat-backups" / version)
    changed: list[str] = []
    already: list[str] = []
    for root in roots:
        if package_version(root) != version:
            raise OracleCompatError("ORACLE_VERSION_MISMATCH", "Oracle package version does not match the resolved CLI version")
        for relative, contract in PATCHES.items():
            target = root / Path(relative)
            current = sha256_file(target)
            item = relative if len(roots) == 1 else f"{root}:{relative}"
            if current == contract["patched"]:
                already.append(item)
                continue
            backup_path = backup / Path(relative)
            if current in contract.get("legacy_patched", []):
                if not backup_path.exists() or sha256_file(backup_path) != contract["pristine"]:
                    legacy_patches = contract.get("legacy_patches")
                    legacy_patch = (
                        legacy_patches.get(current)
                        if isinstance(legacy_patches, dict)
                        else None
                    ) or contract.get("legacy_patch")
                    if not isinstance(legacy_patch, str) or not legacy_patch:
                        raise OracleCompatError(
                            "ORACLE_LEGACY_PATCH_BACKUP_INVALID",
                            "A legacy Oracle patch cannot be migrated without the exact pristine backup",
                            {"path": str(target), "backup": str(backup_path), "actual": current},
                        )
                    _migrate_known_legacy_patch(
                        root, target, relative, patches / legacy_patch, str(contract["pristine"])
                    )
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup_path)
                else:
                    shutil.copy2(backup_path, target)
                current = sha256_file(target)
            if current != contract["pristine"]:
                raise OracleCompatError(
                    "ORACLE_FILE_HASH_MISMATCH",
                    "Oracle compatibility refuses an unknown third-party file",
                    {"path": str(target), "actual": current, "expected": [contract["pristine"], contract["patched"]]},
                )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(target, backup_path)
            _apply_patch(root, patches / str(contract["patch"]))
            actual = sha256_file(target)
            if actual != contract["patched"]:
                raise OracleCompatError(
                    "ORACLE_PATCH_HASH_MISMATCH",
                    "Oracle compatibility patch output hash is unexpected",
                    {"path": str(target), "actual": actual, "expected": contract["patched"]},
                )
            changed.append(item)
    return {
        "ok": True,
        "version": version,
        "package_root": str(roots[0]),
        "package_roots": [str(root) for root in roots],
        "changed": changed,
        "already_patched": already,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Apply the exact Oracle 0.17.1 ChatGPT compatibility patch.")
    parser.add_argument("--resolved-version", default=f"oracle {SUPPORTED_VERSION}")
    parser.add_argument("--package-root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = ensure_oracle_compatibility(args.resolved_version, package_root=args.package_root)
    except OracleCompatError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc), "evidence": exc.evidence}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
