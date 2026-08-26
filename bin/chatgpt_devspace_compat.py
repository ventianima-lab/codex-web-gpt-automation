from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

SUPPORTED_VERSION = "1.0.8"
# 1.0.7 remains the last-known-good rollback artifact.  It is intentionally
# not accepted by the updater: applying current compatibility patches to an
# old package would hide an incomplete rollback from the service health gates.
LEGACY_LKG_VERSION = "1.0.7"
CREATE_NO_WINDOW = 0x08000000
NATIVE_DEPENDENCY_NAME = "better-sqlite3"
NATIVE_DEPENDENCY_VERSION = "12.11.1"
NATIVE_DEPENDENCY_INTEGRITY = (
    "sha512-dq9AtApgg5PGFtBzPFSBl3HZQjHok5gaQCM6zh2Yk0aSmDCs1CbnVI8/"
    "HgASQkNKsWFpseIO9beg5xxpYhbIfA=="
)
NATIVE_DEPENDENCY_RESOLVED = (
    "https://registry.npmjs.org/better-sqlite3/-/better-sqlite3-12.11.1.tgz"
)
NATIVE_DEPENDENCY_INSTALL_SCRIPT = "prebuild-install || node-gyp rebuild --release"
PATCHES = {
    "dist/artifact-tools.js": {
        "patch": "artifact-audit-readonly.patch",
        "pristine": "53a045b3961875afce5a95b3992aea3d156b64c0268b1d22724d2ed8e2c3aad2",
        "patched": "fd5204b37da657d6183c8394b5ee8bed09bbffd50999946b4d0421897a52dfa7",
    },
    "dist/oauth-provider.js": {
        "patch": "oauth-refresh-replay.patch",
        "pristine": "90ff3fd116735e98af5751de1065538964f6eaae913171223e8e19337b9831b8",
        "patched": "51376673f3def7a3dc05884a409ef52b1ae8580510ba9de86d0b4014b3cd6239",
    },
    "dist/server.js": {
        "patch": "workspace-write-and-read-bridge.patch",
        "pristine": "bf3db902241b631d7c6fbaf12385243b46b4f2d4bb776b6ea7ca6c9d429a3263",
        "patched": "1370524581b75d6b91d281dea52e427004a5ac71c19ac8090d66fe521748760c",
        "upgrades": {
            "659cb1011cd7ab7fb75debb21a44f030001797c2160a42beac527354be93e497": "tool-read-receipts.patch",
        },
    },
    "dist/workspaces.js": {
        "patch": "workspaces.patch",
        "pristine": "e11517f291cac33e37a66e84aeb80e1664a5abd0b6eb1e9bdb933d84c186efad",
        "patched": "68a4c61ae0f509bd40d2a682e0b9bbbac72cb00dc96693f7646e6a535cc872ed",
    },
}


class DevSpaceCompatError(RuntimeError):
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
        raise DevSpaceCompatError(
            "DEVSPACE_PACKAGE_INVALID",
            "DevSpace package.json is unreadable",
            {"root": str(package_root)},
        ) from exc
    return str(value.get("version") or "").strip()


