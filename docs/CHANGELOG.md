# 기술 변경 기록

## 1.17.1 - WebJjonku Oracle 0.18 후속 실행 timeout 호환성

- 일반 comprehensive 자동화는 계속 검증된 Oracle 0.17.1만 허용하고,
  WebJjonku Linux 배포가 명시적으로 `webjjonku-linux` profile을 선택한 경우에만
  Oracle 0.18.0의 후속 실행 timeout 전달 패치를 적용합니다.
- 0.18.0 패치는 pristine·patched SHA-256과 npm integrity를 모두 확인하고,
  명시한 `--browser-timeout`만 child follow-up에 전달합니다. profile 누락,
  알 수 없는 버전, 해시 불일치는 브라우저 실행 전에 실패 폐쇄됩니다.
- 범위 제한 프로필은 버전·설치 루트·archive를 모두 명시해야 하며,
  Windows junction/reparse point와 archive 경로 탈출을 거부합니다. 공개
  portability CI는 Windows·macOS·Ubuntu에서 실제 0.18.0 archive를 검증합니다.

## 1.17.0 - 재개 가능한 최초 설치 마법사와 커넥터 신원 가드

- `onboard.py`에 `start`, `next`, `resume`, `confirm`, `record-final-gate`를
  추가해 최초 설치를 중단·재개 가능한 상태 기계로 만들었습니다. 상태는
  `~/.codex/state/codex-web-gpt-automation/onboarding/state.json`에 저장되며
  암호·token·cookie·OAuth secret을 담지 않도록 저장 시점에 검사합니다.
- `next`는 완료 단계를 다시 실행하거나 다음 단계로 건너뛰지 않고 현재 단계
  하나만 반환합니다. 사용자 소유 단계의 `confirm`은 실제 증거로 재검증되며,
  증거가 없으면 `STAGE_CONFIRMATION_NOT_PROVEN_BY_EVIDENCE`로 거부합니다.
- 완료 표시를 프로그램 설치 완료, ChatGPT 연결 대기, 앱 등록 완료·검증 대기,
  전체 설치 및 실제 프로젝트 연결 검증 완료로 분리했습니다. `08_final_gate`는
  일반 비-Pro Oracle exact-root 읽기 증거를 함께 요구하고, exact allowed root가
  아닌 경로는 거부합니다.
- 앱 등록 단계에서 계정별 `플러그인`과 `앱` UI 경로를 모두 안내하고, 생성
  버튼이 없을 때의 확인 순서를 제공합니다. 요금제는 마지막 가설로만 다룹니다.
- 저장소 주소만 받은 에이전트를 위해 `docs/INSTALL_AGENT.md` 설치 계약을 추가하고
  `AGENTS.md`, README, 수명주기 설치 manifest에 연결했습니다.
- `start`, `next`, `resume`이 JSON 대신 읽기 쉬운 단계 요약을 출력합니다. 셸
  로케일에 따라 한국어와 영어를 자동 선택하고 `--lang`으로 고정할 수 있으며,
  기계 판독용 원본은 `--json`으로 얻습니다.
- 마법사 회귀 테스트가 늘어나 fast gate wall-clock 예산을 60초에서 100초로
  조정했습니다. 테스트 대상과 실패 판정 기준은 그대로입니다.
- 단계는 `06b_local_network_access`를 포함한 9개입니다.
- 진행 중인 유효한 상태에서 `start`를 다시 실행하면
  `ONBOARDING_ALREADY_STARTED`로 멈추고 `resume`을 안내합니다. 기존 진행 상태를 버릴
  때만 `start --reset`으로 새 상태를 기록합니다.
- `--lang`, `--json`은 모든 하위 명령 앞에서 받습니다. `next`와 `resume`은 명령 뒤에서도
  두 플래그를 받고, `confirm`은 명령 뒤에서 `--lang`만 받습니다.
- `confirm`은 앞선 단계가 미검증이면 `accepted: false`와
  `STAGE_OUT_OF_ORDER_EARLIER_STAGE_PENDING`, 막힌 단계 ID를 반환합니다.
- 여러 ChatGPT 플러그인이 `open_workspace`, `read` 같은 동일한 도구 이름을
  노출하면 `@앱이름` 멘션만으로는 커넥터가 고정되지 않았습니다. Oracle composer
  프롬프트에 `connector_identity_guard`를 추가해 등록된 앱의 도구만 사용하고,
  미션을 읽기 전에 어느 앱의 도구를 호출해 어떤 workspace id를 받았는지 한 줄로
  밝히도록 요구합니다.
- 첫 workspace 호출이 실패해도 자체 도구 배선을 조사하거나 웹을 검색하거나 다른
  커넥터로 대체하지 않고, 같은 루트를 한 번만 재시도한 뒤 구체적 blocker를 보고하고
  멈추도록 명시합니다.
- incident classifier에 `foreign-workspace-connector-substituted` 시그니처를
  추가했습니다. 플러그인 검색 흔적과 빈 결과 또는 workspace 미발급 흔적이 함께
  있을 때만 분류하며 기존 자기관찰·OAuth 503 시그니처가 우선합니다.
