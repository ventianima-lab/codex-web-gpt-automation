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
`codex` 수동 등록 → 일반 비-Pro 연결 검사 순서로 진행합니다. 기존 설치를
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
| 정확한 프로젝트 루트와 미션 SHA를 실행 전에 고정합니다. | 끊긴 실행을 새로 보내지 않고 기존 Oracle 세션에서 회수합니다. | 계획·리서치·구현·검토를 웹 ChatGPT 세션에 분리해 맡깁니다. | 영수증 기반 설치·롤백을 Windows와 macOS에서 검증합니다. |

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
7. **일반 GPT 연결 검사** — Pro를 소비하지 않고 `@codex` read probe 수행

ChatGPT 앱 `codex` 등록은 준비가 끝난 뒤 **최초 한 번 수동 등록**하는
절차입니다. ChatGPT 설정·앱 목록·권한·삭제·선택 UI를 자동화하지 않습니다.

기존 `codex` 앱은 같은 이름과 정확한 `/mcp` URL로 보존합니다. Action이 오래되면
앱 상세의 보이는 `Refresh`/`새로 고침`으로 갱신하고, OAuth 또는 도구 호출이 계속
오래되면 `https://chatgpt.com/#settings/Plugins/`에서 기존 앱을 선택해
`Reconnect`/`다시 연결`합니다. Business UI나 Refresh 부재만으로 앱을 다시 만들지
않으며, 실제 앱 레코드가 없거나 손상된 경우에만 예외적으로 재생성합니다. 필요할 때만
`post-register`를 정확히 한 번 실행한 후, 새 일반 비-Pro auditNonce canary로
`open_workspace → read → read_chunk`를 확인합니다. 위젯 도메인 경고만으로
`read_chunk`의 존재·부재를 판단하지 않습니다.

새 프로젝트를 추가할 때는 기존 root를 보존한 전체 목록에 exact folder만
추가합니다. 앱 설정은 매 작업마다 재검사하거나 자동 조작하지 않습니다.

## 모드 선택

| 원하는 결과 | 모드 | 실행 경로 |
|---|---|---|
| 질문·분석·작은 작업 | `direct` | Oracle + DevSpace |
| 구현 전 설계 | `plan` | 읽기 전용 웹 세션 |
| 코드·계획 독립 검토 | `review` | 읽기 전용 웹 세션 |
| 범위가 정해진 수정 | `edit` | 웹 구현·테스트 |
| 한 번에 끝내는 실행 | `orchestrator` | 단일 웹 세션 |
| 공개 자료 심층 조사 | `deep-research` | Oracle Deep Research |
| 독립 관점 병렬 탐색 | Web Multi-GPT | 여러 Oracle 세션 + merger |
| PC 로컬 자문·반례 탐색 | Local Multi-GPT | 선택 설치, Luna Max, 읽기 전용 |
| 계획부터 최종 gate까지 | comprehensive mode | 단계별 웹 워크플로 |
| 로컬 비용 최소화 | `ultra-economy` | Luna Max 지휘 + 분리 웹 단계 |
| Codex Ultra식 웹 분업 | `ultra-gpt` | 웹 planner/reviewer + 병렬 격리 worktree 구현 + merger/검증; 필요할 때 선택형 SHA 폐쇄 감사 |
| 명시 요청한 Pro 작업 | `pro` | GPT-5.6 Sol Pro + 읽기 전용 DevSpace 설계·자문·검토 |

자세한 선택 기준은 [전역 라우팅](docs/GLOBAL_CHATGPT_ROUTING.md),
[초절약모드](docs/ULTRA_ECONOMY_MODE.md),
[울트라 GPT 모드와 선택형 폐쇄 감사](docs/ULTRA_GPT_MODE.md)를 참고하세요.

## 실행 예시

프로젝트 안에 UTF-8 미션을 만들고 먼저 dry-run으로 신원을 확인합니다.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_dispatch.py" `
  --mode orchestrator `
  --project-root C:\project `
  --mission-path C:\project\mission.md `
  --manifest-output C:\project\.ai-bridge\oracle.json `
  --reasoning-level "Very High" `
  --dry-run
```

실제 실행 승인이 있을 때만 `--dry-run`을 제거합니다.

## 안전 계약

