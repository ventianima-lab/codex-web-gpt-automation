"""Human-facing onboarding presentation; diagnostic state remains unchanged."""
from __future__ import annotations

import re
import sys
from typing import Any, Sequence


STAGE_NAMES = {
    "01_install": ("프로그램 설치", "Install software"),
    "02_stable_endpoint": ("연결 주소와 프로젝트 확인", "Confirm address and projects"),
    "03_devspace_init": ("프로젝트 연결 준비", "Prepare workspace connection"),
    "04_reboot_service": ("재시작 후 연결 복원 확인", "Verify connection recovery"),
    "05_endpoint_check": ("연결 상태 확인", "Check connection"),
    "06_oracle_login": ("전용 브라우저 로그인", "Sign in to the dedicated browser"),
    "06b_local_network_access": ("전용 브라우저의 로컬 연결 허용", "Allow local browser connection"),
    "07_chatgpt_app": ("ChatGPT 앱 연결", "Connect the ChatGPT app"),
    "08_final_gate": ("실제 프로젝트 읽기 검증", "Verify a real project read"),
}


def configure_output() -> None:
    """Write Unicode consistently to consoles and redirected diagnostic files."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _humanize(value: str, language: str) -> str:
    index = 0 if language == "ko" else 1
    for stage, names in STAGE_NAMES.items():
        value = value.replace(stage, names[index])
    return value


def render_step(step: dict[str, Any], stages: Sequence[str]) -> str:
    language = "en" if step.get("language") == "en" else "ko"
    ko = language == "ko"
    total = len(stages)
    pending = set(step.get("pending_stages") or [])
    if step.get("done"):
        return f"[{total}/{total}] {step['completion_label']}\n" + (
            "설치와 실제 프로젝트 연결 검증을 마쳤습니다." if ko
            else "Installation and real project access are verified."
        )
    current = step["current_stage"]
    position = stages.index(current) + 1
    owner = ("사용자 확인 필요" if ko else "Your action is needed") if step.get("needs_user_action") else (
        "자동 확인" if ko else "Automatic check"
    )
    lines = [f"[{position}/{total}] {_humanize(current, language)} · {owner}", str(step["completion_label"])]
    completed = [stage for stage in stages[:position - 1] if stage not in pending]
    if completed:
        lines.append(("완료: " if ko else "Completed: ") + ", ".join(_humanize(item, language) for item in completed))
    guidance = {
        "01_install": ("설치 파일과 필수 프로그램을 확인합니다.", "Check installed files and required programs."),
        "03_devspace_init": ("연결 준비 화면을 완료해주세요. 직접 암호를 정한다면 16자 이상, 공백 없이 문자 종류 3개 이상을 사용하세요. 암호는 에이전트에게 보내지 마세요.", "Complete connection setup. A custom password needs 16 characters, no whitespace, and three character classes. Do not send it to the agent."),
        "04_reboot_service": ("저장된 프로젝트 목록으로 연결 서비스가 다시 시작되는지 확인합니다.", "Verify that the service recovers with the saved project list."),
        "05_endpoint_check": ("이 컴퓨터와 ChatGPT 사이의 연결을 확인합니다.", "Check the connection between this computer and ChatGPT."),
        "06_oracle_login": ("전용 로그인 창에서 ChatGPT에 로그인해주세요. 완료한 뒤 해당 창만 닫아주세요.", "Sign in to ChatGPT in the dedicated login window, then close only that window."),
        "06b_local_network_access": ("전용 로그인 창을 닫은 상태에서 chatgpt.com의 로컬 연결 권한을 확인합니다.", "With the dedicated login window closed, check local connection permission for chatgpt.com."),
        "08_final_gate": ("최신 모델의 Pro 설정으로 프로젝트 열기와 파일 전체 읽기를 검증합니다. 검증 결과를 기다려주세요.", "Verify project access and a complete file read using Latest with Pro effort. Wait for the verification result."),
    }
    if current == "07_chatgpt_app" and step.get("registration_url"):
        lines.extend([
            "ChatGPT 설정의 플러그인 또는 앱에서 개발자 모드를 켜고 앱을 등록해주세요." if ko else "Enable developer mode under ChatGPT Plugins or Apps, then register the app.",
            ("앱 이름: " if ko else "App name: ") + str(step.get("app_name", "codex")),
            ("등록 주소: " if ko else "Registration address: ") + str(step["registration_url"]),
            "Owner 승인을 같은 화면에서 완료해주세요. 이미 등록했다면 다시 만들지 마세요." if ko else "Complete Owner approval. If the app is already registered, do not recreate it.",
        ])
    elif current in guidance:
        lines.append(guidance[current][0 if ko else 1])
    else:
        for instruction in step.get("instructions") or []:
            if re.search(r"onboard\.py\s+(?:confirm|consent)\s", instruction):
                continue
            lines.append(_humanize(str(instruction), language))
    lines.append(("다음 행동: 안내된 작업을 마친 뒤 ‘계속’이라고 알려주세요. 중단했다면 python onboard.py resume으로 이어집니다."
                  if ko else "Next: complete the action above and say 'continue'. After an interruption, use python onboard.py resume."))
    return "\n".join(lines)


def render_error(code: str, language: str | None = None) -> str:
    ko = language != "en"
    reasons = {
        "ONBOARDING_ALREADY_STARTED": ("진행 중인 설치가 있습니다.", "Installation is already in progress."),
        "ONBOARDING_STATE_CORRUPT": ("저장된 진행 정보를 읽을 수 없습니다.", "Saved installation progress cannot be read."),
        "DEVSPACE_CONFIG_ALLOWED_ROOTS_MISSING": ("프로젝트 폴더 저장이 끝나지 않았습니다.", "Project folders were not saved completely."),
        "DEVSPACE_SERVICE_IDENTITY_MISMATCH": ("실행 중인 연결 서비스의 설치 정보를 확인하지 못했습니다.", "The running connection service could not be verified."),
        "DEVSPACE_OWNER_PASSWORD_STRENGTH_INVALID": ("암호 조건을 충족하지 못했습니다. 16자 이상, 공백 없이 문자 종류 3개 이상이 필요합니다.", "The password needs at least 16 characters, no whitespace, and three character classes."),
        "ORACLE_CHROME_PROFILE_IN_USE": ("전용 로그인 브라우저가 아직 열려 있습니다.", "The dedicated login browser is still open."),
    }
    reason = reasons.get(code, ("현재 설치 단계의 검증이 끝나지 않았습니다.", "The current installation step could not be verified."))[0 if ko else 1]
    if code == "ORACLE_CHROME_PROFILE_IN_USE":
        action = "전용 로그인 창만 닫은 뒤 설치를 재개해주세요." if ko else "Close only the dedicated login window, then resume installation."
    elif code == "ONBOARDING_ALREADY_STARTED":
        action = "python onboard.py resume으로 이어가세요." if ko else "Continue with python onboard.py resume."
    else:
        action = "이 단계의 진단 결과를 확인해 복구를 이어가겠습니다." if ko else "Inspect this step's diagnostics before continuing recovery."
    return "\n".join([reason, ("자동 복구: 아직 확인되지 않았습니다." if ko else "Automatic recovery has not been verified."),
                       ("다음 행동: " if ko else "Next: ") + action,
                       ("이 오류 표시는 암호, OAuth, 프로젝트 폴더 설정을 변경하지 않습니다." if ko else "Reporting this error does not change passwords, OAuth, or project settings.")])
