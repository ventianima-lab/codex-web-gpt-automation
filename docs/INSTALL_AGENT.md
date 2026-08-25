# Codex Web GPT Automation 설치 계약

사용자가 이 저장소 URL만 붙여 넣고 “설치해 줘”라고 하면 에이전트가 checkout부터
아래 순서로 계속 진행합니다. 온보딩 마법사 자체는 로컬 checkout과 lifecycle 설치가
끝난 뒤 시작합니다. URL만으로 실행되는 원격 설치기라고 설명하지 않습니다.

## 처음에만 물을 것

질문은 최대 세 개입니다.

1. 연결할 프로젝트 폴더는 어디인가요? 여러 개면 모두 받습니다.
2. 고정 주소 방식은 무엇인가요? 기본값은 Tailscale입니다.
3. 선택형 Local Multi-GPT도 설치할까요? 기본값은 아니오입니다.

Owner 암호, ChatGPT 비밀번호, token, cookie, OAuth secret은 묻거나 저장하지
않습니다.

## 필수 순서

Windows PowerShell 예시입니다. macOS에서는 `python3`을 사용합니다.

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python install.py --dry-run
python install.py
python doctor.py
python onboard.py start --root <프로젝트 폴더>
```

여러 root는 `--root`를 반복합니다. 주소 방식이 기본 Tailscale이 아니면 `start`에
`--provider`와 고정 `--public-url https://.../mcp`를 넣습니다.

이미 진행 중인 유효한 온보딩에 `start`를 다시 실행하면
`ONBOARDING_ALREADY_STARTED`로 멈춥니다. 계속하려면 `python onboard.py resume`을
사용합니다. 기존 진행 상태를 버리고 다시 시작할 때만 `start`에 `--reset`을 붙입니다.

그 뒤에는 다음 루프만 따릅니다.

```powershell
python onboard.py next
# 사용자가 browser/TTY에서 한 단계를 마친 뒤
python onboard.py confirm <stage-id>
```

`next`가 지시한 현재 단계만 처리합니다. 완료 단계를 다시 실행하거나 다음 단계로
건너뛰지 않습니다. 앞선 단계가 미검증이면 `confirm`은
`STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING`로 거부합니다. `next`가 가리키는 단계만
확인합니다. `08_final_gate`가 통과할 때까지 반복합니다.

기존 DevSpace `allowedRoots`는 새 root와 합쳐 보존합니다. 기존 config JSON이
손상됐거나 root 목록이 유효하지 않으면 조용히 덮어쓰지 말고 실패 폐쇄합니다.

출력은 셸 로케일에 따라 한국어 또는 영어로 자동 선택됩니다. 필요하면 `--lang ko`,
`--lang en`으로 고정하고, 기계 판독이 필요하면 `--json`을 사용합니다. 모든 명령 앞에는
`python onboard.py --lang en next`, `python onboard.py --json next`처럼 둘 수 있습니다.
`next`와 `resume` 뒤에는 두 플래그를 둘 수 있고, `confirm` 뒤에는 `--lang`만 둘 수
있습니다.

## 사용자 소유 작업

Tailscale 로그인, DevSpace Owner 암호 입력, Oracle ChatGPT 로그인, ChatGPT
개발자 모드와 앱 등록, Owner OAuth 승인은 사용자가 직접 합니다. 에이전트는
ChatGPT 설정을 바꾸거나 앱을 만들고 지우지 않으며, 권한이나 도구를 선택하지
않습니다.

`06b_local_network_access`는 먼저
`python onboard.py consent 06b_local_network_access`로 `chatgpt.com` 한정 변경에
동의받은 뒤에만 적용합니다.

`+`/`만들기`가 없으면 먼저 ChatGPT 웹인지, 개인/관리 워크스페이스인지, 개발자
모드가 켜졌는지, `앱` 대신 `플러그인` UI인지 확인합니다. 요금제는 마지막 가설이며
임의로 Business 필요라고 결론내리지 않습니다.

## 완료 판정

다음 명령으로 상태를 확인합니다.

```powershell
python onboard.py status --provider <p> --public-url <url> --root <r>
```

`08_final_gate`는 onboarding `ready`와 새 일반(non-Pro) Oracle `@<앱이름>`
읽기 전용 검사로 exact root를 열고 작은 디렉터리를 나열한 결과를 함께 요구합니다.
Codex Desktop 내장 DevSpace 플러그인은 다른 연결이므로 증거로 쓰지 않습니다.

일반 비-Pro Oracle의 실제 결과를 기록할 때는 `--root`, 충분히 구체적인 `--evidence`,
하나 이상의 반복 가능한 `--listing`을 모두 제공합니다.

```powershell
python onboard.py record-final-gate --run-dir <Oracle run 디렉터리> `
  --root <프로젝트 폴더> `
  --evidence "읽은 경로와 결과 요약" `
  --listing <항목1> `
  --listing <항목2>
```

마법사는 run 경로, exact root/app, 일반 GPT-5.6 extra-high, terminal EXECUTED,
conversation URL, output SHA와 최종 marker를 다시 검증합니다. 임의 설명문은 완료
증거가 아닙니다. 요약이 너무 짧거나 목록이 없으면 `FINAL_GATE_EVIDENCE_INSUFFICIENT`, 일반 비-Pro
Oracle 이외의 transport면 `FINAL_GATE_TRANSPORT_MUST_BE_REGULAR_NON_PRO_ORACLE`로
거부합니다. 온보딩 상태 파일이 손상되면 `ONBOARDING_STATE_CORRUPT`로 실패 폐쇄합니다.

`06_oracle_login`과 `07_chatgpt_app`의 `confirm`은 사용자 진술을 기록할 뿐, 로그인이나
앱 연결의 기능 증명이 아닙니다. `08_final_gate` 전에는 “설치 완료”라고 보고하지
않습니다. 대신 정확히 다음 중 현재 상태를 보고합니다: 로컬 설치·연결 설정 진행 중 / ChatGPT
연결 대기 / 앱 등록 사용자 확인·기능 검증 대기. 마지막 상태인 전체 설치 및 실제
프로젝트 연결 검증 완료만 완료입니다.

전체 절차와 수동 fallback은 [최초 설치 가이드](FIRST_INSTALL.md)를 따르며, Local Multi-GPT 선택 사항은 [Local Multi-GPT](LOCAL_MULTI_GPT.md)를 확인합니다.
