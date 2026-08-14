#!/usr/bin/env python
"""Validate public documentation, brand assets, and release-version parity."""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_LINK_RE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']", re.IGNORECASE)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
CHANGELOG_VERSION_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\b", re.MULTILINE)
REQUIRED_MODES = {
    "direct",
    "plan",
    "review",
    "edit",
    "orchestrator",
    "deep-research",
    "Web Multi-GPT",
    "Local Multi-GPT",
    "comprehensive mode",
    "ultra-economy",
    "pro",
}


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _local_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith("#"):
        return None
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme or target.startswith("//"):
        return None
    path_text = urllib.parse.unquote(parsed.path)
    if not path_text:
        return None
    return (source.parent / path_text).resolve()


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if len(data) != 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a valid PNG header")
    return struct.unpack(">II", data[16:24])


def check_repository(root: Path) -> list[str]:
    errors: list[str] = []
    package = _json(root / "package.json")
    lock = _json(root / "package-lock.json")
    manifest = _json(root / "install-manifest.json")
    changelog = (root / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")

    versions = {
        "package.json": package.get("version"),
        "package-lock.json": lock.get("version"),
        "package-lock root": lock.get("packages", {}).get("", {}).get("version"),
        "install-manifest.json": manifest.get("version"),
    }
    first_changelog = CHANGELOG_VERSION_RE.search(changelog)
    versions["CHANGELOG.md"] = first_changelog.group(1) if first_changelog else None
    unique_versions = {value for value in versions.values() if isinstance(value, str)}
    if len(unique_versions) != 1 or any(value is None for value in versions.values()):
        errors.append(f"release versions differ: {versions}")
    elif not SEMVER_RE.fullmatch(next(iter(unique_versions))):
        errors.append(f"release version is not SemVer: {versions}")

    markdown_files = [root / "README.md", root / "README.en.md", root / "CONTRIBUTING.md"]
    markdown_files.extend(sorted((root / "docs").rglob("*.md")))
    for source in markdown_files:
        text = source.read_text(encoding="utf-8")
        for match in (*MARKDOWN_LINK_RE.findall(text), *HTML_LINK_RE.findall(text)):
            target = _local_target(source, match)
            if target is not None and not target.exists():
                errors.append(f"broken local link: {source.relative_to(root)} -> {match}")

    for svg_name in ("logo-mark.svg", "logo.svg", "banner.svg", "social-preview.svg"):
        path = root / "docs" / "assets" / "brand" / svg_name
        try:
            ET.parse(path)
        except (OSError, ET.ParseError) as exc:
            errors.append(f"invalid SVG {path.relative_to(root)}: {exc}")

    preview = root / "docs" / "assets" / "brand" / "social-preview.png"
    try:
        if _png_dimensions(preview) != (1280, 640):
            errors.append("social-preview.png must be exactly 1280x640")
    except (OSError, ValueError) as exc:
        errors.append(f"invalid social-preview.png: {exc}")

    readmes = {
        "README.md": (root / "README.md").read_text(encoding="utf-8"),
        "README.en.md": (root / "README.en.md").read_text(encoding="utf-8"),
    }
    for name, text in readmes.items():
        if "docs/assets/brand/banner.svg" not in text:
            errors.append(f"{name} does not use the canonical banner")
        if "Agent Web GPT Automation" not in text:
            errors.append(f"{name} is missing the product name")
        missing_modes = sorted(mode for mode in REQUIRED_MODES if mode not in text)
        if missing_modes:
            errors.append(f"{name} is missing modes: {missing_modes}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = check_repository(args.root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("docs-contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
