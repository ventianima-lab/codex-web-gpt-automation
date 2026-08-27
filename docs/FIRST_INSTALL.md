# Codex Web GPT Automation 최초 설치

이 문서는 설치, 공개 주소, DevSpace, Oracle 전용 브라우저, ChatGPT 앱 등록을
한 번에 끝내는 기준 절차입니다. 순서를 바꾸면 OAuth 주소나 허용 루트가 어긋날
수 있으므로 1번부터 진행합니다.

## 결론부터

- 제품 이름: **Codex Web GPT Automation**
- 저장소: `codex-web-gpt-automation`
- ChatGPT에 표시할 앱 이름: **`codex`**
- 권장 공개 경로: **Tailscale Funnel**
- DevSpace 기본 로컬 주소: `http://127.0.0.1:7676/mcp`
- ChatGPT 등록 주소: 고정 HTTPS 주소의 `/mcp`

`codexpro-*` 파일명, 상태 스키마, 설치 영수증은 기존 실행 복구와 롤백을 위해
내부 호환 ID로 유지합니다. 새 작업을 CodexPro 엔진으로 보내는 뜻이 아닙니다.

## 자동 설치 마법사 (권장)

아래 수동 절차를 한 단계씩 해석하기보다 마법사 상태를 기준으로 진행합니다. Windows
PowerShell에서는 `python`, macOS에서는 `python3`을 사용합니다.

```powershell
python onboard.py start --root <프로젝트 폴더>
python onboard.py next
python onboard.py confirm <stage-id>
python onboard.py status --provider <p> --public-url <url> --root <r>
```

`start`는 비밀값 없이 온보딩 상태를 기록하고 첫 행동을 출력합니다. `--provider`의
기본값은 `tailscale`, `--app-name`의 기본값은 `codex`입니다. `--root`는 여러 번
지정할 수 있습니다. Tailscale은 hostname을 자동 발견하므로 `--public-url`이
선택 사항입니다. cloudflare, ngrok, custom은 고정 `https://.../mcp` 주소를
반드시 제공합니다.

이미 진행 중인 유효한 온보딩이 있으면 `start`는 `ONBOARDING_ALREADY_STARTED`로
멈춥니다. 계속하려면 `python onboard.py resume`을 사용합니다. 처음부터 다시 할 때만
`python onboard.py start --reset --root <프로젝트 폴더>`를 사용합니다. `--reset`은
기존 진행 상태를 버리고 새 상태로 덮어씁니다.

`next`는 현재 단계 하나만 출력합니다. 자동으로 실행할 일과 사용자가 browser/TTY에서
직접 할 일을 구분하며, 완료 단계를 다시 실행하거나 다음 단계로 건너뛰지 않습니다.
사용자 소유 단계는 `confirm <stage-id>`로 확인합니다. `02_stable_endpoint`는 사용자가
고정 주소와 전체 root 계획을 승인했다는 명시적 기록이며, 나머지 단계는 마법사가
비밀이 아닌 실제 검사를 다시 수행해 함께 통과해야 합니다. 실패하면 진행을 거부합니다. 앞선 단계가
아직 미검증이면 `accepted: false`, `STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING`와
막힌 단계 ID를 돌려줍니다. 앞서 확인하지 말고 `next`가 가리키는 단계를 따릅니다.

출력 언어는 셸 로케일을 따라 한국어 또는 영어로 자동 선택됩니다. 강제로 바꿀 때는
`--lang ko` 또는 `--lang en`을 사용하고, 원본 JSON이 필요하면 `--json`을 붙입니다.
모든 하위 명령에서는 전역 위치인 `python onboard.py --lang en next`와
`python onboard.py --json next`를 쓸 수 있습니다. `next`와 `resume`은 명령 뒤에도
두 플래그를 받습니다. 예: `python onboard.py next --lang en`,
`python onboard.py resume --json`. `confirm` 뒤에는 `--lang`만 받습니다.