- `record-final-gate`는 `--root`, `--evidence`, 반복 가능한 `--listing`을 요구합니다.
  증거 요약이 너무 짧거나 목록이 없으면 `FINAL_GATE_EVIDENCE_INSUFFICIENT`로, 일반
  비-Pro Oracle 이외의 transport면
  `FINAL_GATE_TRANSPORT_MUST_BE_REGULAR_NON_PRO_ORACLE`로 거부합니다.
- 온보딩 상태 구조가 맞지 않으면 `ONBOARDING_STATE_CORRUPT`로 실패 폐쇄합니다.

## 1.16.1 - Strict Ultra 설치 문서 동기화

- `strict-ultra` 전역 skill이 참조하는 `docs/STRICT_ULTRA.md`를 수명주기
  설치 manifest에 포함하고 설치본 경로를 명확히 했습니다.

## 1.16.0 - Strict Ultra 감사와 안전한 DevSpace 파일 제거

- 기존 Oracle Multi v2 스케줄러를 그대로 사용하는 선택형
  `strict-ultra` comprehensive 프로필을 추가했습니다. dependency,
  authority, advisory Research Governor, identity ledger, local gate, 최종
  workflow audit가 닫힌 JSON keyset과 SHA-256으로 결속됩니다.
- strict Multi 결과가 실제 wave schedule, all-lanes barrier, audited apply,
  merger를 최상위 감사 자료로 노출합니다. 5개 lane/동시성 3은 안정적인
  3+2 wave로 기록됩니다.
- DevSpace 1.0.4 호환 패치에 일반 파일 전용 `delete_file`과 복구 가능한
  `trash_file`을 추가했습니다. 절대경로·경로이탈·reparse point·Git 및
  trash 내부 대상은 실패 폐쇄하며 trash 이동은 바이트 수와 SHA-256을
  재검증합니다.
- 신규 계약은 명시적으로 선택한 경우에만 적용되며 기존 standard,
  ultra-economy, ultra-gpt, legacy 경로는 그대로 유지됩니다.

## 1.15.12 - Luna Max CLI 및 Oracle 버전 해석 복구

- Local Multi-GPT 등록이 Codex Desktop 업데이트로 사라진 구버전 CLI를
  가리키면, exact server ownership을 확인한 뒤 최신 CLI로 원자 갱신합니다.
  setup과 runtime 모두 `gpt-5.6-luna` / `max` no-run 구성 canary를 통과해야
  하며, 지원하지 않는 CLI에서는 child나 job을 만들기 전에 실패 폐쇄합니다.
- pinned Oracle 0.17.1의 `npx --version`이 일시 실패해도 정확한 로컬 npx
  캐시 package version을 확인해 브라우저 생성 전 버전 해석을 복구합니다.
- exact `ORACLE_VERSION_FAILED` pre-submit 상태는 빈 stdout/output, 대화 URL
  부재, pinned command 및 lifecycle을 모두 검증한 경우에만 공식
  no-submission 정산 대상이 됩니다. 유사 오류와 모순 증거는 거부합니다.

## 1.15.11 - DevSpace restart pre-submit 공식 정산

- direct Oracle의 exact `DEVSPACE_SERVICE_RESTART_REQUIRED` 오류를 출력·대화
  URL·Oracle 실행이 모두 없는 bounded pre-submit host failure로 분류합니다.
- 기존 `settle-no-submission` 명령이 이 exact pre-submit 증거를 mission copy,
  stderr/transcript 및 locator 해시에 결속한 append-only receipt로 정산합니다.
  유사 오류, 출력 존재, URL 존재 또는 다른 lifecycle 상태는 계속 거부합니다.

## 1.15.10 - DevSpace 테스트 restart marker 격리

- DevSpace compatibility 테스트의 restart-marker state를 각 테스트의 격리된
  임시 디렉터리로 강제했습니다. synthetic package patch가
  `%USERPROFILE%\.codex\state\devspace-compat\1.0.4\restart-required.json`을
  남겨 이후 실제 Oracle run을 제출 전에 잘못 차단할 수 없습니다.

## 1.15.9 - Oracle 재귀 자기관찰 차단

- regular direct Oracle와 comprehensive stage prompt에 exact run ID/slug를
  결속한 no-self-observation/no-nested-Oracle guard를 추가했습니다. 웹 단계는
  자신의 Oracle state/output/transcript/recovery/observer를 읽거나 기다리지
  않고 요청된 미션을 직접 수행해야 합니다.
- terminal `BLOCKED`가 exact 자기 run ID와 slug, `running`, `pending`, output
  부재, `continue-observing-same-exact-session`을 모두 포함할 때만
  `post-submit-recursive-self-observation`으로 분류합니다. 일반 BLOCKED와 단순
  식별자 언급은 기존 분류를 유지합니다.
- comprehensive stage의 해당 결함은 자동 재시도 없이 terminal BLOCKED로
  종결하여 scope를 해제합니다. fresh run은 exact state/output/transcript 해시와
  명시적 사용자 권한을 append-only receipt로 결속한 뒤에만 허용됩니다.

## 1.15.8 - Ultra review FAIL 종결 수리

- hash-bound review receipt가 `FAIL`, `ready_for_next=false`, `next_stage=null`,
  비어 있지 않은 blocker와 유효한 critical finding 결속을 제공하면 workflow를
  `BLOCKED / REVIEW_FAILED`로 즉시 종결하고 comprehensive scope를 해제합니다.
