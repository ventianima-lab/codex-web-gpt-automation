import importlib.util
import os
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("onboarding_ui", Path(__file__).resolve().parents[1] / "bin/codex_web_gpt_onboarding_ui.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def test_resume_display_keeps_unicode_context_without_internal_stage_ids():
    stages = tuple(ui.STAGE_NAMES)
    text = ui.render_step({"language": "ko", "current_stage": "07_chatgpt_app",
                          "completion_label": "ChatGPT 연결 대기", "needs_user_action": True,
                          "pending_stages": list(stages[-2:]),
                          "instructions": ["앱 codex, https://example.test/mcp", "F:\\새 프로잭트", "python onboard.py confirm 07_chatgpt_app"]}, stages)
    assert "[8/9]" in text and "ChatGPT 앱 연결" in text
    assert "F:\\새 프로잭트" in text and "https://example.test/mcp" in text
    assert not any(stage in text for stage in stages)
    assert text.count("다음 행동:") == 1
    assert "완료:" in text


def test_error_never_prints_unknown_raw_error_or_secrets():
    text = ui.render_error("UNKNOWN_FAILURE secret-token", "ko")
    assert "secret-token" not in text and "UNKNOWN_FAILURE" not in text
    assert "다음 행동:" in text and "OAuth" in text


def test_profile_error_only_asks_to_close_dedicated_window():
    text = ui.render_error("ORACLE_CHROME_PROFILE_IN_USE", "ko")
    assert "전용 로그인 창만" in text


def test_utf8_bytes_survive_redirected_output(tmp_path):
    import subprocess
    import sys
    script = "import sys; sys.path.insert(0, sys.argv[1]); import codex_web_gpt_onboarding_ui as u; u.configure_output(); print(u.render_error('ONBOARDING_ALREADY_STARTED', 'ko'))"
    result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "bin")], capture_output=True, check=True)
    assert "진행 중인 설치" in result.stdout.decode("utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows shell encoding regression")
@pytest.mark.parametrize("edition", ["windows", "core"])
def test_korean_output_through_windows_shells(edition):
    import base64
    import shutil
    import subprocess
    import sys
    shell = (Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
             if edition == "windows" else Path(shutil.which("pwsh") or "missing-pwsh"))
    if not shell.is_file():
        pytest.skip("PowerShell edition is not installed")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    module_dir = Path(__file__).resolve().parents[1] / "bin"
    python_code = "import sys;sys.path.insert(0,sys.argv[1]);import codex_web_gpt_onboarding_ui as u;u.configure_output();print('F:\\\\새 프로잭트');print(u.render_error('ONBOARDING_ALREADY_STARTED','ko'))"
    script = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $OutputEncoding=[Console]::OutputEncoding; & " + quote(sys.executable) + " -c " + quote(python_code) + " " + quote(module_dir)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], capture_output=True, timeout=30, check=True)
    text = result.stdout.decode("utf-8-sig")
    assert "새 프로잭트" in text and "진행 중인 설치" in text
