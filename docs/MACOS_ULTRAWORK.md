# macOS Ultrawork와 80분 상태 점검

## 경계

macOS 신규 작업은 Oracle 0.18.0, DevSpace 1.0.8, Tailscale Funnel,
OMO Codex Light를 사용한다. 구형 CodexPro/agbrowse 자산은 저장된 Windows
실행 복구 전용이다. `com.ventianima.codexpro-automation.*`만 이 프로젝트가
관리하며 기존 `com.openclaw.codexpro*`와 `~/.codexpro`는 건드리지 않는다.

신규 제출 경로는 Oracle과 수동 등록 DevSpace 앱이다. 아래 marketplace/hook
plugin과 OMO 항목은 macOS 수명주기 보조 도구이며 제출 경로가 아니다. 최초
설치와 ChatGPT 연결 순서는 [최초 설치 가이드](FIRST_INSTALL.md)가 기준이다.

## 설치 순서

```bash
python3 install.py --dry-run
python3 install.py
python3 doctor.py

### 설치된 로컬 legacy marketplace와 hook plugin
codex plugin marketplace add "$HOME/.codex/marketplace"
codex plugin add codexpro-harness@ventianima-local

### OMO Codex Light: OpenCode Ultimate는 설치하지 않는다
OMO_CODEX_DISABLE_POSTHOG=1 npx lazycodex-ai install --no-tui --codex-autonomous
```

네이티브 subagent는 지원되는 `[agents]` 블록만 사용한다.
`max_concurrent_threads_per_session = 3`이 하드 상한이고 실운영 기본
동시 작업자는 2명이다. 불안정한 `multi_agent_v2`는 켜지 않는다. 설정은
`python3 "$HOME/.codex/bin/codex_global_agents_setup.py" --doctor`로 확인한다.
웹 wave와 로컬 subagent wave는 동시에 실행하지 않는다. Oracle Web Multi는
provider 세션 5개로 별도 제한된다.

## DevSpace와 Tailscale

`tailscale-app` 설치, Tailscale 로그인, Funnel 계정 승인과 macOS 보안
승인은 사용자가 직접 수행한다. 이후 다음 명령으로 현재 프로젝트 하나만
허용한다.

```bash
python3 "$HOME/.codex/skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py" setup \
  --root "/absolute/path/to/codexpro-automation" \
  --dry-run
```

미리보기를 확인한 뒤 `--dry-run`을 `--apply`로 바꾼다. ChatGPT Developer
Mode 등록과 Owner 승인은 수동으로 완료한다. URL은 doctor가 출력하는
`https://<magic-dns>/mcp`다. 표준 HTTPS 443을 사용하므로 ChatGPT OAuth
메타데이터 수집 경로에도 별도 포트가 붙지 않는다.

### Cloudflare Named Tunnel 대체 경로

OpenAI 쪽에서 Tailscale Funnel 엣지 연결이 반복적으로 시간 초과하면, 기존
터널이나 `com.openclaw.*` 서비스를 재사용하지 말고 DevSpace 전용 터널을
만든다.

```bash
cloudflared tunnel create codexpro-devspace-macos
cloudflared tunnel route dns <tunnel-uuid> devspace.example.com

python3 "$HOME/.codex/bin/codexpro_cloudflared_launchd.py" install \
  --project-root "/absolute/path/to/codexpro-automation" \
  --cloudflared "/opt/homebrew/bin/cloudflared" \
  --hostname "devspace.example.com" \
  --tunnel-id "<tunnel-uuid>" \
  --credentials-file "$HOME/.cloudflared/<tunnel-uuid>.json" \
  --load
python3 "$HOME/.codex/bin/codexpro_cloudflared_launchd.py" doctor
```

DevSpace의 `publicBaseUrl`은 `https://devspace.example.com`과 정확히 같아야
한다. 전환 뒤 `/healthz`, OAuth authorization-server metadata, protected
resource metadata, 인증 없는 `/mcp`의 401 challenge를 확인한다. ChatGPT
Developer Mode 앱 등록과 Owner 승인은 사용자가 새 URL로 다시 수행한다.
Cloudflare Access나 대화형 WAF challenge를 이 hostname 앞에 추가하지 않는다.
제거할 때는 아래 명령을 사용한다. 관리 표식과 정확한 label이 모두 일치할 때만
전용 plist와 생성된 config를 제거하며 Cloudflare credential JSON과 기존 터널은
보존한다.

```bash
python3 "$HOME/.codex/bin/codexpro_cloudflared_launchd.py" uninstall
```

## launchd

```bash
python3 "$HOME/.codex/bin/codexpro_macos_launchd.py" install \
  --project-root "/absolute/path/to/codexpro-automation" --load
python3 "$HOME/.codex/bin/codexpro_macos_launchd.py" doctor
```

DevSpace는 실패 시 재시작되고, Funnel 상태는 5분마다 확인되며, 하네스
감독기는 60초마다 실행된다. 제거는 동일 CLI의 `uninstall`로 이 프로젝트가
소유한 세 plist만 대상으로 한다.

Cloudflare 대체 경로의 plist는 별도 label
`com.ventianima.codexpro-automation.cloudflared-devspace`로 관리되며 기존 세
plist와 기존 Cloudflare 터널을 수정하지 않는다.

압축 clock canary는 `python3 scripts/run_harness_canary.py`로 실행한다. 배포 전
실제 85분 증거가 필요하면 같은 명령에 `--real-time`을 추가하며, 이 경로도
80분 caution/status-audit 뒤 작업이 종료·해제되지 않았음을 85분 해시
영수증으로 남긴다.

## 하네스 상태

`codexpro_harness.py start`는 미션 해시, Codex session ID, OMO 경로,
Oracle slug/URL, todo와 다음 지시를 host-only 상태에 저장한다.

- 4,800초: exact run의 생존·진행·출력·terminal 증거를 확인하는 caution audit
- 6,000초: 환경에서 관측한 provider 경계 및 기본 browser observation window

4,800초는 종료·실패·소유권 해제·replacement 제출 조건이 아니다. 살아 있는
Oracle run은 동일 exact slug의 `RECOVER_SAME_SESSION` 관찰만 이어간다. 시간
외의 terminal 증거와 명시적 owner release가 모두 있을 때만 다음 단계로 간다.

## GJC 인터뷰

`chatgpt_gjc_interview.py`는 1~6개 최상위 구성요소를 먼저 고정하고 한
라운드에 한 질문만 낸다. goal 35%, constraints 25%, success criteria 25%,
context 15%로 coverage를 계산하고 모호성 0.35 이하에서 한 문장 재진술과
명시적 승인을 요구한다. 모순·회피·범위 확장은 모호성을 다시 높인다.

## Computer Use

Computer Use는 Tailscale 앱 상태와 macOS GUI의 시각적 QA에만 사용한다.
ChatGPT 설정, Terminal, 자격 증명, 보안 승인, Developer Mode 앱 등록은
자동화하지 않는다. 기능 증거는 가능한 경우 HTTP 또는 tmux를 우선한다.