- `PASS`와 `PASS_WITH_NOTES`만 계속 `web-multi`로 진행해야 합니다. 불완전하거나
  모순된 FAIL receipt는 계속 실패 폐쇄되며, 기존 Oracle run·output·receipt는
  수정하지 않습니다.
- terminal review 상태에는 receipt SHA-256과 critical finding 집합의 해시·개수만
  보존하여 다음 workflow가 이전 의미 내용을 상속하지 않고도 정산을 감사할 수
  있습니다.

## 1.15.7 - DevSpace read bridge 사전검증 수리

- DevSpace의 50KB 초과 단일행 `read_chunk` 사전검증이 전체 MCP 서버
  모듈 그래프 import에서 멈추던 문제를 수정했습니다. 설치된
  `server.js`에서 해시 게이트된 정확한 함수 본문만 분리해 최소 Node
  프로세스에서 검증하므로 Oracle 제출 전 버전 판정이 timeout으로
  오인 실패하지 않습니다.
- 정확히 결속된 `pre_submit` bridge-timeout run은 명시적
  `user-confirmed-pre-submit-workflow-cancel` 권한으로 workflow를
  `CANCELED`로 정산하고 scope를 해제할 수 있습니다. stdout/output/
  conversation 흔적이나 다른 오류는 계속 fail-closed이며 Oracle run state는
  변경하지 않습니다.
- 동일한 정산 계약은 패치 적용 후 서비스 재시작이 필요하다는
  정확한 `DEVSPACE_SERVICE_RESTART_REQUIRED` pre-submit 오류도 구분하여
  결속합니다. 서비스 재시작은 별도 managed setup 절차로 수행하며
  정산 명령은 서비스나 prompt를 조작하지 않습니다.

## 1.15.6 - comprehensive 사용자 중지 정산

- 사용자가 provider UI에서 응답을 명시적으로 중지하고 workflow 종료를
  요청한 경우, terminal-harvested Oracle run과 exact workflow/scope/run state의
  사전 SHA-256을 요구하는 공식 `--cancel-user-stopped` 경로를 추가했습니다.
- 정산은 Oracle run state를 수정하거나 새 prompt/recovery를 만들지 않습니다.
  user authority receipt, `CANCELED` workflow, released scope, completion receipt를
  원자 기록하며 중단 후 재실행은 동일 결속에서만 idempotent하게 마무리합니다.
- scope는 `canceled`를 terminal 상태로 인정해 새 workflow가 같은 exact scope를
  청구할 수 있지만, 기존 canceled workflow 자체는 다시 활성화하지 않습니다.

## 1.15.5 - 읽기 전용 웹 표면용 Ultra host bridge

- regular comprehensive planner/reviewer가 DevSpace의 변경 도구를 받지 못해도
  hash-bound stage envelope를 반환하면 host가 workflow 소유 output, next mission,
  receipt를 동일 계약으로 materialize합니다.
- Ultra GPT strict writer는 직접 쓰기 대신 parent/lane/source-mission에 결속된
  닫힌 writeset을 반환할 수 있습니다. host는 격리 worktree의 선언된
  `owned_paths`에만 원자 적용하고 file/byte/symlink/reparse/Git delta 경계를
  검증하며 직접 delta와 writeset 혼용을 거부합니다.
- DevSpace `read_chunk`를 추가해 50KB를 넘는 단일 UTF-8 행도 24KiB 이하의
  연속 byte chunk, 전체 파일 SHA-256, EOF 결속으로 shell 없이 완전 복원합니다.
- write/edit/bash의 MCP 안전 annotation은 완화하거나 읽기 전용으로 위장하지
  않습니다.

## 1.15.4 - Oracle 0.17.1 exact-session live recovery 보강

- Oracle 0.17.1의 복구된 Pro 대화 준비 대기를 고정 60초가 아니라 host가 전달한
  `ORACLE_LIVE_TERMINAL_TIMEOUT_MS` 전체 기한까지 유지합니다.
- 느린 대화 로딩이나 장기 tool-result 대기 중 같은 exact slug/profile/tab을 보존하고,
  readiness timeout 때문에 recovery browser를 반복 생성하지 않습니다.
- published Oracle 0.17.1 byte hash, patch 결과 hash, Node 구문을 회귀 테스트로
  fail-closed 검증합니다.

## 1.15.3 - Chrome Local Network Access 최초 설치 보강

- Windows 최초 설치에서 `chatgpt.com` 정확한 origin만 Chrome의 공식
  `LocalNetworkAccessAllowedForUrls` 사용자 정책에 추가하는 receipt-backed
  helper를 제공합니다. 기존 정책 항목은 덮어쓰지 않습니다. 정책 ACL이 쓰기를
  거부하면 traceback 대신 정확한 수동 seed-profile 1회 허용 절차로 전환합니다.
- 온보딩 상태는 영속 정책 또는 전용 Oracle seed profile의 실제 Local network
  허용을 확인하며, 로그인만 된 상태를 더 이상 준비 완료로 오판하지 않습니다.
  macOS 등 비-Windows 환경은 전용 seed profile에서 한 번 직접 허용한 뒤 Chrome을
  완전히 종료하도록 안내합니다.

