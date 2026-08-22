#!/usr/bin/env python3
"""Prepare the exact published Oracle 0.18.0 package for CI compatibility tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


ORACLE_VERSION = "0.18.0"
ORACLE_INTEGRITY = "sha512-o8KFd66zNt36jw5zdtQAV74bgrOlJibbyvnLsVikIWDamesYtez/dIUhQ4zqtD9jkx+7A6vcP9+JgcJt0H5pOw=="
MAX_FILES = 10_000
MAX_BYTES = 100 * 1024 * 1024
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def integrity(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha512-" + base64.b64encode(digest.digest()).decode("ascii")


def safe_member_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name or any(ord(character) < 32 for character in name):
        raise RuntimeError("Oracle archive contains an unsafe path")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise RuntimeError("Oracle archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "package":
        raise RuntimeError("Oracle archive contains an unsafe path")
    for part in path.parts[1:]:
        if ":" in part or part.endswith((" ", ".")):
            raise RuntimeError("Oracle archive contains an unsafe path")
        device_name = part.rstrip(" .").split(".", 1)[0].upper()
        if device_name in WINDOWS_RESERVED_NAMES:
            raise RuntimeError("Oracle archive contains an unsafe path")
    return tuple(path.parts)


def extract_verified(archive_bytes: bytes, destination: Path) -> Path:
    count = 0
    total = 0
    destination.mkdir(parents=True, exist_ok=False)
    destination = destination.resolve(strict=True)
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as package:
        for member in package:
            parts = safe_member_parts(member.name)
            collision_key = PurePosixPath(*parts).as_posix().casefold()
            if collision_key in seen:
                raise RuntimeError("Oracle archive contains duplicate or case-colliding entries")
            seen.add(collision_key)
            target = destination.joinpath(*parts)
            canonical_target = target.resolve(strict=False)
            if not canonical_target.is_relative_to(destination):
                raise RuntimeError("Oracle archive path escapes the extraction directory")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or len(parts) < 2:
                raise RuntimeError("Oracle archive contains a non-file entry")
            count += 1
            total += int(member.size)
            if count > MAX_FILES or total > MAX_BYTES:
                raise RuntimeError("Oracle archive exceeds CI extraction limits")
            source = package.extractfile(member)
            if source is None:
                raise RuntimeError("Oracle archive file is unreadable")
            content = source.read()
            if len(content) != member.size:
                raise RuntimeError("Oracle archive file size is inconsistent")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(content)
            target.chmod(member.mode & 0o777)
    package_root = destination / "package"
    metadata = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    if metadata.get("version") != ORACLE_VERSION:
        raise RuntimeError("Oracle archive version does not match the CI contract")
    return package_root


def main() -> int:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm is required to prepare the Oracle CI package")
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
    stage = Path(tempfile.mkdtemp(prefix="oracle-018-ci-", dir=runner_temp))
    packed = subprocess.run(
        [npm, "pack", "--silent", "--pack-destination", str(stage), f"@steipete/oracle@{ORACLE_VERSION}"],
        cwd=stage,
        capture_output=True,
        text=True,
        check=False,
    )
    if packed.returncode != 0:
        raise RuntimeError(f"npm pack failed: {packed.stderr.strip()[-1000:]}")
    names = [line.strip() for line in packed.stdout.splitlines() if line.strip()]
    if len(names) != 1 or Path(names[0]).name != names[0]:
        raise RuntimeError("npm pack returned an unexpected archive name")
    archive = stage / names[0]
    if not archive.is_file() or archive.is_symlink() or archive.stat().st_size > MAX_BYTES:
        raise RuntimeError("Oracle npm archive is not a bounded regular file")
    archive_bytes = archive.read_bytes()
    actual_integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive_bytes).digest()).decode("ascii")
    if actual_integrity != ORACLE_INTEGRITY:
        raise RuntimeError("Oracle npm archive integrity does not match the CI contract")
    package_root = extract_verified(archive_bytes, stage / "extracted")
    github_env_value = os.environ.get("GITHUB_ENV", "")
    if not github_env_value:
        raise RuntimeError("GITHUB_ENV is required; this helper is CI-only")
    github_env = Path(github_env_value)
    for value in (package_root, archive):
        if "\n" in str(value) or "\r" in str(value):
            raise RuntimeError("CI package path contains a newline")
    with github_env.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"ORACLE_018_PACKAGE_ROOT={package_root}\n")
        output.write(f"ORACLE_018_PACKAGE_ARCHIVE={archive}\n")
    print(f"Prepared hash-verified Oracle {ORACLE_VERSION} for CI tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