상태 파일은 Windows에서
`%USERPROFILE%\.codex\state\codex-web-gpt-automation\onboarding\state.json`,
macOS에서 `~/.codex/state/...`입니다. provider, 등록 URL, exact allowed roots, 앱
이름, 단계 상태와 시각만 저장합니다. 암호, token, cookie, OAuth secret은 저장하지
않습니다. 상태 구조가 맞지 않으면 `ONBOARDING_STATE_CORRUPT`로 실패 폐쇄합니다.

단계 ID는 아래와 같습니다.

```text
01_install, 02_stable_endpoint, 03_devspace_init, 04_reboot_service,
05_endpoint_check, 06_oracle_login, 06b_local_network_access,
07_chatgpt_app, 08_final_gate
```

완료 표시는 네 가지를 구분합니다.

- 로컬 설치·연결 설정 진행 중
- ChatGPT 연결 대기
- 앱 등록 완료·검증 대기
- 전체 설치 및 실제 프로젝트 연결 검증 완료

마지막 상태만 설치 완료입니다.

### 사용자가 직접 하는 단계

Tailscale 로그인, DevSpace Owner 암호 입력, Oracle ChatGPT 로그인, ChatGPT 개발자
모드와 앱 등록, Owner OAuth 승인은 사용자가 직접 완료합니다. 자동화는 ChatGPT
설정을 바꾸거나 앱을 만들고 지우지 않으며, 권한·도구를 고르거나 Owner 암호를
입력하지 않습니다.

Chrome의 Local Network 권한은 `06b_local_network_access`에서 먼저
`python onboard.py consent 06b_local_network_access`로 범위를 확인한 뒤에만
`chatgpt.com` origin에 한정해 자동 적용합니다. 사용자의 일상 Chrome 설정이나 다른
사이트 권한은 건드리지 않습니다.

앱 등록 단계에서 마법사는 앱 이름과 정확한 `https://<고정호스트>/mcp` URL을
출력합니다. 계정 UI가 다르므로 둘 다 확인합니다.

1. `설정 → 플러그인 → (맨 아래) 개발자 모드`를 켠 뒤 왼쪽 `플러그인 → +`
2. `설정 → 앱 → 고급 설정 → 개발자 모드`를 켠 뒤 `앱 → 만들기`

관리 워크스페이스는 `워크스페이스 설정 → 앱 → 만들기`를 사용합니다. `+` 또는
`만들기`가 없으면 다음 순서로 확인합니다.

1. ChatGPT 웹인지 확인
2. 개인/관리 워크스페이스 확인
3. 개발자 모드 토글 확인
4. `앱` 대신 `플러그인` UI인지 확인

Plus/Pro에서도 보통 등록할 수 있으므로 요금제는 마지막 가설입니다.

ChatGPT의 등록 앱 Action 목록은 고정 스냅샷일 수 있습니다. 새 final canary에서
`open_workspace`와 `read`만 보이고 `read_chunk`가 없거나 서버 생성 Audit receipt ID가
보이지 않으면 부분 성공으로 처리하지 않습니다. 정확한 기존 `codex` 앱의 이름과 `/mcp`
URL을 보존하고, 앱 상세에서 보이는 `Refresh` 또는 `새로 고침`으로 Action을 갱신한 뒤
새 Action을 검토·활성화합니다. 에이전트는 이 ChatGPT 설정을 자동 조작하지 않습니다.

OAuth 또는 도구 호출이 계속 오래되면 `https://chatgpt.com/#settings/Plugins/`에서 기존
`codex` 앱을 선택하고 `Reconnect`/`다시 연결`을 직접 실행합니다. Business라는 이유나
`Refresh`가 보이지 않는다는 이유만으로 앱을 삭제·재등록하지 않습니다. `post-register`는
방금 등록했거나 다시 연결한 뒤 `08_final_gate` 또는 진단이 요구할 때만 정확히 한 번
실행하고, 이어서 새 일반 비-Pro auditNonce canary를 실행합니다. 앱 레코드가 실제로
없거나 손상되어 기존 앱을 선택·갱신·재연결할 수 없을 때만 예외적으로 같은 정확한 이름과
`/mcp` URL로 다시 만듭니다.

