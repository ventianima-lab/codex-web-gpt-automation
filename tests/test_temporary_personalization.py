"""Run DOM regression tests in the normal cross-platform pytest suite."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_temporary_personalization_dom():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    script = Path(__file__).with_suffix(".mjs")
    result = subprocess.run([node, "--test", str(script)], capture_output=True, timeout=20)
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")
