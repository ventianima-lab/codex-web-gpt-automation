# 최초 설치

앱과 연결 프로젝트는 [공통 자동화 규칙](AUTOMATION_POLICY.md)을 따릅니다.
설정은 처음 한 번 진행하며, 매 실행마다 같은 감사·자격 검사를 반복하지 않습니다.

## 설치와 이어하기

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python install.py --dry-run
python install.py
python doctor.py
python onboard.py start --root <프로젝트-폴더>
python onboard.py next
```

macOS에서는 `python3`을 사용합니다. 프로젝트가 여러 개면 `--root`를 반복합니다.
기존 설정은 `python onboard.py resume`으로 이어가며 완료된 단계를 초기화하지
않습니다. `next`의 현재 안내에 따라 사용자 작업을 진행한 뒤
`python onboard.py confirm <stage-id>`로 확인합니다. `--lang ko` 또는
`--lang en`으로 표시 언어를 고를 수 있습니다.

Tailscale Funnel이 기본 관리 경로입니다. 다른 공급자는 고정 HTTPS `/mcp`
주소가 필요합니다. 기존 승인 폴더와 설정을 보존하며, 손상된 설정을 임의의
기본값으로 덮어쓰지 않습니다. 선택형 Local Multi-GPT는 일반 실행 경로와
별개이며 기본값은 사용하지 않음입니다.

## 사용자가 직접 하는 설정

공급자 로그인, DevSpace Owner 암호 입력, Oracle 전용 ChatGPT 로그인, 앱
등록과 OAuth 승인은 사용자가 직접 합니다. 암호·토큰·쿠키를 요청하거나
온보딩 상태에 저장하지 않습니다. ChatGPT 계정·맞춤화·권한 설정을 자동 조작하지
않습니다.

승인한 고정 주소로 앱을 등록합니다(예시 이름 `codex`). 로컬 네트워크 권한
변경은 명시적 동의 후 해당 범위만 적용하고, 일상용 Chrome 설정과 프로필은
보존합니다. 연결 문제가 있으면 해당 실패 원인만 진단합니다. 정상 앱을
반복해서 새로 만들거나 갱신하지 않습니다.

## 한 번의 실제 연결 확인

endpoint 연결 상태와 등록 앱을 통한 실제 프로젝트 읽기를 확인합니다.
비인증 HTTP 401은 경로가 응답한다는 증거이지 파일 읽기 성공 증거는 아닙니다.

일반 임시채팅 실행에서 모델 메뉴의 **최신 → Pro (6 Pro)** 를 명시적으로
선택합니다. 제출 전에 임시채팅 맞춤화를 자동으로 켜고 확인합니다. Oracle 호환 인자는
`gpt-5.6-sol`, `model_strategy=current`, `thinking_time=pro`이지만, 실제
브라우저는 숫자 GPT-5.6이 아닌 `Latest`를 클릭해야 합니다.
알 수 없는 `gpt-6` 또는 `latest` CLI 모델 키를 만들지 않습니다.

답변 전체를 로컬에 저장한 뒤 소유한 탭만 닫습니다. 타임아웃에는 같은 실행을
유지하고 자동 재전송하지 않습니다. 실제 읽기는 강제 도구 호출 순서,
auditNonce, 영수증 세 개, 필수 완료 마커를 요구하지 않습니다.
접근 불가나 미완료 결과는 그대로 보고합니다.

프로젝트는 공통 정책을 참조하며 고유 빌드·테스트·데이터 안전 규칙을 유지합니다.
파일 설치, 연결 설정, 실제 읽기 성공은 구분해서 보고합니다. 코드 변경이나
버전 번호만으로 설치·연결 완료라고 하지 않습니다.

세부 관리 명령은 [DevSpace 설정](DEVSPACE_TAILSCALE_SETUP.md),
에이전트용 요약은 [설치 계약](INSTALL_AGENT.md), 영어 안내는
[English](FIRST_INSTALL.en.md)를 참조하세요.