위젯 도메인 경고는 앱 제출에 필요한 UI 메타데이터에 관한 경고이며 `read_chunk` Action의
존재·부재를 증명하지 않습니다. 관리형 DevSpace 호환 패치는 정확한 자격 증명 없는 공개
HTTPS origin을 제공합니다. 설치 뒤에도 경고가 남으면 기존 앱의 Action을 `새로 고침`하고
앱을 다시 만들지 않습니다. 이 경우에도 Action 목록 추측 대신 새 canary로 실제
`open_workspace → read → read_chunk`를 확인합니다.

### 실제 연결 확인

인증 없는 local/public `/mcp`의 HTTP `401`은 정상입니다. 연결 거부 또는 timeout은
정상이 아닙니다. 최종 단계 `08_final_gate`는 마법사 상태가 `ready`이고, 새 일반
(non-Pro) Oracle `@<앱이름>` 읽기 전용 검사가 exact 프로젝트 root를 열어 작은
디렉터리 목록을 읽을 때만 통과합니다. Codex Desktop 내장 DevSpace 플러그인은 다른
연결이므로 이 검증 증거로 사용하지 않으며, 첫 검증에 Pro 세션을 쓰지 않습니다.

일반 비-Pro Oracle에서 실제 읽기를 확인한 뒤에만 gate를 기록합니다. 요약은 충분히
구체적으로 쓰고, 관찰한 디렉터리 항목을 하나 이상 넣습니다. `--listing`은 반복할 수
있습니다.

먼저 exact 프로젝트 root 안에 짧은 읽기 전용 canary 미션을 둔 뒤, 현재 Codex 작업에서
manifest와 dry-run/live 명령을 생성합니다. 이 명령은 새 canary만
`registered_app_final_gate=true`로 표시하고 현재 작업 ID를 결속합니다.

```powershell
python onboard.py prepare-final-gate `
  --root <프로젝트 폴더> `
  --mission-path <프로젝트 폴더>\missions\onboarding-final-gate.md
```

출력된 `dry_run_command`를 먼저 실행하고 `submission_action=none`을 확인한 다음,
출력된 `run_command`를 같은 Codex 작업에서 한 번만 실행합니다. 일반 터미널이나 다른
Codex 작업에서는 task 결속 검증이 fail-closed 됩니다.

```powershell
python onboard.py record-final-gate --run-dir <Oracle run 디렉터리> `
  --root <프로젝트 폴더> `
  --evidence "읽은 경로와 결과 요약" `
  --listing <항목1> `
  --listing <항목2>
```

마법사는 run이 `%USERPROFILE%\.codex\state` 아래에 있는지, exact root/app 이름,
일반 `GPT-5.6` extra-high, terminal EXECUTED, conversation URL, output SHA-256과
`TASK_OUTCOME: EXECUTED` 최종 행까지 재검증합니다. 임의의 설명문이나 다른 커넥터의
목록을 증거로 넣을 수 없습니다. 증거 요약이 너무 짧거나 목록이 없으면
`FINAL_GATE_EVIDENCE_INSUFFICIENT`로 거부합니다.
일반 비-Pro Oracle 이외의 transport는
`FINAL_GATE_TRANSPORT_MUST_BE_REGULAR_NON_PRO_ORACLE`로 거부합니다.

canary는 반드시 같은 workspaceId의 `open_workspace`, 별도 `read`, 같은 파일의 offset
0부터 EOF까지 `read_chunk` 및 서버 생성 receipt ID 세 개를 모두 증명해야 합니다.
`open_workspace/read`만 성공한 것은 앱 갱신 뒤에도 최종 gate를 통과시키지 않습니다.

## 0. 공개 경로 선택