- 같은 Codex task와 프로젝트 조합에는 활성 또는 불확실한 Oracle 작업을 하나만 둡니다. 서로 다른 task는 같은 프로젝트에서도 별도 소유권으로 동시에 실행할 수 있으며 서로의 실행을 복구·수확·중단하지 않습니다.
- 새 프로젝트의 첫 DevSpace 제출 전에 exact root 등록을 확인합니다.
- 일반 웹 작업은 최고 지원 비-Pro 추론 강도가 기본입니다. Pro는 횟수 제한이 있으므로 사용자가 명시적으로 요청할 때만 선택하며 자동 승격하지 않습니다.
- 명시적으로 선택한 신규 Pro는 exact root에서 설계·자문·검토만 수행하는 읽기 전용 DevSpace를 사용합니다. 파일 생성·수정·삭제나 명령 실행은 최고 지원 비-Pro `GPT-5.6` `extra-high` regular DevSpace 단계가 맡습니다. 저장된 legacy `pro-devspace` 쓰기 실행은 정확한 복구 때에만 원래 권한을 보존합니다.
- 같은 읽기 전용 Pro 대화를 이어갈 때는 task-bound terminal parent에 내부 `followup` 라운드만 추가합니다. 신규 `pro-devspace-readonly` 부모의 기본 `archive=auto`는 `never`로 정규화되어 후속 입력창을 보존합니다. 명시적으로 보관했거나 과거에 자동 보관된 부모만 exact conversation URL의 제한된 복원을 사용합니다. 복원이 composer 전에 실패하면 harvest하지 않고 exact run을 보존한 채 사용자 미제출 확인 후 `settle-no-submission`으로 정산합니다. 각 라운드는 같은 conversation과 보관 전이를 다시 증명하고 mission/state/output/transcript hash 영수증을 남기며, raw Oracle follow-up 인자나 새 대화 fallback은 허용하지 않습니다. `--dry-run`은 어떤 예약도 쓰지 않으며, 실제 실행의 로컬 preflight 실패는 자녀 state/log와 hash-bound launch/result 영수증에 남습니다. 하나의 부모 대화에서는 round key가 달라도 controller가 직렬화됩니다. 과거 reservation-only 키는 삭제하거나 재생하지 않고 보존한 뒤, 이전 controller 종료를 확인하고 새 키를 사용합니다.
- 제출 후 오류는 기존 실행 신원으로 정확히 복구하며, 저장된 slug와 대화
  URL만 회수하고 자동 재제출하지 않습니다.
- Oracle이 공식 `output.md`와 완료 메타를 이미 저장했지만 외부 state 전이만
  끊긴 경우에는 소유 task의 해시 결속 `settle-saved-output`만 사용합니다.
  이 정산이 예약 CDP 포트와 Oracle 완료 메타의 실제 포트 불일치를
  검증하면 별도 v2 브라우저 신원 영수증을 자동 봉인해 동일 대화를
  후속 질문의 부모로 유지합니다. 이미 v1.19.5에서 정산된 실행은 소유
  task의 `seal-saved-output-browser-identity`로만 이 영수증을 추가합니다.
  일반 recovery의 브라우저 신원 게이트를 완화하거나 state를 직접 고치지 않습니다.
- 브라우저나 로컬 프로세스 종료만으로 웹 작업 실패를 판정하지 않습니다.
- 비밀, Owner 암호, OAuth 토큰, 브라우저 프로필은 저장소에 넣지 않습니다.
- `codexpro-*` 이름은 기존 영수증·스키마·복구 자산의 내부 호환 ID일 뿐,
  새 작업용 제품명이나 실행 경로가 아닙니다.

보안 문제는 공개 이슈 대신 [보안 정책](SECURITY.md)의 비공개 경로로 알려주세요.

## 문서 지도

| 시작 | 운영 | 고급 모드 | 프로젝트 |
|---|---|---|---|
| [최초 설치](docs/FIRST_INSTALL.md) | [DevSpace + Tailscale](docs/DEVSPACE_TAILSCALE_SETUP.md) | [초절약모드](docs/ULTRA_ECONOMY_MODE.md) · [울트라 GPT](docs/ULTRA_GPT_MODE.md) | [아키텍처 개요](docs/ARCHITECTURE.md) |
| [문서 인덱스](docs/README.md) | [전역 라우팅](docs/GLOBAL_CHATGPT_ROUTING.md) | [Local Multi-GPT](docs/LOCAL_MULTI_GPT.md) | [변경 기록](docs/CHANGELOG.md) |
| [기여 가이드](CONTRIBUTING.md) | [macOS Ultrawork](docs/MACOS_ULTRAWORK.md) | [레거시 경계](docs/FROZEN_LEGACY.md) | [버전 정책](docs/VERSIONING.md) |

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