## 1.15.2 - 터미널 복구 후 observer 자동 정리

- exact-slug recovery가 durable output과 terminal authority를 확정하면 원래
  runner가 자신이 시작한 Oracle 프로세스 트리만 즉시 종료해 프로젝트 submit
  mutex를 반환합니다. 새 prompt나 replacement run은 만들지 않습니다.
- recovery가 먼저 끝난 뒤 80분 caution audit가 도착하더라도 이미 확정된
  `complete / terminal / terminal_harvested` 상태를 `running`으로 되돌리지 않도록
  단조성 회귀 검사를 추가했습니다. 불완전하거나 모순된 상태에는 자동 정리가
  작동하지 않습니다.

## 1.15.1 - 모델 선택 전 미제출 정산 결속

- Oracle 0.17.1이 ChatGPT 홈에서 모델 선택 버튼을 찾지 못해 prompt 전송 전에
  종료된 qualified Pro run을 사용자 확인 정산 후보로 추가했습니다. 정확한
  selector 오류만으로는 해제하지 않으며 exact slug, 버전, copied profile,
  `execute-browser` stage, `promptSubmitted=false`, 홈 URL, output·대화 URL 부재,
  recovery-binding 불가 증거와 모든 관련 SHA-256이 함께 일치해야 합니다.
- prompt 제출, 대화 URL, output, 다른 오류·stage, 누락되거나 변경된 Oracle
  session ledger 중 하나라도 있으면 기존 잠금이 유지됩니다. 정산 receipt가
  만들어진 뒤 meta가 바뀌어도 재검증이 실패해 해당 run이 다시 unresolved로
  취급됩니다.

## 1.15.0 - 울트라 GPT 모드

- setup 문서와 실제 기본 runner app name의 불일치를 제거해 기본값을 수동
  등록 권장 이름인 `codex`로 통일했습니다. 명시적 host override와 기존 custom
  app name은 계속 지원합니다.
- Codex Ultra/Multi-agent의 bounded role 분해를 독립 Oracle 웹 GPT 세션으로
  치환하는 선택형 `ultra-gpt` comprehensive 프로필을 추가했습니다. 로컬
  Codex는 native semantic subagent를 만들지 않고 exact root, mission/receipt
  해시, session lifecycle, 결정론적 gate, Git/CI/Release만 관리합니다.
- regular web planner와 별도 reviewer가 계획을 확정하고, reviewer가 2~5개의
  병렬 `worktree-write` Web Multi 구현 lane으로 분할합니다. 동시 실행은 최대
  3개이며 각 lane은 같은 Git HEAD의 별도 사전 생성 worktree에서 실행됩니다.
  host는 project-relative `owned_paths`의 동일·상위·하위 겹침과 실제 범위 밖
  변경, Git metadata 변경을 거부합니다. 모든 lane이 통과한 뒤에만 canonical
  결과에 적용하고 merger와 별도 final verifier가 순차 검증합니다.
- Pro는 프로필 내부에서 선택할 수 없습니다. 사용자가 별도로 명시 승인하고
  설계 불확실성이 실제로 있을 때만 workflow 전 사전 자문 1회를 허용합니다.
  initial stage, stage budget, Web Multi 전환, lane access와 concurrency를 모두
  제출 전 실패 폐쇄합니다.

## 1.14.7 - Oracle stale observer exact recovery

- CDP가 끊긴 원래 Oracle observer가 프로젝트 submission mutex를 계속 보유해도
  동일 run/slug의 prompt 없는 `live`/`harvest` 복구는 exact-run 전용 mutex로
  직렬화됩니다. unresolved run 상태가 새 제출을 계속 차단하므로 중복 prompt를
  허용하지 않으면서 provider-terminal 응답을 안전하게 수확할 수 있습니다.
- exact recovery가 프로젝트 mutex를 다시 기다리지 않는 회귀 테스트와 동시
  recovery writer 직렬화 경계를 추가했습니다. 기존 terminal authority의 단조성,
  exact URL/slug 결속, output/transcript 원자 저장 규칙은 그대로 유지합니다.
  늦게 종료된 원래 observer도 이미 수확된 terminal state를 덮어쓰지 못합니다.

## 1.14.6 - DevSpace OAuth 장시간 세션 안정화

- DevSpace 1.0.4의 회전형 refresh token을 여러 도구 호출이 동시에 갱신할
  때 한 요청만 성공하고 나머지가 `OAuth token request failed 503`으로
  끊기는 경쟁을 보완했습니다. 이미 소비된 token을 영구 재허용하지 않고,
  동일 client·scope·resource 요청에만 30초 동안 같은 회전 결과를 최대 32개
  메모리에서 재생합니다. 만료·불일치·revoke는 계속 fail-closed입니다.
- 호환 패치는 upstream 버전과 pristine/patched SHA-256으로 고정되며, 실제
  DevSpace 모듈과 격리 SQLite DB를 사용하는 무네트워크 replay/revoke/expiry
  검사까지 통과해야 Oracle 실행 전 호환성 확인이 성공합니다.