| 경로 | 권장 상황 | 완료 조건 | 주의점 |
|---|---|---|---|
| Tailscale Funnel | 개인 PC, 재부팅 자동 복구 | 고정 `*.ts.net` 주소와 로그인 시작 복구 | 이 저장소가 자동 설정·진단하는 기준 경로 |
| Cloudflare named tunnel | 소유 도메인이나 중앙 관리가 필요할 때 | named tunnel과 고정 hostname, OS 서비스 | 임시 `*.trycloudflare.com` URL은 금지 |
| ngrok static/reserved domain | ngrok 계정의 고정 도메인을 쓸 때 | 고정 도메인과 OS 시작 서비스 | 실행할 때마다 바뀌는 URL은 금지 |
| custom HTTPS proxy | 이미 역방향 프록시가 있을 때 | 고정 HTTPS `/mcp`, OAuth 전달, 재부팅 복구 | 운영 책임은 해당 프록시 구성에 있음 |

ChatGPT는 공개 HTTPS MCP endpoint 또는 Secure MCP Tunnel을 지원합니다. 다만
현재 이 저장소에서 DevSpace OAuth·로그인 복구까지 자동 검증한 경로는 Tailscale
Funnel입니다. Cloudflare/ngrok/custom은 고정 주소와 부팅 서비스를 직접 준비한
뒤 같은 DevSpace·Oracle·ChatGPT 단계로 합류합니다.

## 1. 저장소 설치

Windows PowerShell:

```powershell
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python install.py --dry-run
python install.py
python doctor.py
```

첫 대화형 설치에서는 환경 언어에 따라 `Local Multi-GPT도 설치할까요? [y/N]` 또는
`Install optional Local Multi-GPT too? [y/N]`를 묻습니다.
곁다리 기능이므로 기본값은 아니오입니다. 필요한 경우에만 Windows에서는
`.\install.ps1 -EnableLocalMultiGpt`, 공통 Python lifecycle에서는
`python install.py --enable-local-multi-gpt`를 사용합니다. 선택하면 스킬,
서버 파일, `multi_gpt` MCP 등록이 함께 완료되며 Codex를 재시작해야 합니다.
자세한 내용은 [선택형 Local Multi-GPT](LOCAL_MULTI_GPT.md)를 참고하세요.

### 선택 권장: Codex 네이티브 서브에이전트

비용 제한형 전역 기본값은 lifecycle 설치가 끝난 뒤에만 적용합니다. 이 명령은
현재 `~/.codex/config.toml`을 다시 읽고, 관련 없는 설정과 기존 전역
`AGENTS.md`를 보존하며, 시간표시 백업과 영수증을 만든 뒤 세 가지 독립 역할을
설치합니다.

```powershell
python "$env:USERPROFILE\.codex\bin\codex_global_agents_setup.py"
python "$env:USERPROFILE\.codex\bin\codex_global_agents_setup.py" --apply
python "$env:USERPROFILE\.codex\bin\codex_global_agents_setup.py" --doctor
```

주 에이전트는 GPT-5.6 Sol high, 일반 서브에이전트 기본값은 GPT-5.6 Terra
medium입니다. 생성 작업의 하드 상한은 3개이고 정책상 기본 동시 작업자는
2명입니다. `scout`는 Luna max/read-only, `implementer`는 명시된 파일만
맡는 Terra high, `verifier`는 Terra high/read-only입니다. 불안정한
`multi_agent_v2`는 켜지 않습니다. 적용 후 Codex를 재시작해야 새 작업이 전역
설정과 역할 목록을 다시 읽습니다.

macOS:

```bash
git clone https://github.com/ventianima-lab/codex-web-gpt-automation.git
cd codex-web-gpt-automation
python3 install.py --dry-run
python3 install.py
python3 doctor.py
```

설치기는 기존 전역 파일을 백업하고 `.codex/receipts`에 영수증을 기록합니다.
`rollback.py`와 `uninstall.py`는 그 영수증을 기준으로 동작합니다.

## 2. 전체 계획을 먼저 출력

허용할 프로젝트 루트를 모두 넣습니다. 드라이브 전체는 허용하지 않습니다.

```powershell
python onboard.py plan `
  --provider tailscale `
  --public-url https://your-device.your-tailnet.ts.net/mcp `
  --root C:\projects\alpha `
  --root D:\projects\beta
```

