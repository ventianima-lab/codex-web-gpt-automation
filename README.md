<p align="center">
  <img src="docs/assets/brand/banner.svg" alt="Codex Web GPT Automation" width="100%">
</p>

<p align="center">
  <a href="https://github.com/ventianima-lab/codex-web-gpt-automation/actions/workflows/release-portability.yml"><img alt="CI" src="https://github.com/ventianima-lab/codex-web-gpt-automation/actions/workflows/release-portability.yml/badge.svg"></a>
  <a href="https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/tag/ventianima-lab/codex-web-gpt-automation?sort=semver&label=release"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/ventianima-lab/codex-web-gpt-automation"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS-334155">
  <img alt="Oracle" src="https://img.shields.io/badge/Oracle-0.18.0-8B5CF6">
  <img alt="DevSpace" src="https://img.shields.io/badge/DevSpace-1.0.8-14B8A6">
</p>

<p align="center">
  <strong>로컬 Codex 프로젝트에 웹 ChatGPT를 안전하고 복구 가능한 실행 계층으로 연결합니다.</strong>
</p>

<p align="center">
  한국어 · <a href="README.en.md">English</a> · <a href="docs/README.md">문서 전체 보기</a>
</p>

> [!IMPORTANT]
> 이 저장소는 커뮤니티 프로젝트이며 OpenAI의 공식 제품이 아닙니다. ChatGPT
> 로그인, Developer Mode 앱 등록, DevSpace Owner 승인은 사용자가 직접 수행합니다.

## 바로 시작하기

| 처음 설치 | 이미 설치됨 | 문제 해결 | 기여하기 |
|---|---|---|---|
| [최초 설치 가이드](docs/FIRST_INSTALL.md) | `python doctor.py`로 현재 상태 확인 | [진단·복구 문서](docs/README.md) | [기여 가이드](CONTRIBUTING.md) |

