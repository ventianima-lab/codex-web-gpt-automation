from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

SUPPORTED_VERSION = "0.16.1"
CREATE_NO_WINDOW = 0x08000000
PATCHES = {
    "dist/src/browser/chromeLifecycle.js": {
        "patch": "chromeLifecycle.patch",
        "pristine": "9eaffd8264051266581548ea9dbee1152bd94b7a6032ed0441b1ba3c11c5b5e9",
        "patched": "d852372c9c16c9a130a280001e62312542092b0c38397907897217f8af0c559d",
    },
    "dist/src/browser/recoverConversation.js": {
        "patch": "recoverConversation.patch",
        "pristine": "8c7d841bc078af20c8922ec435f62e00df7a40605583fbd89334696b3ddb386b",
        "patched": "650ffe9bdbbaf799510e8cacaa8ba8407322bbbb175e790a3cf7777fa14772fe",
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
          "patched": "bf9097d613baadc7b04f4bed6670857bd7c50a584289fbbf1e65a8ec962bca8c",
          "legacy_patched": [
              "9168df2b3e8c4d1c962d05b198ceab1a9df9e50c7573453673212905e2bc5eba",
          ],
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
    "dist/src/browser/actions/thinkingTime.js": {
        "patch": "thinkingTime.patch",
        "pristine": "7d475ed81ccee29a5b4107ed166584bcd3b0266bfd25e02ca7743bf24301e7f0",
        "patched": "f526acb4d187b9833f423832e2ed9c2f001c0424e78af574da050ea50df0474a",
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


def _apply_patch(package_root: Path, patch_path: Path) -> None:
    isolated_env = os.environ.copy()
    # `git apply` also works outside a repository. Prevent a package root used
    # inside a test/worktree from being silently rebound to that parent repo.
    isolated_env["GIT_CEILING_DIRECTORIES"] = str(package_root.parent)
    patch_bytes = patch_path.read_bytes().replace(b"\r\n", b"\n")
    check = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "--check", "-"],
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
        ["git", "-c", "core.autocrlf=false", "apply", "-"],
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
                    raise OracleCompatError(
                        "ORACLE_LEGACY_PATCH_BACKUP_INVALID",
                        "A legacy Oracle patch cannot be migrated without the exact pristine backup",
                        {"path": str(target), "backup": str(backup_path), "actual": current},
                    )
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

    parser = argparse.ArgumentParser(description="Apply the exact Oracle 0.16.1 ChatGPT compatibility patch.")
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