- 장애 복구 뒤에는 config·OAuth DB·ChatGPT 앱 설정을 변경하지 않고 관리
  서비스만 한 번 재기동한 뒤 regular non-Pro canary로 exact root의 읽기와
  no-op 명령 반환을 검증합니다. Pro는 이 canary가 성공하기 전까지 막습니다.

## 1.14.5 - DevSpace exact-root 응답 경로 보강

- Oracle의 regular·Pro DevSpace composer가 미션 경로보다 먼저 exact project
  root를 명시해 checkout으로 열도록 변경했습니다. 미션 디렉터리, 상위·하위
  폴더, 현재 활성 workspace를 exact root 대신 선택하지 못하게 해 미션과 명령
  결과가 다른 workspace session으로 분리되는 재발을 줄입니다.
- DevSpace 장애 복구 뒤 첫 검증은 계속 quota 없는 regular Oracle canary로
  수행합니다. 로컬/public doctor만으로 registered ChatGPT app 응답 성공을
  주장하거나 Pro 제출을 연결 확인용으로 소비하지 않습니다.

## 1.14.4 - Oracle 미제출 정산 잠금 호환성

- 버전 파일만 갱신하고 GitHub Release를 빠뜨리는 일을 막기 위해 annotated
  `v*` 태그 push 시 버전·태그 형식을 검증하고 Release를 자동 발행합니다.
  유지보수 스킬은 exact CI, peeled remote tag, `releases/latest`, 설치 영수증과
  source/install parity를 모두 확인하기 전에는 발행 완료로 보고하지 않습니다.
- 사용자 확인으로 정산된 Pro attachment run은 정산 뒤 프로젝트의 비미션
  첨부파일이 정상 변경돼도 당시 state와 해시 영수증에 결속된 원본 identity를
  유지합니다. 미션·운송 사본·로그·복구 증거·출력·대화 URL·영수증이 달라지면
  기존처럼 fail-closed로 잠금을 유지합니다.
- pre-submit run의 exact Oracle session 부재는 복구 로그 바이트와 locator를
  재검증한 경우에만 진단·incident packet에서 명시적으로 분류합니다. 검증된
  미제출 run만 fresh run 안전 판정에 참여합니다.

## 1.14.3 - GitHub 요청과 복구 경계 정비

- 설치 오류 뒤 정상 rollback이 완료되면 WAL을 terminal 상태로 기록하고,
  이미 backup 바이트로 복원된 항목은 재실행 때 멱등적으로 인정합니다. 실제
  외부 수정이나 누락 파일은 계속 fail-closed로 차단합니다.
- exact Oracle recovery가 provider terminal 결과를 수확했는데 로컬 observer만
  `running`으로 남은 경우 동일 run의 terminal 증거로 정합화합니다. OAuth 503과
  stale observer는 doctor가 별도 원인으로 분류합니다.
- macOS Funnel LaunchAgent에 Homebrew 우선 PATH를 명시해 headless Tailscale
  CLI를 안정적으로 선택하고 App Store GUI 번들 경로를 서비스 탐색에서 배제합니다.
  이 수정은 PR #13의 핵심 제안을 현행 main에 맞춰 반영한 것입니다.
- GitHub 메인 화면의 깨진 release badge를 tag 기반 badge로 교체하고, 첫 설치·
  진단·기여 경로를 README 상단에서 바로 찾을 수 있게 재정렬했습니다. CI는
  수동 `workflow_dispatch` 실행도 지원합니다.

## 1.14.2 - DevSpace 상주 복구

- Windows DevSpace 부트스트랩을 로그인 시 한 번 실행하고 종료하는 방식에서
  5분 간격의 숨김 per-user 감시 방식으로 변경했습니다. DevSpace 프로세스가
  로그인 이후 종료돼도 현재 `~/.devspace/config.json`의 전체 root와 정확한
  Tailscale Funnel을 자동 복구합니다.
- `setup --apply`가 감시 명령을 등록하고 즉시 시작합니다. Owner 암호, OAuth
  클라이언트·refresh token, ChatGPT 설정은 변경하거나 기록하지 않습니다.
- 설치 manifest의 새 Pro transport 표기를 실제 정책과 같은 명시적
  `pro-devspace` 읽기·쓰기 계약으로 정정했습니다. 기존 read-only run의 복구
  의미는 그대로 유지합니다.

## 1.14.1 - Pro 첨부 무전송 정산

- Oracle 0.17.1이 프롬프트 전송 전에 정확한 첨부 업로드 타임아웃을
  보고한 경우에만 사용할 수 있는 fail-closed 사용자 확인 정산 경로를
  추가했습니다.
- 정산 영수증은 run/project, 원본·운송 mission 해시, 모든 첨부파일의
  경로·크기·SHA-256, Oracle 버전·exact locator, 업로드 타임아웃 marker,
  stdout/transcript, recovery 바이트와 출력·대화 URL 부재를 결속합니다.
- 사용자 확인 token 누락, 첨부 변경, 출력·URL·live recovery, 미지원 Oracle
  버전, locator 불일치 또는 다른 오류가 하나라도 있으면 잠금을 유지하고
  replacement 제출을 금지합니다.

## 1.14.0 - 명시적 Pro 읽기·쓰기 정책

- 일반 웹 작업은 `gpt-5.6`의 최고 지원 비-Pro 추론 강도 `extra-high`를
  기본으로 사용하며 Pro로 자동 승격하지 않습니다.