def _candidate_roots() -> list[Path]:
    override = str(os.environ.get("DEVSPACE_PACKAGE_ROOT") or "").strip()
    if override:
        return [Path(override).expanduser().resolve()]
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    candidates = [appdata / "npm" / "node_modules" / "@waishnav" / "devspace"]
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    candidates.extend((local / "npm-cache" / "_npx").glob("*/node_modules/@waishnav/devspace"))
    if os.name != "nt":
        candidates.extend((Path.home() / ".npm" / "_npx").glob("*/node_modules/@waishnav/devspace"))
        completed = subprocess.run(["npm", "root", "--global"], capture_output=True, text=True, check=False)
        if completed.returncode == 0 and completed.stdout.strip():
            candidates.append(Path(completed.stdout.strip()) / "@waishnav" / "devspace")
    return sorted(
        {path.resolve() for path in candidates if path.is_dir()},
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def resolve_package_roots(version: str = SUPPORTED_VERSION) -> list[Path]:
    roots = [path for path in _candidate_roots() if package_version(path) == version]
    if not roots:
        raise DevSpaceCompatError(
            "DEVSPACE_PACKAGE_NOT_FOUND",
            "The tested DevSpace package is not installed",
            {"version": version, "candidates": [str(path) for path in _candidate_roots()[:8]]},
        )
    return roots


def resolve_service_stop_roots() -> list[Path]:
    """Resolve only validated current/LKG package roots for an exact upgrade stop.

    The updater never patches the LKG package, but an in-place upgrade must be
    able to stop the still-running LKG service before starting the tested
    current package.  Identity remains bound to an exact installed CLI path.
    """
    roots: list[Path] = []
    for version in (SUPPORTED_VERSION, LEGACY_LKG_VERSION):
        try:
            roots.extend(resolve_package_roots(version))
        except DevSpaceCompatError as exc:
            if exc.code != "DEVSPACE_PACKAGE_NOT_FOUND":
                raise
    if not roots:
        raise DevSpaceCompatError(
            "DEVSPACE_PACKAGE_NOT_FOUND",
            "No validated current or last-known-good DevSpace package is installed",
            {"versions": [SUPPORTED_VERSION, LEGACY_LKG_VERSION]},
        )
    return list(dict.fromkeys(roots))


def _json_object(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevSpaceCompatError(code, f"{path.name} is unreadable", {"path": str(path)}) from exc
    if not isinstance(value, dict):
        raise DevSpaceCompatError(code, f"{path.name} must contain a JSON object", {"path": str(path)})
    return value


def validate_native_dependency(package_root: Path) -> dict[str, Any]:
    """Bind a native rebuild to the exact tested npx package tree only."""
    root = package_root.expanduser().resolve(strict=True)
    if package_version(root) != SUPPORTED_VERSION:
        raise DevSpaceCompatError(
            "DEVSPACE_NATIVE_DEPENDENCY_INVALID",
            "native preparation accepts only the tested DevSpace package",
            {"root": str(root), "version": package_version(root)},
        )
    node_modules = root.parent.parent
    try:
        workspace = node_modules.parent.resolve(strict=True)
        dependency = (node_modules / NATIVE_DEPENDENCY_NAME).resolve(strict=True)
    except OSError as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_NATIVE_DEPENDENCY_INVALID",
            "tested native dependency is missing from the exact npx package tree",
            {"root": str(root), "dependency": NATIVE_DEPENDENCY_NAME},
        ) from exc
    if not dependency.is_relative_to(workspace):
        raise DevSpaceCompatError(
            "DEVSPACE_NATIVE_DEPENDENCY_INVALID",
            "native dependency escapes the exact npx package tree",
            {"root": str(root), "dependency": str(dependency)},
        )
    metadata = _json_object(dependency / "package.json", code="DEVSPACE_NATIVE_DEPENDENCY_INVALID")
    lock = _json_object(workspace / "package-lock.json", code="DEVSPACE_NATIVE_LOCK_INVALID")
    packages = lock.get("packages")
    entry = packages.get(f"node_modules/{NATIVE_DEPENDENCY_NAME}") if isinstance(packages, dict) else None
    actual = {
        "name": metadata.get("name"),
        "version": metadata.get("version"),
        "install_script": (metadata.get("scripts") or {}).get("install")
        if isinstance(metadata.get("scripts"), dict)
        else None,
        "lock_version": entry.get("version") if isinstance(entry, dict) else None,
        "lock_integrity": entry.get("integrity") if isinstance(entry, dict) else None,
        "lock_resolved": entry.get("resolved") if isinstance(entry, dict) else None,
        "lock_has_install_script": entry.get("hasInstallScript") if isinstance(entry, dict) else None,
    }
    expected = {
        "name": NATIVE_DEPENDENCY_NAME,
        "version": NATIVE_DEPENDENCY_VERSION,
        "install_script": NATIVE_DEPENDENCY_INSTALL_SCRIPT,
        "lock_version": NATIVE_DEPENDENCY_VERSION,
        "lock_integrity": NATIVE_DEPENDENCY_INTEGRITY,
        "lock_resolved": NATIVE_DEPENDENCY_RESOLVED,
        "lock_has_install_script": True,
    }
    if actual != expected or lock.get("lockfileVersion") != 3:
        raise DevSpaceCompatError(
            "DEVSPACE_NATIVE_DEPENDENCY_INVALID",
            "DevSpace native dependency does not match the tested lock contract",
            {"root": str(root), "expected": expected, "actual": actual},
        )
    return {"root": root, "workspace": workspace, "dependency": dependency}


def _native_command(
    argv: Sequence[str], *, cwd: Path, runner: Any, code: str, message: str, timeout_seconds: int = 180
) -> subprocess.CompletedProcess[str]:
    completed = runner(
        list(argv), cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False, timeout=timeout_seconds, **_git_kwargs(),
    )
    if completed.returncode != 0:
        raise DevSpaceCompatError(code, message, {"stderr": (completed.stderr or "").strip()[-1200:]})
    return completed


def prepare_native_runtime(*, package_root: Path | None = None, runner: Any = subprocess.run) -> dict[str, Any]:
    """Prime the exact pinned package and approve/rebuild only its native dependency."""
    roots = resolve_package_roots() if package_root is None else [package_root.expanduser().resolve(strict=True)]
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise DevSpaceCompatError("DEVSPACE_NPM_MISSING", "npm is required for native preparation")
    checks: list[dict[str, Any]] = []
    for root in roots:
        layout = validate_native_dependency(root)
        try:
            native = check_native_runtime(package_root=root, runner=runner)
        except DevSpaceCompatError as exc:
            if exc.code != "DEVSPACE_NATIVE_BINDING_UNAVAILABLE":
                raise
        else:
            checks.append({"root": str(root), "status": "already-loadable", "native_runtime": native})
            continue
        version_result = _native_command(
            [npm, "--version"], cwd=layout["workspace"], runner=runner,
            code="DEVSPACE_NPM_VERSION_UNAVAILABLE", message="npm version could not be determined",
        )
        npm_version = (version_result.stdout or "").strip()
        try:
            npm_major = int(npm_version.split(".", 1)[0])
        except ValueError as exc:
            raise DevSpaceCompatError("DEVSPACE_NPM_VERSION_UNAVAILABLE", "npm returned an invalid version") from exc
        if npm_major < 12:
            raise DevSpaceCompatError(
                "DEVSPACE_NPM_VERSION_UNSUPPORTED", "bounded install-script approval requires npm 12 or newer",
                {"version": npm_version},
            )
        policy_path = layout["workspace"] / "package.json"
        before = _json_object(policy_path, code="DEVSPACE_NATIVE_POLICY_INVALID")
        before_policy = before.get("allowScripts") or {}
        if not isinstance(before_policy, dict):
            raise DevSpaceCompatError("DEVSPACE_NATIVE_POLICY_INVALID", "npx allowScripts policy must be a JSON object")
        target = f"{NATIVE_DEPENDENCY_NAME}@{NATIVE_DEPENDENCY_VERSION}"
        conflicts = sorted(
            key for key in before_policy
            if key != target and (key == NATIVE_DEPENDENCY_NAME or key.startswith(f"{NATIVE_DEPENDENCY_NAME}@"))
        )
        if conflicts:
            raise DevSpaceCompatError(
                "DEVSPACE_NATIVE_POLICY_INVALID", "existing native approval policy conflicts with the exact tested version",
                {"target": target, "conflicts": conflicts},
            )
        approval_added = before_policy.get(target) is not True
        if approval_added:
            _native_command(
                [npm, "install-scripts", "approve", target, "--allow-scripts-pin", "--json"],
                cwd=layout["workspace"], runner=runner, code="DEVSPACE_NATIVE_APPROVAL_FAILED",
                message="bounded native install-script approval failed",
            )
        after = _json_object(policy_path, code="DEVSPACE_NATIVE_POLICY_INVALID")
        after_policy = after.get("allowScripts")
        if not isinstance(after_policy, dict) or after_policy.get(target) is not True:
            raise DevSpaceCompatError("DEVSPACE_NATIVE_APPROVAL_SCOPE_CHANGED", "exact native approval was not persisted")
        if ({key: value for key, value in before.items() if key != "allowScripts"} !=
                {key: value for key, value in after.items() if key != "allowScripts"} or
                any(key != target and after_policy.get(key) != value for key, value in before_policy.items()) or
                any(key != target and key not in before_policy for key in after_policy)):
            raise DevSpaceCompatError(
                "DEVSPACE_NATIVE_APPROVAL_SCOPE_CHANGED", "native approval changed unrelated install-script policy"
            )
        _native_command(
            [npm, "rebuild", target, "--foreground-scripts", "--ignore-scripts=false"],
            cwd=layout["workspace"], runner=runner, code="DEVSPACE_NATIVE_REBUILD_FAILED",
            message="bounded better-sqlite3 rebuild failed", timeout_seconds=600,
        )
        native = check_native_runtime(package_root=root, runner=runner)
        checks.append({
            "root": str(root), "status": "rebuilt-and-loadable", "npm_version": npm_version,
            "approval_added": approval_added, "native_runtime": native,
        })
    return {"ok": True, "version": SUPPORTED_VERSION, "checks": checks}


def check_native_runtime(
    *,
    package_root: Path | None = None,
    runner: Any = subprocess.run,
    allow_package_absent: bool = False,
) -> dict[str, Any]:
    """Prove the tested better-sqlite3 binding loads under the active Node runtime."""
    try:
        roots = (
            resolve_package_roots()
            if package_root is None
            else [package_root.expanduser().resolve(strict=True)]
        )
    except DevSpaceCompatError as exc:
        if allow_package_absent and exc.code == "DEVSPACE_PACKAGE_NOT_FOUND":
            return {"ok": True, "status": "package-absent", "version": SUPPORTED_VERSION}
        raise
    node = shutil.which("node")
    if not node:
        raise DevSpaceCompatError("DEVSPACE_NODE_MISSING", "Node.js is required for DevSpace")
    checked: list[str] = []
    source = (
        "const {createRequire}=require('node:module');"
        "const r=createRequire(process.cwd()+'/package.json');"
        "const Database=r('better-sqlite3');"
        "const db=new Database(':memory:');db.close();"
    )
    for root in roots:
        completed = runner(
            [node, "-e", source],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
            **_git_kwargs(),
        )
        if completed.returncode != 0:
            raise DevSpaceCompatError(
                "DEVSPACE_NATIVE_BINDING_UNAVAILABLE",
                "DevSpace better-sqlite3 could not load under the active Node runtime",
                {
                    "root": str(root),
                    "version": SUPPORTED_VERSION,
                    "stderr": (completed.stderr or "").strip()[-1200:],
                    "next_action": (
                        "Review `npm install-scripts ls`, explicitly approve only the tested "
                        "DevSpace native dependency scripts, then rebuild better-sqlite3."
                    ),
                },
            )
        checked.append(str(root))
    return {"ok": True, "status": "loadable", "version": SUPPORTED_VERSION, "package_roots": checked}


def check_oauth_refresh_replay(
    *,
    package_root: Path,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Exercise the bounded refresh-token replay grace against an isolated database."""
    root = package_root.expanduser().resolve(strict=True)
    node = shutil.which("node")
    if not node:
        raise DevSpaceCompatError("DEVSPACE_NODE_MISSING", "Node.js is required for DevSpace")
    source = r"""
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { SingleUserOAuthProvider } from "./dist/oauth-provider.js";
const state = fs.mkdtempSync(path.join(os.tmpdir(), "codex-devspace-oauth-replay-"));
let provider;
try {
  const resource = new URL("https://example.test/mcp");
  provider = new SingleUserOAuthProvider({
    ownerToken: "synthetic-test-only",
    accessTokenTtlSeconds: 60,
    refreshTokenTtlSeconds: 600,
    scopes: ["devspace", "offline_access"],
    allowedRedirectHosts: ["chatgpt.com"],
  }, resource, state);
  const client = await provider.clientsStore.registerClient({
    redirect_uris: ["https://chatgpt.com/connector/oauth/callback"],
    client_name: "Codex synthetic replay check",
  });
  const seed = provider.issueTokens(client.client_id, ["devspace"], resource);
  const first = await provider.exchangeRefreshToken(client, seed.refresh_token);
  const replay = await provider.exchangeRefreshToken(client, seed.refresh_token);
  assert.deepEqual(replay, first);
  await assert.rejects(() => provider.exchangeRefreshToken(
    { ...client, client_id: "wrong-client" }, seed.refresh_token));
  await assert.rejects(() => provider.exchangeRefreshToken(
    client, seed.refresh_token, ["offline_access"]));
  await assert.rejects(() => provider.exchangeRefreshToken(
    client, seed.refresh_token, undefined, new URL("https://other.test/mcp")));
  await provider.verifyAccessToken(first.access_token);
  await provider.revokeToken(client, { token: first.refresh_token });
  await assert.rejects(() => provider.exchangeRefreshToken(client, seed.refresh_token));
  const secondSeed = provider.issueTokens(client.client_id, ["devspace"], resource);
  await provider.exchangeRefreshToken(client, secondSeed.refresh_token);
  for (const value of provider.refreshReplays.values()) value.expiresAtMs = 0;
  await assert.rejects(() => provider.exchangeRefreshToken(client, secondSeed.refresh_token));
  console.log(JSON.stringify({
    ok: true,
    replayed_same_pair: true,
    mismatch_rejected: true,
    revoke_invalidated: true,
    expired_rejected: true,
  }));
} finally {
  if (provider) provider.close();
  fs.rmSync(state, { recursive: true, force: true });
}
"""
    completed = runner(
        [node, "--input-type=module", "-e", source],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        **_git_kwargs(),
    )
    if completed.returncode != 0:
        raise DevSpaceCompatError(
            "DEVSPACE_OAUTH_REFRESH_REPLAY_CHECK_FAILED",
            "DevSpace OAuth refresh replay compatibility check failed",
            {"root": str(root), "stderr": (completed.stderr or "").strip()[-1200:]},
        )
    try:
        result = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_OAUTH_REFRESH_REPLAY_CHECK_INVALID",
            "DevSpace OAuth refresh replay check did not return valid JSON",
            {"root": str(root)},
        ) from exc
    expected = {
        "ok": True,
        "replayed_same_pair": True,
        "mismatch_rejected": True,
        "revoke_invalidated": True,
        "expired_rejected": True,
    }
    if result != expected:
        raise DevSpaceCompatError(
            "DEVSPACE_OAUTH_REFRESH_REPLAY_CHECK_INCOMPLETE",
            "DevSpace OAuth refresh replay check did not prove every safety boundary",
            {"root": str(root), "result": result},
        )
    return {**result, "root": str(root), "status": "bounded-replay-verified"}


def check_large_read_bridge(
    *,
    package_root: Path,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """Prove a long single UTF-8 line can be reconstructed without shell access."""
    root = package_root.expanduser().resolve(strict=True)
    node = shutil.which("node")
    if not node:
        raise DevSpaceCompatError("DEVSPACE_NODE_MISSING", "Node.js is required for DevSpace")
    server_path = root / "dist" / "server.js"
    try:
        server_source = server_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_LARGE_READ_BRIDGE_SOURCE_UNREADABLE",
            "DevSpace large single-line read bridge source is unreadable",
            {"path": str(server_path)},
        ) from exc
    match = re.search(
        r"export async function readUtf8Chunk\(path, offsetBytes, limitBytes\) \{.*?\n\}\n(?=(?:const workspaceIdDescription|function serverInstructions)\b)",
        server_source,
        flags=re.DOTALL,
    )
    if match is None:
        raise DevSpaceCompatError(
            "DEVSPACE_LARGE_READ_BRIDGE_SOURCE_MISSING",
            "DevSpace large single-line read bridge source is missing",
            {"path": str(server_path)},
        )
    # Importing dist/server.js loads the entire MCP dependency graph. Some
    # supported Windows hosts can block indefinitely in that graph even though
    # the bridge itself is valid. Execute the exact installed function body in
    # a dependency-minimal Node process instead; the enclosing package file is
    # already hash-gated by ensure_devspace_compatibility().
    bridge_source = match.group(0).replace("export async function", "async function", 1)
    source = r"""
import assert from "node:assert/strict";
import crypto, { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { open } from "node:fs/promises";
""" + bridge_source + r"""
const state = fs.mkdtempSync(path.join(os.tmpdir(), "codex-devspace-read-chunk-"));
const target = path.join(state, "single-line.json");
const expected = JSON.stringify({payload: "\uAC00".repeat(24000)});
try {
  fs.writeFileSync(target, expected, "utf8");
  const chunks = [];
  let offset = 0;
  let identity;
  for (let calls = 0; calls < 16; calls += 1) {
    const result = await readUtf8Chunk(target, offset, 24 * 1024);
    assert.equal(result.offsetBytes, offset);
    assert.ok(result.bytesReturned <= 24 * 1024);
    assert.ok(result.nextOffsetBytes >= offset);
    assert.equal(result.totalBytes, Buffer.byteLength(expected, "utf8"));
    identity ??= result.sha256;
    assert.equal(result.sha256, identity);
    chunks.push(result.content);
    offset = result.nextOffsetBytes;
    if (result.eof) break;
    assert.ok(result.bytesReturned > 0);
  }
  assert.equal(chunks.join(""), expected);
  assert.equal(identity, crypto.createHash("sha256").update(expected, "utf8").digest("hex"));
  console.log(JSON.stringify({
    ok: true,
    reconstructed: true,
    utf8_boundary_safe: true,
    max_chunk_bytes: 24576,
  }));
} finally {
  fs.rmSync(state, {recursive: true, force: true});
}
process.exit(0);
"""
    try:
        completed = runner(
            [node, "--input-type=module", "-e", source],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
            **_git_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_LARGE_READ_BRIDGE_CHECK_TIMEOUT",
            "DevSpace large single-line read bridge check timed out",
            {"root": str(root), "timeout_seconds": 30},
        ) from exc
    if completed.returncode != 0:
        raise DevSpaceCompatError(
            "DEVSPACE_LARGE_READ_BRIDGE_CHECK_FAILED",
            "DevSpace large single-line read bridge check failed",
            {"root": str(root), "stderr": (completed.stderr or "").strip()[-1200:]},
        )
    try:
        result = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_LARGE_READ_BRIDGE_CHECK_INVALID",
            "DevSpace large read bridge check did not return valid JSON",
            {"root": str(root)},
        ) from exc
    expected = {
        "ok": True,
        "reconstructed": True,
        "utf8_boundary_safe": True,
        "max_chunk_bytes": 24576,
    }
    if result != expected:
        raise DevSpaceCompatError(
            "DEVSPACE_LARGE_READ_BRIDGE_CHECK_INCOMPLETE",
            "DevSpace large read bridge check did not prove every boundary",
            {"root": str(root), "result": result},
        )
    return {**result, "root": str(root), "status": "chunk-reconstruction-verified"}


def patch_root() -> Path:
    return Path(__file__).resolve().parent / "devspace-compat" / SUPPORTED_VERSION


def compat_state_root() -> Path:
    override = str(os.environ.get("CODEX_DEVSPACE_COMPAT_STATE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".codex" / "state" / "devspace-compat" / SUPPORTED_VERSION).resolve()


def restart_marker_path() -> Path:
    return compat_state_root() / "restart-required.json"


def _write_restart_marker(roots: Sequence[Path]) -> Path:
    marker = restart_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(f"{marker.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {
                "schema": "codex.chatgpt.devspace-restart-required/v1",
                "version": SUPPORTED_VERSION,
                "created_at_unix_ns": time.time_ns(),
                "package_roots": [str(root) for root in roots],
                "patched_files": {
                    str(root / relative): contract["patched"]
                    for root in roots
                    for relative, contract in PATCHES.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, marker)
    return marker


def _powershell_json(script: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        **_git_kwargs(),
    )
    if completed.returncode == 3:
        return None
    if completed.returncode != 0:
        raise DevSpaceCompatError(
            "DEVSPACE_SERVICE_PROBE_FAILED",
            "DevSpace listener identity could not be inspected",
            {"exit_code": completed.returncode, "stderr": (completed.stderr or "").strip()[-1200:]},
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_SERVICE_PROBE_INVALID",
            "DevSpace listener identity was not valid JSON",
        ) from exc
    return value if isinstance(value, dict) else None


def current_devspace_service_identity(local_port: int = 7676) -> dict[str, Any] | None:
    if os.name != "nt":
        path = Path(__file__).resolve().with_name("codexpro_posix_process.py")
        spec = importlib.util.spec_from_file_location("codexpro_posix_process_runtime", path)
        if spec is None or spec.loader is None:
            raise DevSpaceCompatError("DEVSPACE_SERVICE_PROBE_UNAVAILABLE", "POSIX identity module unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            return module.listener_identity(local_port)
        except module.ProcessIdentityError as exc:
            raise DevSpaceCompatError("DEVSPACE_SERVICE_PROBE_FAILED", str(exc)) from exc
    script = (
        f"$c=Get-NetTCPConnection -State Listen -LocalPort {int(local_port)} "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if($null -eq $c){exit 3}; "
        "$p=Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\"; "
        "if($null -eq $p){exit 3}; "
        "$started=[DateTimeOffset]::new($p.CreationDate.ToUniversalTime()).ToUnixTimeMilliseconds()*1000000; "
        "[pscustomobject]@{pid=[int]$p.ProcessId;command_line=[string]$p.CommandLine;"
        "started_at_unix_ns=[int64]$started;local_port=[int]$c.LocalPort}|ConvertTo-Json -Compress"
    )
    return _powershell_json(script)


def _assert_devspace_service_identity(
    value: dict[str, Any] | None,
    package_roots: Sequence[Path],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DevSpaceCompatError(
            "DEVSPACE_SERVICE_NOT_LISTENING",
            "DevSpace service is not listening on the expected local port",
        )
    command_line = str(value.get("command_line") or "")
    normalized = command_line.replace("\\", "/").casefold()
    normalized = re.sub(r"/+", "/", normalized)
    normalized = normalized.replace("/.bin/../", "/")
    expected_cli_paths = [
        str(root / "dist" / "cli.js").replace("\\", "/").casefold()
        for root in package_roots
    ]
    if os.name != "nt":
        for root in package_roots:
            cli = root / "dist" / "cli.js"
            shim = root.parents[1] / ".bin" / "devspace"
            try:
                if shim.is_symlink() and shim.resolve(strict=True) == cli.resolve(strict=True):
                    expected_cli_paths.append(str(shim).casefold())
            except OSError:
                continue
    if not any(
        expected in normalized
        and re.search(rf"{re.escape(expected)}(?:\"|\s)+serve(?:\s|$)", normalized)
        for expected in expected_cli_paths
    ):
        raise DevSpaceCompatError(
            "DEVSPACE_SERVICE_IDENTITY_MISMATCH",
            "the expected DevSpace port is owned by another process",
            {
                "pid": value.get("pid"),
                "command_line": command_line,
                "expected_cli_paths": expected_cli_paths,
            },
        )
    return value


def stop_exact_devspace_service(
    *,
    local_port: int = 7676,
    service_probe=current_devspace_service_identity,
    stopper: Any | None = None,
    package_roots: Sequence[Path] | None = None,
    windows_runner: Any = subprocess.run,
    platform_name: str | None = None,
    sleeper: Any = time.sleep,
    stop_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    identity = service_probe(local_port)
    if identity is None:
        return {"ok": True, "stopped": False, "reason": "service-absent"}
    roots = list(package_roots or resolve_service_stop_roots())
    identity = _assert_devspace_service_identity(identity, roots)
    pid = int(identity["pid"])
    stop_evidence: dict[str, Any] = {"mode": "injected"}
    if stopper is not None:
        stopper(pid)
    elif (platform_name or os.name) != "nt":
        path = Path(__file__).resolve().with_name("codexpro_posix_process.py")
        spec = importlib.util.spec_from_file_location("codexpro_posix_process_stop_runtime", path)
        if spec is None or spec.loader is None:
            raise DevSpaceCompatError("DEVSPACE_SERVICE_STOP_FAILED", "POSIX identity module unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            module.terminate_exact_process(identity)
        except module.ProcessIdentityError as exc:
            raise DevSpaceCompatError("DEVSPACE_SERVICE_STOP_FAILED", str(exc), {"pid": pid}) from exc
        stop_evidence = {"mode": "posix-exact-terminate"}
    else:
        started_at = int(identity.get("started_at_unix_ns") or 0)
        if pid <= 0 or started_at <= 0:
            raise DevSpaceCompatError(
                "DEVSPACE_SERVICE_STOP_FAILED",
                "exact Windows service identity is missing PID/start evidence",
                {"pid": identity.get("pid"), "started_at_unix_ns": identity.get("started_at_unix_ns")},
            )
        script = (
            f"$targetPid={pid};$expectedStart=[int64]{started_at};$port={int(local_port)};"
            "function Get-Target {"
            "$p=Get-CimInstance Win32_Process -Filter \"ProcessId=$targetPid\" -ErrorAction SilentlyContinue;"
            "if($null -eq $p){return $null};"
            "$s=[DateTimeOffset]::new($p.CreationDate.ToUniversalTime()).ToUnixTimeMilliseconds()*1000000;"
            "if([int64]$s -ne $expectedStart){return $null};return $p};"
            "$initial=Get-Target;$stale=($null -eq $initial);$forced=$false;"
            "if(!$stale){"
            "Stop-Process -Id $targetPid -ErrorAction SilentlyContinue;"
            "for($i=0;$i -lt 50 -and $null -ne (Get-Target);$i++){Start-Sleep -Milliseconds 100};"
            "if($null -ne (Get-Target)){"
            "$forced=$true;Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue;"
            "for($i=0;$i -lt 50 -and $null -ne (Get-Target);$i++){Start-Sleep -Milliseconds 100}}};"
            "$alive=($null -ne (Get-Target));"
            "$listener=Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1;"
            "$released=($null -eq $listener);$ok=(!$alive -and $released);"
            "[pscustomobject]@{ok=$ok;pid=$targetPid;stale_pid=$stale;forced=$forced;"
            "process_exited=(!$alive);listener_released=$released;"
            "listener_pid=$(if($listener){[int]$listener.OwningProcess}else{$null})}|ConvertTo-Json -Compress;"
            "if($ok){exit 0}else{exit 4}"
        )
        completed = windows_runner(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(15.0, stop_timeout_seconds + 5.0),
            **_git_kwargs(),
        )
        try:
            stop_evidence = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DevSpaceCompatError(
                "DEVSPACE_SERVICE_STOP_FAILED", "exact Windows stop did not return structured evidence",
                {"pid": pid, "exit_code": completed.returncode, "stdout": (completed.stdout or "").strip()[-1200:]},
            ) from exc
        if completed.returncode != 0:
            raise DevSpaceCompatError(
                "DEVSPACE_SERVICE_STOP_FAILED",
                "the exact DevSpace service could not be stopped",
                {"pid": pid, "exit_code": completed.returncode, "stop": stop_evidence,
                 "stderr": (completed.stderr or "").strip()[-1200:]},
            )
        if not isinstance(stop_evidence, dict) or not stop_evidence.get("ok"):
            raise DevSpaceCompatError(
                "DEVSPACE_SERVICE_STOP_FAILED",
                "exact Windows stop evidence did not prove process exit and listener release",
                {"pid": pid, "stop": stop_evidence},
            )

    deadline = time.monotonic() + max(0.0, stop_timeout_seconds)
    while True:
        remaining = service_probe(local_port)
        if remaining is None:
            break
        remaining_pid = int(remaining.get("pid") or 0)
        if remaining_pid != pid:
            raise DevSpaceCompatError(
                "DEVSPACE_SERVICE_PORT_REBOUND",
                "the DevSpace port was rebound before exact stop verification completed",
                {"expected_pid": pid, "listener_pid": remaining_pid},
            )
        if time.monotonic() >= deadline:
            raise DevSpaceCompatError(
                "DEVSPACE_SERVICE_STOP_FAILED",
                "the exact DevSpace process did not release its listener before timeout",
                {"pid": pid, "timeout_seconds": stop_timeout_seconds},
            )
        sleeper(min(0.1, max(0.0, deadline - time.monotonic())))
    return {"ok": True, "stopped": True, "pid": pid, "stop": stop_evidence}


def _git_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": startup}


def _apply_patch(package_root: Path, patch_path: Path) -> None:
    isolated_env = os.environ.copy()
    isolated_env["GIT_CEILING_DIRECTORIES"] = str(package_root.parent)
    patch_bytes = patch_path.read_bytes().replace(b"\r\n", b"\n")
    for check_only in (True, False):
        argv = ["git", "-c", "core.autocrlf=false", "apply", "--ignore-space-change"]
        if check_only:
            argv.append("--check")
        argv.append("-")
        completed = subprocess.run(
            argv,
            cwd=str(package_root),
            input=patch_bytes,
            capture_output=True,
            check=False,
            env=isolated_env,
            **_git_kwargs(),
        )
        if completed.returncode != 0:
            code = "DEVSPACE_PATCH_CHECK_FAILED" if check_only else "DEVSPACE_PATCH_APPLY_FAILED"
            raise DevSpaceCompatError(
                code,
                "DevSpace compatibility patch could not be validated or applied",
                {
                    "patch": str(patch_path),
                    "stderr": (completed.stderr or b"").decode("utf-8", errors="replace").strip()[-1200:],
                },
            )


def ensure_devspace_compatibility(
    *,
    package_root: Path | None = None,
    backup_root: Path | None = None,
) -> dict[str, Any]:
    roots = (
        resolve_package_roots()
        if package_root is None
        else [package_root.expanduser().resolve(strict=True)]
    )
    backup = backup_root or (
        Path.home() / ".codex" / "state" / "devspace-compat-backups" / SUPPORTED_VERSION
    )
    changed: list[str] = []
    already: list[str] = []
    oauth_checks: list[dict[str, Any]] = []
    large_read_checks: list[dict[str, Any]] = []
    for root in roots:
        if package_version(root) != SUPPORTED_VERSION:
            raise DevSpaceCompatError(
                "DEVSPACE_VERSION_UNVALIDATED",
                "DevSpace compatibility is validated only for the tested version",
                {"root": str(root), "supported": SUPPORTED_VERSION},
            )
        for relative, contract in PATCHES.items():
            target = root / Path(relative)
            current = sha256_file(target)
            item = relative if len(roots) == 1 else f"{root}:{relative}"
            if current == contract["patched"]:
                already.append(item)
                continue
            upgrades = contract.get("upgrades") if isinstance(contract.get("upgrades"), dict) else {}
            if current != contract["pristine"] and current not in upgrades:
                raise DevSpaceCompatError(
                    "DEVSPACE_FILE_HASH_MISMATCH",
                    "DevSpace compatibility refuses an unknown third-party file",
                    {
                        "path": str(target),
                        "actual": current,
                        "expected": [contract["pristine"], contract["patched"], *sorted(upgrades)],
                    },
                )
            backup_path = backup / Path(relative)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(target, backup_path)
            observed: set[str] = set()
            while current != contract["patched"]:
                if current in observed:
                    raise DevSpaceCompatError(
                        "DEVSPACE_PATCH_CYCLE",
                        "DevSpace compatibility patch chain did not converge",
                        {"path": str(target), "actual": current},
                    )
                observed.add(current)
                patch_name = contract["patch"] if current == contract["pristine"] else upgrades.get(current)
                if not patch_name:
                    raise DevSpaceCompatError(
                        "DEVSPACE_PATCH_HASH_MISMATCH",
                        "DevSpace compatibility patch output hash is unexpected",
                        {
                            "path": str(target),
                            "actual": current,
                            "expected": contract["patched"],
                        },
                    )
                _apply_patch(root, patch_root() / str(patch_name))
                current = sha256_file(target)
            changed.append(item)
        if "dist/oauth-provider.js" in PATCHES:
            oauth_checks.append(check_oauth_refresh_replay(package_root=root))
        if "dist/server.js" in PATCHES:
            large_read_checks.append(check_large_read_bridge(package_root=root))
    marker = restart_marker_path()
    if changed:
        marker = _write_restart_marker(roots)
    return {
        "ok": True,
        "version": SUPPORTED_VERSION,
        "package_roots": [str(root) for root in roots],
        "changed": changed,
        "already_patched": already,
        "oauth_refresh_replay_checks": oauth_checks,
        "large_read_bridge_checks": large_read_checks,
        "service_restart_required": marker.is_file(),
        "restart_marker": str(marker),
    }


def confirm_service_restarted(
    *,
    package_root: Path | None = None,
    local_port: int = 7676,
    wait_timeout_seconds: float = 20,
    service_probe=current_devspace_service_identity,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    roots = (
        resolve_package_roots()
        if package_root is None
        else [package_root.expanduser().resolve(strict=True)]
    )
    for root in roots:
        if package_version(root) != SUPPORTED_VERSION:
            raise DevSpaceCompatError(
                "DEVSPACE_VERSION_UNVALIDATED",
                "DevSpace restart confirmation requires the tested version",
                {"root": str(root), "supported": SUPPORTED_VERSION},
            )
        for relative, contract in PATCHES.items():
            actual = sha256_file(root / relative)
            if actual != contract["patched"]:
                raise DevSpaceCompatError(
                    "DEVSPACE_RESTART_CONFIRM_HASH_MISMATCH",
                    "DevSpace restart cannot be confirmed before every tested file is patched",
                    {"path": str(root / relative), "actual": actual, "expected": contract["patched"]},
                )
    marker = restart_marker_path()
    existed = marker.is_file()
    if not existed:
        return {
            "ok": True,
            "version": SUPPORTED_VERSION,
            "package_roots": [str(root) for root in roots],
            "restart_confirmed": False,
            "restart_marker_cleared": False,
            "reason": "restart-marker-absent",
        }
    try:
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        patched_at = int(marker_payload["created_at_unix_ns"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise DevSpaceCompatError(
            "DEVSPACE_RESTART_MARKER_INVALID",
            "DevSpace restart marker is unreadable",
            {"path": str(marker)},
        ) from exc
    deadline = time.monotonic() + max(0, wait_timeout_seconds)
    identity: dict[str, Any] | None = None
    while True:
        candidate = service_probe(local_port)
        if isinstance(candidate, dict) and int(candidate.get("started_at_unix_ns") or 0) > patched_at:
            identity = _assert_devspace_service_identity(candidate, roots)
            break
        if time.monotonic() >= deadline:
            raise DevSpaceCompatError(
                "DEVSPACE_RESTART_NOT_PROVEN",
                "DevSpace listener did not start after the compatibility patch",
                {"marker": str(marker), "observed": candidate},
            )
        sleep(min(0.25, max(0, deadline - time.monotonic())))
    if existed:
        marker.unlink()
    return {
        "ok": True,
        "version": SUPPORTED_VERSION,
        "package_roots": [str(root) for root in roots],
        "restart_confirmed": True,
        "restart_marker_cleared": existed,
        "service_identity": identity,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Apply the exact DevSpace 1.0.8 bounded workspace and OAuth refresh "
            "compatibility patches."
        )
    )
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--confirm-service-restarted", action="store_true")
    parser.add_argument("--stop-exact-service", action="store_true")
    parser.add_argument("--prepare-native-runtime", action="store_true")
    parser.add_argument("--check-native-runtime", action="store_true")
    parser.add_argument("--check-oauth-refresh-replay", action="store_true")
    parser.add_argument("--allow-package-absent", action="store_true")
    parser.add_argument("--local-port", type=int, default=7676)
    args = parser.parse_args(argv)
    try:
        selected = sum(bool(value) for value in (
            args.confirm_service_restarted,
            args.stop_exact_service,
            args.prepare_native_runtime,
            args.check_native_runtime,
            args.check_oauth_refresh_replay,
        ))
        if selected > 1:
            raise DevSpaceCompatError(
                "DEVSPACE_COMPAT_ACTION_CONFLICT",
                "choose only one DevSpace compatibility action",
            )
        if args.prepare_native_runtime:
            result = prepare_native_runtime(package_root=args.package_root)
        elif args.check_native_runtime:
            result = check_native_runtime(
                package_root=args.package_root,
                allow_package_absent=args.allow_package_absent,
            )
        elif args.check_oauth_refresh_replay:
            roots = (
                [args.package_root.expanduser().resolve(strict=True)]
                if args.package_root is not None
                else resolve_package_roots()
            )
            result = {
                "ok": True,
                "version": SUPPORTED_VERSION,
                "checks": [check_oauth_refresh_replay(package_root=root) for root in roots],
            }
        elif args.confirm_service_restarted:
            result = confirm_service_restarted(
                package_root=args.package_root,
                local_port=args.local_port,
            )
        elif args.stop_exact_service:
            roots = None
            if args.package_root is not None:
                root = args.package_root.expanduser().resolve(strict=True)
                version = package_version(root)
                if version not in {SUPPORTED_VERSION, LEGACY_LKG_VERSION}:
                    raise DevSpaceCompatError(
                        "DEVSPACE_SERVICE_STOP_VERSION_UNSUPPORTED",
                        "service stop accepts only the validated current or last-known-good package",
                        {
                            "root": str(root),
                            "version": version,
                            "allowed_versions": [SUPPORTED_VERSION, LEGACY_LKG_VERSION],
                        },
                    )
                roots = [root]
            result = stop_exact_devspace_service(
                local_port=args.local_port,
                package_roots=roots,
            )
        else:
            result = ensure_devspace_compatibility(package_root=args.package_root)
    except DevSpaceCompatError as exc:
        result = {
            "ok": False,
            "error": {"code": exc.code, "message": str(exc), "evidence": exc.evidence},
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