계획은 설치부터 최종 gate까지 9개 단계를 JSON으로 출력합니다. 암호나 토큰을
인자로 받지 않습니다. Cloudflare, ngrok, 기존 프록시는 `--provider`만 바꾸고
고정 `/mcp` URL을 제공합니다.

## 3. 고정 HTTPS 주소와 DevSpace를 먼저 설정

### Tailscale 권장 경로

먼저 미리보기합니다.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup `
  --root C:\projects\alpha `
  --root D:\projects\beta `
  --hostname your-device.your-tailnet.ts.net `
  --dry-run
```

마법사 `start` 자체도 기존 `%USERPROFILE%\.devspace\config.json`의 `allowedRoots`와
새 루트를 합쳐 보존합니다. 기존 JSON이 손상되었거나 root 목록이 유효하지 않으면
조용히 덮어쓰지 않고 실패 폐쇄합니다. Tailscale 미리보기에서도 현재
`allowedRoots`와 새 루트를 합친 전체 목록이 표시됩니다. 목록을 확인한 뒤에만
`--apply`를 사용합니다.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py setup `
  --root C:\projects\alpha `
  --root D:\projects\beta `
  --hostname your-device.your-tailnet.ts.net `
  --apply
```

`devspace init` 화면에는 표시된 **전체 exact root**와 public origin
`https://your-device.your-tailnet.ts.net`을 입력합니다. 여기에는 `/mcp`를 붙이지
않습니다. 첫 설치의 `init`은 숨겨진 서비스 창이 아니라 현재 터미널에 표시됩니다.
초기화 직후 Owner 암호 검토도 같은 TTY에서 이어집니다. 기존 설정에 root만 추가할
때는 `auth.json`을 읽거나 바꾸지 않습니다.

### Cloudflare/ngrok/custom 경로

1. 먼저 고정 hostname을 만들고 `http://127.0.0.1:7676`으로 전달합니다.
2. 터널 클라이언트를 OS 로그인/서비스로 등록합니다.
3. `npx --yes @waishnav/devspace@1.0.8 init`을 실행합니다.
4. exact roots와 public origin을 입력합니다. public origin에는 `/mcp`를 빼고,
   ChatGPT 등록 URL에는 `/mcp`를 붙입니다.
5. DevSpace 관리 실행 환경에 아래 세 값을 유지합니다.

```text
DEVSPACE_TOOL_MODE=full
DEVSPACE_OAUTH_SCOPES=devspace,offline_access
DEVSPACE_SUBAGENTS=false
```

임시 URL은 앱 등록 후 바뀌므로 설치 완료 조건을 만족하지 않습니다.

## 4. Owner 암호 처리

DevSpace는 `init` 중 Owner 암호를 생성해 자체 로컬 `auth.json`에 저장합니다.

- 생성된 고엔트로피 암호를 유지하는 것이 기본 권장값입니다.
- 첫 설치 안내에서 `K`를 선택하면 기존 값을 유지하고, `C`를 선택한 경우에만 숨김
  입력으로 custom 암호와 확인값을 받습니다. custom 값은 16자 이상, 공백 없음,
  문자 종류 3개 이상이어야 하며 숫자 전용 값은 거부합니다.
- 별도로 다시 확인해야 하면 사용자 본인이 열린 터미널에서만 아래 명령을 실행합니다.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py owner-password
```

- 최종 Owner 암호는 대화형 TTY에 한 번만 표시되므로 즉시 암호 관리자에 저장합니다.
- CLI 인자, 환경 파일, 스크립트, Git, 이슈, 로그에 복사하지 않습니다.
- ChatGPT의 최초 OAuth 승인 페이지에서만 직접 입력합니다.
- 암호를 바꾸거나 재생성하면 기존 ChatGPT 연결도 다시 승인해야 합니다.

## 5. 재부팅 복구와 endpoint 확인

Tailscale 경로의 `setup --apply`는 로그인 시 시작되는 숨김 watchdog을 등록하고
즉시 실행합니다. watchdog은 현재 `.devspace/config.json`을 5분마다 다시 읽고,
DevSpace가 로그인 후 예기치 않게 종료돼도 서비스와 정확한 Funnel을 복구합니다.
root 목록을 wrapper나 시작 명령에 별도로 하드코딩하지 않습니다. 로그인 때 한 번만
실행하고 끝나는 시작 명령은 지속 복구로 인정하지 않습니다.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py doctor `
  --root C:\projects\alpha `
  --root D:\projects\beta `
  --hostname your-device.your-tailnet.ts.net