- Pro는 사용자의 명시 요청에만 선택됩니다. 표준 종합 워크플로는
  `allow_pro: true`가 없으면 plan의 Pro 전환을 제출 전에 차단합니다.
- 새 qualified Pro 실행은 `pro-devspace` transport를 사용하며 exact root
  안에서 미션이 허용한 파일 쓰기와 명령 실행을 지원합니다. 기존
  `pro-devspace-readonly` 실행 기록은 복구 호환용 의미를 그대로 보존합니다.
- README, 전역 정책, 라우팅·아키텍처·설치 문서와 Pro 관련 스킬을 같은
  explicit-only/read-write 계약으로 재정렬했습니다.

## 1.13.1 - Oracle 장기 실행 상태 점검 안전성

- 80분을 종료·실패·소유권 해제 시점이 아닌 caution/status-audit 임계값으로
  정정했습니다. 동일 프로세스의 생존과 출력 진행을 기록한 뒤 계속 기다립니다.
- 브라우저 관찰 프로세스가 응답 타임아웃으로 반환해도 동일 exact slug의 live
  회수를 자동으로 이어가며, 시간만으로 새 제출이나 replacement를 만들지 않습니다.
- 종합 모드와 legacy canary에도 같은 no-time-based-termination 계약을 적용했습니다.

## 1.13.0 - 첫 설치와 DevSpace 진단 완결

- 기존 DevSpace 설정의 root 병합, Windows 재부팅 root 영속성, Unicode root의
  PowerShell 5.1 안전 직렬화를 하나의 source-of-truth 계약으로 통합했습니다.
- 첫 `devspace init`을 현재 터미널에 표시하고, 생성 Owner 암호 유지 또는 강한
  custom 암호 선택을 TTY 전용·숨김 입력으로 안내합니다.
- Funnel public endpoint에 bounded propagation retry를 추가하고 마지막 redacted
  probe를 오류에 포함합니다.
- Tailscale status JSON은 Windows ANSI locale과 무관하게 UTF-8로 읽어 Unicode
  장치명이 있어도 setup doctor가 중단되지 않습니다.
- DevSpace 시작과 lifecycle doctor가 active Node에서 `better-sqlite3` 메모리 DB를
  실제로 열어 npm 12 install-script 차단을 사전에 발견합니다.
- onboarding plan/status/configure가 기본 `codex`뿐 아니라 검증된 임의 ChatGPT
  app name을 일관되게 지원합니다.
- 초절약모드는 새 Codex 작업의 최초 요청에서만 Luna/Max 선택을 한 번 안내하고,
  사용자 확인 뒤에는 런타임 모델을 읽거나 작업 중간에 다시 묻지 않습니다.

## 1.12.1 - Oracle 사전제출 CDP 복구

- Oracle 0.17.1의 정확한 CDP 연결 해제 오류와 외부 session ledger의
  `promptSubmitted=false`가 함께 증명될 때만 qualified Pro run을
  `pre_submit / not_executed`로 안전 정산합니다.
- 출력, 대화 URL, 제출 플래그, 모델·프로필·버전 또는 오류 형태가
  조금이라도 모순되면 기존 `submitted_unknown` 잠금을 유지합니다.
- exact-slug recovery가 이 증거를 감지하면 Oracle을 다시 호출하지 않고
  프로젝트 소유권을 해제하는 standalone Pro 회귀 테스트를 추가했습니다.
- 기존 DevSpace 설정은 백업 후 전체 `allowedRoots`를 원자적으로 병합하며,
  bootstrap JSON은 진단용 mirror로만 동기화합니다.
- Windows 로그인 복구 wrapper는 매 실행마다 live
  `%USERPROFILE%\.devspace\config.json`에서 root를 읽으므로, 재부팅 시 오래된
  bootstrap 배열이 새 프로젝트를 제거하지 않습니다.
- Unicode root가 있는 설정은 ASCII-safe JSON escape로 원자 저장해, BOM 없는
  UTF-8을 ANSI로 읽는 Windows PowerShell 5.1 기본 `Get-Content`에서도 손상
  없이 파싱됩니다.

## 1.12.0 - 브랜드와 릴리스 체계

- 포털·코드 괄호·연결 노드를 결합한 프로젝트 로고, README 배너, GitHub
  소셜 프리뷰와 사용 규칙을 추가했습니다.
- 한국어·영어 README를 동일한 정보 구조로 재작성하고 최초 설치, 모드 선택,
  안전 계약과 문서 지도를 한 화면에서 찾을 수 있게 정리했습니다.
- 현행 아키텍처, 문서 인덱스, 기여 가이드, 브랜드 가이드와 SemVer 정책을
  추가하고 legacy 문서를 현재 실행 경로와 명확히 분리했습니다.
- GitHub 이슈·기능 제안·Pull Request 템플릿과 저장소 주제/설명을 정비했습니다.
- `package.json`, `package-lock.json`, `install-manifest.json`, Git 태그와 GitHub
  Release가 하나의 버전을 가리키는 릴리스 계약을 도입했습니다.

## 1.11.3 - standalone Pro 전송 불확실성 정산