설치 → 고정 HTTPS 주소 승인 → DevSpace exact root 등록 → 재부팅 유지 서비스 →
endpoint 확인 → Oracle 전용 브라우저 로그인 → Local Network 권한 → ChatGPT 앱
`codex` 수동 등록 → 실제 프로젝트 읽기 확인으로 진행합니다. 기존 설치를
업데이트할 때는 [최신 릴리스](https://github.com/ventianima-lab/codex-web-gpt-automation/releases/latest)의
변경 기록을 먼저 확인하세요.

저장소 주소만 AI 코딩 에이전트에게 주고 설치를 맡길 때는
[설치 계약](docs/INSTALL_AGENT.md)을 함께 읽히세요. 설치 후 `python onboard.py start`
마법사가 남은 9단계를 하나씩 안내합니다. 로그인·앱 등록 `confirm`은 사용자 확인으로
표시하며, 실제 프로젝트 읽기 검증까지 끝난 뒤에만
설치 완료로 판정합니다.

## 왜 이 도구를 쓰나요?

| Guarded | Recoverable | Web-first | Cross-platform |
|---|---|---|---|
| 정확한 프로젝트 루트와 미션 SHA를 실행 전에 고정합니다. | 끊긴 실행을 새로 보내지 않고 기존 Oracle 세션에서 회수합니다. | 필요한 작업을 하나의 미션으로 전달하고 모델·강도를 선택합니다. | 영수증 기반 설치·롤백을 Windows와 macOS에서 검증합니다. |

Codex Web GPT Automation은 [Oracle](https://github.com/steipete/oracle)로
로그인된 ChatGPT 브라우저 세션을 실행하고,
[DevSpace](https://github.com/Waishnav/devspace)로 사용자가 허용한 프로젝트만
웹 GPT에 노출합니다. 로컬 Codex는 제출 신원, 복구, 해시, 최종 결정론적
테스트를 책임집니다.

```text
로컬 Codex
  └─ UTF-8 미션 + exact project root + SHA-256
       └─ Oracle → 로그인된 웹 ChatGPT 세션
            └─ DevSpace → 승인된 프로젝트만 읽기/작업
                 └─ 결과 회수 → 신원·해시·최종 gate
```

## 3분 설치

### Windows

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
.\install.ps1 -WhatIf
.\install.ps1
python doctor.py
```

### macOS

```bash
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python3 install.py --dry-run
python3 install.py
python3 doctor.py
```

대화형 최초 설치는 선택 기능인 Local Multi-GPT를 설치할지 묻고 기본값은
`No`입니다. 설치기는 기존 전역 파일을 백업하고 `~/.codex/receipts`에 영수증을
남깁니다. 설치 후 Codex를 재시작하세요.

> [!NOTE]
> 파일 설치만으로 ChatGPT 연결이 끝나지는 않습니다. 아래 최초 연결 절차를
> 한 번 완료해야 합니다.

## 최초 연결 순서

순서를 바꾸지 않는 것이 중요합니다. 전체 명령과 분기 기준은
[최초 설치 가이드](docs/FIRST_INSTALL.md)가 권위 문서입니다.

1. **고정 공개 경로 선택** — Tailscale Funnel 권장, Cloudflare Named Tunnel,
   ngrok 고정 도메인, custom HTTPS proxy 지원
2. **DevSpace 설정** — 사용할 모든 exact project root와 public origin 등록
3. **Owner 승인 정보 보존** — 암호를 CLI·Git·로그에 복사하지 않음
4. **상주 복구 검증** — 로그인 watchdog, local/public endpoint, root persistence 확인
5. **Oracle 전용 브라우저 로그인 및 Local network 영속 허용** — 일상 Chrome과 분리; Windows helper는 정확한 `chatgpt.com` 정책을 우선하고 정책 ACL이 잠겨 있으면 Oracle seed 프로필에만 백업·영수증과 함께 저장하며, Oracle 격리 Chrome의 반복 복구 팝업도 일반 Chrome 설정을 건드리지 않고 억제
6. **ChatGPT 앱 수동 등록** — 이름 `codex`, URL `https://고정주소/mcp`
7. **앱 연결 검사** — 선택한 모델로 `@codex`의 실제 파일 읽기를 한 번 확인

ChatGPT 앱 `codex` 등록은 준비가 끝난 뒤 **최초 한 번 수동 등록**하는
절차입니다. ChatGPT 설정·앱 목록·권한·삭제·선택 UI를 자동화하지 않습니다.

기존 `codex` 앱은 같은 이름과 정확한 `/mcp` URL로 보존합니다. Action이 오래되면
앱 상세의 보이는 `Refresh`/`새로 고침`으로 갱신하고, OAuth 또는 도구 호출이 계속
오래되면 `https://chatgpt.com/#settings/Plugins/`에서 기존 앱을 선택해
`Reconnect`/`다시 연결`합니다. Business UI나 Refresh 부재만으로 앱을 다시 만들지
않으며, 실제 앱 레코드가 없거나 손상된 경우에만 예외적으로 재생성합니다. 필요할 때만
`post-register`를 실행한 후, 선택한 모델로 승인된 파일을 실제로 읽고 결과를
저장합니다. 고정 도구 순서나 감사 영수증은 요구하지 않습니다. 위젯 도메인 경고만으로
`read_chunk`의 존재·부재를 판단하지 않습니다.

새 프로젝트를 추가할 때는 기존 root를 보존한 전체 목록에 exact folder만
추가합니다. 앱 설정은 매 작업마다 재검사하거나 자동 조작하지 않습니다.

## 하나의 실행 흐름

[공통 자동화 규칙](docs/AUTOMATION_POLICY.md)을 앱과 모든 프로젝트가 함께
따릅니다. 계획·검토·수정·리서치는 미션 내용으로 전달하며 별도 실행 모드를
선택하지 않습니다. 호출할 모델과 추론 강도만 명시합니다.

기본 호환 경로는 **최신 → Pro (6 Pro)** 입니다. GPT-5.6을 숫자로 선택하지
않습니다. 임시채팅 맞춤화를 자동으로 켜고 확인한 뒤 실행하고, 답변을
로컬에 저장한 뒤 해당 실행이 소유한 탭만 닫습니다. 오류·타임아웃에는 같은
탭을 복구하며 자동 재전송하거나 아카이브·복원을 수행하지 않습니다.

프로젝트 고유 테스트와 안전 규칙은 유지하지만, 강제 도구 호출 순서·감사
영수증 3개·반복 자격 검사·필수 완료 마커는 요구하지 않습니다.

## 실행 예시

프로젝트 안에 UTF-8 미션을 만들고 먼저 dry-run으로 신원을 확인합니다.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" `
  --project-root C:\project `
  --mission-path C:\project\mission.md `
  --model latest `
  --effort pro `
  --dry-run
```

실제 실행 승인이 있을 때만 `--dry-run`을 제거합니다.

## 안전 계약

[공통 정책](docs/AUTOMATION_POLICY.md)에 따라 승인된 루트·인증·실행 소유권을
보존합니다. 결과 저장 전에는 탭을 닫지 않으며, 오류에도 자동 재전송하지
않습니다. 프로젝트의 고유 테스트와 안전 규칙은 유지합니다.
비밀·Owner 암호·OAuth 토큰·브라우저 프로필은 저장소에 넣지 않습니다.
보안 문제는 [비공개 보안 경로](SECURITY.md)로 알려주세요.

## 문서 지도

- [최초 설치](docs/FIRST_INSTALL.md)
- [공통 자동화 규칙](docs/AUTOMATION_POLICY.md)
- [DevSpace 설정](docs/DEVSPACE_TAILSCALE_SETUP.md)
- [아키텍처](docs/ARCHITECTURE.md) · [문서 인덱스](docs/README.md)
- [과거 실행 복구 참고](docs/FROZEN_LEGACY.md)

## 버전과 지원

이 프로젝트는 `MAJOR.MINOR.PATCH` 형식의 [Semantic Versioning](https://semver.org/)을
사용합니다. `package.json`, `package-lock.json`, `install-manifest.json`, Git 태그와
GitHub Release가 같은 버전을 가리켜야 합니다. 업그레이드 전에는
[변경 기록](docs/CHANGELOG.md)을 확인하세요.

현재 기본 검증 기준은 Oracle `0.18.0`, DevSpace `1.0.8`, Node.js `>=24 <27`,
Windows 11 및 macOS 12 이상입니다. 공식 npm `latest`는 즉시 후보로 감지하지만,
격리된 archive·패치·무전송·교차 플랫폼 검증과 리뷰를 통과한 버전만 current로
승격합니다. 6시간 감시자는 이슈만 만들고 호스트를 변경하지 않습니다. 별도의 예약된
Codex 유지관리 자동화가 24시간 안에 검증을 시작해 검증 PR·CI·릴리스·설치 및 안전
창의 단일 DevSpace 재시작을 맡고, 차단이 없으면 48시간 내 승격을 목표로 합니다. 안정판
patch/minor는 모든 게이트가 통과하면 별도 사용자 확인 없이 승인됩니다. major,
권한/OAuth 변경, 패치 충돌, 실패 또는 모호한 결과만 명시적 사용자 승인이
필요합니다. 직전 Oracle `0.17.1`과 DevSpace `1.0.7`은 롤백 LKG 및 과거 실행
복구용으로 보존하며 신규 작업의 기본값으로 사용하지 않습니다. 자세한 계약은
[업스트림 런타임 정책](docs/UPSTREAM_RUNTIME_POLICY.md)을 참고하세요.

WebJjonku Linux의 archive 검증 프로필도 같은 Oracle `0.18.0` current를 사용합니다.

```sh
python bin/chatgpt_oracle_compat.py --profile webjjonku-linux --resolved-version "oracle 0.18.0" --package-root /exact/node_modules/@steipete/oracle --package-archive /exact/steipete-oracle-0.18.0.tgz
```

범위 제한 프로필은 버전·설치 루트·archive 세 인수를 모두 명시해야 하며 자동 탐색하지 않습니다.

## 라이선스

[MIT License](LICENSE). Oracle·DevSpace 등 제3자 구성요소의 저작권과 라이선스는
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 정리되어 있습니다.