```

정상 DevSpace OAuth endpoint는 인증 없는 GET에 보통 `401`을 반환합니다. 연결
거부나 timeout은 정상으로 보지 않습니다. 현재 config roots와 bootstrap roots가
완전히 같아야 합니다.

## 6. Oracle 전용 브라우저 로그인과 Local network 영속 허용

일상 Chrome 프로필이 아닌 Oracle 전용 프로필을 초기화합니다.

```powershell
npx --yes @steipete/oracle@0.18.0 --engine browser `
  --browser-manual-login --browser-keep-browser `
  --browser-manual-login-profile-dir "$env:USERPROFILE\.oracle\browser-profile" `
  -p "HI"
```

열린 전용 브라우저에서 ChatGPT 로그인을 완료합니다. 이후 실제 실행은 이 프로필의
throwaway copy를 사용하므로 동시 프로젝트가 같은 브라우저 상태를 공유하지 않습니다.

Chrome의 **Local network** 권한은 `chatgpt.com`이 로컬 DevSpace MCP에 연결할 때
필요합니다. 실행용 throwaway copy에서 허용하면 다음 실행에 남지 않으므로, Windows는
다음 helper로 `https://chatgpt.com` 정확한 origin만 사용자 범위 Chrome 정책에
영속 등록합니다. 기존 정책 값은 보존하고 receipt를 남깁니다.

```powershell
python .\bin\chatgpt_chrome_local_network.py enable
python .\bin\chatgpt_chrome_local_network.py status
```

조직 정책 ACL이나 일반 사용자 권한 때문에 `CHROME_POLICY_WRITE_DENIED`가 나오면
관리자 권한을 우회하지 않습니다. 아래 비-Windows 절차와 똑같이 전용 Oracle
프로필에서 한 번 직접 허용합니다.

macOS 등 비-Windows 환경에서는 전용 Oracle 프로필에서 `chatgpt.com`의 **Local
network**를 한 번 허용한 뒤 Chrome을 완전히 종료해 seed profile에 저장합니다.
온보딩 `status`는 정책 또는 seed profile의 실제 허용을 확인하며, 로그인만 된 상태를
준비 완료로 인정하지 않습니다.

## 7. ChatGPT 앱을 마지막에 수동 등록

고정 URL, DevSpace, OAuth, 재부팅 복구, Oracle 로그인과 Local network 영속 허용이
모두 준비된 뒤 진행합니다.

1. ChatGPT `Settings` → `Security and login`에서 Developer mode를 켭니다.
2. ChatGPT Plugins 화면에서 `+`를 선택합니다.
3. 이름은 기본값 **`codex`** 또는 사용자가 정한 이름(예: `dongju`)을 입력합니다.
4. Connection URL에 `https://고정주소/mcp`를 입력합니다.
5. 발견된 도구와 metadata를 확인하고 연결을 생성합니다.
6. 표시되는 DevSpace Owner 승인 화면에서 암호를 직접 입력합니다.