- Oracle 0.17.1의 정확한 prompt-not-observed 오류와 no-live-tab/no-URL
  harvest가 함께 있을 때 standalone qualified Pro도 사용자 확인 기반의
  `settle-no-submission` 정산을 사용할 수 있습니다.
- 출력, 대화 URL, 상충 recovery 상태, 다른 Oracle 버전, 다른 transport,
  변경된 미션 바이트가 있으면 프로젝트 잠금을 계속 유지합니다.

## 1.11.2 - stale Funnel 등록 후 복구

- `post-register`가 로컬 status상 동일한 매핑이라도 외부 relay에서 닫힌
  exclusive HTTPS 슬롯을 scoped `off` 후 동일 target으로 다시 수립합니다.
- 전체 `tailscale funnel reset`은 사용하지 않으며, 같은 포트에 다른 path
  handler가 있으면 이를 보존하고 비파괴 확인만 수행합니다.

## 1.11.1 - 드라이브 루트 위생 정책

- 전역 AGENTS 정책에서 테스트·임시·로그·다운로드·dependency checkout을
  `C:\` 또는 `D:\` 바로 아래에 만들지 못하게 했습니다.
- 기본 임시 위치는 OS temp의 task별 Codex 하위 폴더이며, 짧은 경로가 꼭
  필요하면 저장소의 gitignored `.codex-tmp`를 사용합니다. 외부 소스 checkout은
  `%LOCALAPPDATA%\Codex\Sources`에 둡니다.
- 기존 루트 정리는 소유권과 실행 참조를 먼저 확인하고, 확실한 자동화 산출물만
  복구 가능한 archive로 이동하도록 명시했습니다.

## 1.11.0 - 격리된 macOS Cloudflare DevSpace 터널

- Tailscale Funnel이 OpenAI 연결 제한을 넘는 환경을 위해 별도 Named Tunnel과
  전용 LaunchAgent를 추가했습니다. 기존 Cloudflare 터널과 `com.openclaw.*`
  서비스를 재사용하거나 수정하지 않습니다.
- 설치·재시작 실패 시 기존 관리 파일과 서비스를 복구하고, doctor는 macOS에서
  실제 loaded 상태까지 검사하며, exact managed artifact만 제거하는 uninstall을
  제공합니다.

## 1.10.0 - 초절약모드

- 로컬 지휘관과 모든 네이티브 서브에이전트를 `gpt-5.6-luna` / `max`로
  제한하고, Pro 설계와 regular 웹 검토·구현·최종 검증을 분리하는 선택형
  `ultra-economy` comprehensive 프로필을 추가했습니다.
- 최초 구현은 task-bound rollout runtime evidence로 Luna Max를 검증했으나,
  1.13.0부터는 화면·런타임 판독 오류를 피하기 위해 새 작업 최초 1회 사용자
  안내·확인 계약으로 대체했습니다. 전역 `config.toml`은 자동 변경하지 않습니다.
- Pro-first와 최소 4단계 계약은 코드와 회귀 테스트로 fail-closed 고정했습니다.

## 1.9.1 - ChatGPT 앱 등록 후 연결 안정화

- 수동 ChatGPT 앱 등록·재연결 직후 기존 DevSpace 설정, Owner 자격, OAuth DB,
  허용 루트와 Funnel 주소를 보존하면서 관리 서비스를 한 번 재순환하는 명시적
  `post-register` 단계를 추가했습니다.
- 실제 등록 앱 검증은 일반(non-Pro) Oracle `@codex` 읽기 검사로 분리했습니다.
  Codex Desktop의 동명 DevSpace 플러그인은 다른 연결이므로 등록 검증에 사용하지
  않고, Pro 세션을 최초 연결 검사로 소비하지 않습니다.
- public endpoint가 정상인 상태의 앱 호출 실패가 무조건 재등록을 요구하지 않고,
  한 번의 post-register 복구 후 외부 앱 경계를 보고하도록 진단 안내를 수정했습니다.

## 1.9.0 - 선택형 Local Multi-GPT

- 첫 대화형 설치에서 `Local Multi-GPT도 설치할까요? [y/N]`를 묻고 기본값은
  아니오로 둡니다. 무인 설치는 `-EnableLocalMultiGpt` 또는
  `--enable-local-multi-gpt`를 명시해야 합니다.
- 선택하면 스킬, 서버, `multi_gpt` MCP 등록을 한 구성요소로 설치하고 하위
  단계가 사용할 호환 Codex CLI 경로를 영수증에 기록합니다.
- Multi-GPT는 PATH의 오래된 CLI보다 등록 시 검증한 Codex CLI를 우선하며,
  Planner 실패 시 stderr 진단을 보존합니다.

README는 현재 제품의 목적과 사용법만 설명합니다. 구현 변경, 호환 패치,
레거시 이전 기록은 이 문서에서 관리합니다.

## 1.8.0 — Codex Web GPT Automation

- 공개 제품명과 저장소명을 Pro 전용으로 오해되지 않는
  `Codex Web GPT Automation` / `codex-web-gpt-automation`으로 변경했습니다.
  기존 `codexpro-*` 상태, 영수증, 스키마와 복구 파일은 하위 호환 ID로
  유지합니다.
- 설치부터 고정 HTTPS endpoint, DevSpace Owner 승인, 재부팅 복구, Oracle
  전용 브라우저 로그인, ChatGPT 앱 `codex` 등록까지 순서가 고정된 최초 설치
  가이드와 fail-closed onboarding 점검기를 추가했습니다.
- Tailscale Funnel을 자동화·재부팅 검증 경로로 유지하면서 Cloudflare named
  tunnel, ngrok 고정 도메인, custom HTTPS proxy의 안전한 합류 지점을
  문서화했습니다. 임시 URL은 완료 상태로 인정하지 않습니다.
- Oracle 0.17.1 manual-login profile 미초기화가 제출 전에 발생한 경우의 안전한
  잠금 정산과, `TASK_OUTCOME` 뒤의 제한된 Markdown reference footer 분류를
  회귀 테스트로 고정했습니다.

## 1.7.0 — macOS Ultrawork

- macOS arm64에서 공통 Python `install/update/doctor/rollback/uninstall` lifecycle과
  영수증/WAL/충돌 보존을 지원합니다. PowerShell 진입점은 Windows 호환 경로로
  유지합니다.
- OMO Codex Light, 로컬 CodexPro hook marketplace, GJC식 brownfield 인터뷰와
  합산 동시 실행 상한 5를 추가했습니다.
- `RUNNING → CHECKPOINT_DUE(75분) → HANDOFF_PENDING(80분)` 상태 머신과
  exact Oracle 회수, 동일 Codex session resume, launchd 감독기를 추가했습니다.
- DevSpace 1.0.4를 macOS에서 직접 실행하고 MagicDNS 자동 탐지 및 Tailscale
  Funnel `443 → 127.0.0.1:7676` 복구 경로를 추가했습니다. Funnel 엣지가
  OpenAI 연결 제한을 넘길 때 사용할 격리된 Cloudflare Named Tunnel
  LaunchAgent도 제공합니다.
- GitHub Actions는 `windows-latest`와 `macos-14`를 모두 검증합니다.

### Oracle + DevSpace 단일 실행 경로

- 일반 GPT, 계획, 검토, 수정, 지휘, 심층 리서치, 종합모드와 Web
  Multi-GPT를 Oracle + DevSpace로 통일했습니다.
- Pro는 기본적으로 Oracle + 읽기 전용 DevSpace를 사용하며, 명시적인
  `pro-attachment`만 고정 외부 증거에 사용합니다.
- CodexPro와 agbrowse 신규 제출 경로는 동결했습니다.

### Windows 브라우저 실행 격리

- 실행마다 로그인 프로필의 throwaway 복사본을 사용합니다.
- Windows에서는 Node 내장 복사로 프로필을 만들며 rsync를 요구하지 않습니다.
- 각 Oracle 실행이 소유한 숨김 Chrome만 정리합니다.

### 장기 작업과 복구

- 웹 작업은 기본 70분 이내 episode로 분할합니다.
- 75분에는 새 fan-out을 막고 80분에는 durable handoff와 정확한 owner 상태를
  평가합니다.
- CDP 호출이 멈춰도 host watchdog이 30초 grace 뒤 동일 세션을 보존한 채
  `attention_required`로 반환합니다.
- 제출 후 로컬 종료·브라우저 연결 끊김은 `attention_required`로 보존합니다.
- 복구는 저장된 정확한 slug와 대화 URL만 사용하고 새 질문을 보내지 않습니다.
- terminal 상태는 이후 관찰에서 live로 되돌아가지 않습니다.

### 종합모드

- plan → optional Pro/Web Multi → review → implementation → final web gate
  → local deterministic gate 순서를 사용합니다.
- 각 단계는 다음 미션과 workflow/stage/attempt/input-SHA 결합 영수증을
  직접 작성합니다.
- review 단계가 수정 가능한 계획 결함을 직접 고치고 구현 미션을 확정합니다.
- Pro 증거 파일은 `[PRO_ATTACHMENT_CONTRACT]`에 선언된 파일만 첨부합니다.
- 손상된 Pro JSON은 신원이 정확히 일치하는 제한된 경우에만 감사 기록과
  함께 복구합니다.

### Web Multi-GPT

- 독립 Oracle solver 2~25개를 최대 5개씩 wave로 실행합니다.
- Windows lane마다 별도 프로필을 사용합니다.
- 각 solver는 짧은 handoff 파일을 만들고 merger 하나가 안정된 순서로
  결과를 병합합니다.

### 설치와 릴리스

- 설치 전 파일을 백업하고 durable 영수증을 남깁니다.
- 기본 설치는 동결된 agbrowse/CodexPro 의존성을 설치하거나 갱신하지 않습니다.
- portability, fast gate, golden-path, v3/v4 계약 테스트를 Windows와 macOS
  CI에서 실행합니다.

## 레거시 기록

과거 CodexPro·agbrowse 기반 v1~v4 실행기와 goal supervisor는 새 작업을
만들 수 없습니다. 이미 저장된 실행을 원래 신원으로 복구할 때만 사용합니다.
자세한 목록은 [FROZEN_LEGACY.md](FROZEN_LEGACY.md)에 있습니다.

세부 커밋 단위 변경은 Git 로그와 GitHub Releases/Actions를 권위 기록으로
사용합니다.
