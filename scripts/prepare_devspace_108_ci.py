#!/usr/bin/env python3
"""Prepare the exact published DevSpace 1.0.8 package for CI tests."""

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


DEVSPACE_VERSION = "1.0.8"
DEVSPACE_INTEGRITY = "sha512-uyEgFUmt8UcxRy7xnH2rddYdEm6lLIPMKgS2JHwRP7qVnWFtC7j1l4//EOfEVhO4NqGMckWxixNkCWAHPvgZqg=="
MAX_FILES = 10_000
MAX_BYTES = 100 * 1024 * 1024
WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_member_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name or any(ord(character) < 32 for character in name):
        raise RuntimeError("DevSpace archive contains an unsafe path")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise RuntimeError("DevSpace archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) < 2 or path.parts[0] != "package":
        raise RuntimeError("DevSpace archive contains an unsafe path")
    for part in path.parts[1:]:
        if ":" in part or part.endswith((" ", ".")):
            raise RuntimeError("DevSpace archive contains an unsafe path")
        if part.rstrip(" .").split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise RuntimeError("DevSpace archive contains an unsafe path")
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
                raise RuntimeError("DevSpace archive contains duplicate or case-colliding entries")
            seen.add(collision_key)
            target = destination.joinpath(*parts)
            if not target.resolve(strict=False).is_relative_to(destination):
                raise RuntimeError("DevSpace archive path escapes the extraction directory")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or len(parts) < 2:
                raise RuntimeError("DevSpace archive contains a non-file entry")
            count += 1
            total += int(member.size)
            if count > MAX_FILES or total > MAX_BYTES:
                raise RuntimeError("DevSpace archive exceeds CI extraction limits")
            source = package.extractfile(member)
            if source is None:
                raise RuntimeError("DevSpace archive file is unreadable")
            content = source.read()
            if len(content) != member.size:
                raise RuntimeError("DevSpace archive file size is inconsistent")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(content)
            target.chmod(member.mode & 0o777)
    package_root = destination / "package"
    metadata = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
    if metadata.get("version") != DEVSPACE_VERSION:
        raise RuntimeError("DevSpace archive version does not match the CI contract")
    return package_root


def main() -> int:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm is required to prepare the DevSpace CI package")
    runner_temp = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
    stage = Path(tempfile.mkdtemp(prefix="devspace-108-ci-", dir=runner_temp))
    packed = subprocess.run(
        [npm, "pack", "--silent", "--pack-destination", str(stage), f"@waishnav/devspace@{DEVSPACE_VERSION}"],
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
        raise RuntimeError("DevSpace npm archive is not a bounded regular file")
    archive_bytes = archive.read_bytes()
    actual_integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive_bytes).digest()).decode("ascii")
    if actual_integrity != DEVSPACE_INTEGRITY:
        raise RuntimeError("DevSpace npm archive integrity does not match the CI contract")
    package_root = extract_verified(archive_bytes, stage / "extracted")
    github_env_value = os.environ.get("GITHUB_ENV", "")
    if not github_env_value:
        raise RuntimeError("GITHUB_ENV is required; this helper is CI-only")
    github_env = Path(github_env_value)
    for value in (package_root, archive):
        if "\n" in str(value) or "\r" in str(value):
            raise RuntimeError("CI package path contains a newline")
    with github_env.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"DEVSPACE_108_PACKAGE_ROOT={package_root}\n")
        output.write(f"DEVSPACE_108_PACKAGE_ARCHIVE={archive}\n")
    print(f"Prepared hash-verified DevSpace {DEVSPACE_VERSION} for CI tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