자동화는 ChatGPT 설정, 앱 생성·삭제, 권한 선택, 도구 선택 UI를 조작하지 않습니다.
공식 연결 순서는 [OpenAI의 Connect and test your plugin 문서](https://developers.openai.com/plugins/deploy/connect-chatgpt)를 기준으로 합니다.

등록 또는 사용자가 요청한 재연결 직후에는 기존 설정·Owner 암호·OAuth DB·허용
루트를 보존한 채 관리 DevSpace 프로세스를 정확히 한 번 재순환합니다.

```powershell
python skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py post-register `
  --root C:\projects\alpha `
  --hostname your-device.your-tailnet.ts.net
```

그 다음 새 일반(non-Pro) Oracle `@<앱이름>` 읽기 전용 검사로 exact 프로젝트를 열고
작은 디렉터리 목록을 읽습니다. Codex Desktop에 내장된 `DevSpace` 플러그인은 수동
등록한 ChatGPT 앱과 다른 연결이므로 그 도구 결과로 앱 등록을 판정하지 않습니다.
첫 연결 검증에 Pro 세션을 사용하지 않습니다.

설치 후 일반 웹 작업은 최고 지원 비-Pro 추론 강도를 사용합니다. Pro는 횟수 제한이
있으므로 사용자가 명시적으로 요청한 경우에만 선택하며 자동 승격하지 않습니다. 명시
선택된 신규 Pro는 exact root에서 설계·자문·검토만 하는 읽기 전용 DevSpace를 사용합니다.
파일 생성·수정·삭제와 명령 실행은 최고 지원 비-Pro `GPT-5.6` `extra-high` regular
DevSpace 단계가 맡습니다. 저장된 legacy `pro-devspace` 쓰기 실행은 정확한 복구에서만
원래 권한을 유지합니다.

Oracle이 같은 이름을 사용하도록 로컬 공개 설정을 기록합니다.

```powershell
python onboard.py configure-app-name --app-name codex
```

## 8. 최종 상태 확인

```powershell
python onboard.py status `
  --provider tailscale `
  --public-url https://your-device.your-tailnet.ts.net/mcp `
  --root C:\projects\alpha `
  --root D:\projects\beta `
  --app-name codex
```

`ready: true`여야 최초 질문을 제출합니다. 새 프로젝트에서는 첫 Oracle 질문 전에
exact folder가 `allowedRoots`에 있는지만 가볍게 확인하고, config hash가 그대로면
후속 질문마다 endpoint·앱 설정을 다시 검사하지 않습니다. parent, child, 비슷한
이름의 폴더는 exact root를 대신할 수 없습니다.

## 변경별 재연결 기준

| 변경 | 필요한 조치 |
|---|---|
| 프로젝트 root 추가 | DevSpace와 bootstrap의 전체 root 목록 갱신 및 서비스 재시작; 앱 재등록 불필요 |
| 공개 hostname 또는 `/mcp` URL 변경 | ChatGPT 앱 연결 갱신 필요 |
| Owner 암호/OAuth metadata 변경 | ChatGPT 앱 재승인 또는 재연결 필요 |
| Oracle 전용 프로필 로그아웃 | 전용 브라우저에서 다시 로그인 |
| 자동화 코드 업데이트만 수행 | lifecycle install·doctor 후 Codex 앱 재시작 |

## 장애 시 중단 위치

- `DEVSPACE_EXACT_ROOT_UNAVAILABLE`: Oracle/browser를 시작하지 말고 exact root부터 등록합니다.
- local `/mcp` 실패: 터널이 아니라 DevSpace 서비스부터 복구합니다.
- public `/mcp` 실패: 고정 터널 mapping과 OS 시작 서비스를 복구합니다.
- `DEVSPACE_NATIVE_BINDING_UNAVAILABLE`: `npm install-scripts ls`에서 정확한 DevSpace
  native dependency만 검토·승인한 뒤 `better-sqlite3`를 재빌드합니다. doctor가 실제
  메모리 DB 로드에 성공하기 전에는 서비스를 시작하지 않습니다.
  이 경로는 정확한 1.0.8 package/lock hash와 `better-sqlite3@12.11.1`만 허용하며,
  서비스 로그는 실행 중에도 redaction·회전됩니다.
- 앱이 도구를 찾지 못함: 동일 URL의 MCP/OAuth 상태를 확인합니다. 자동으로 앱을
  삭제하거나 다시 만들지 않습니다. 방금 등록·재연결했다면 `post-register`를 한 번만
  실행하고 일반 Oracle 읽기 검사를 반복합니다.
- Oracle manual-login profile 미초기화: 전용 로그인만 완료합니다. 제출 전 실패는
  안전하게 pre-submit으로 정산되어야 합니다.
