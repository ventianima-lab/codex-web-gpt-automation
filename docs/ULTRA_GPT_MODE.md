> Historical reference only. Do not use this execution mode for new work.
> Follow [the shared automation policy](AUTOMATION_POLICY.md). Preserve existing
> run records and their original recovery authority.

# 울트라 GPT 모드

울트라 GPT 모드는 Codex Ultra/Multi-agent의 역할 분해 방식을 Oracle 웹 GPT
세션으로 옮긴 선택형 품질 우선 워크플로입니다. 로컬 Codex는 작업자가 아니라
결정론적 관제기로 남고, 설계·독립 탐색·구현·검증 같은 의미 작업은 모두 서로
분리된 웹 세션이 맡습니다.

## 초절약모드와의 차이

| | 초절약모드 | 울트라 GPT 모드 |
|---|---|---|
| 목표 | 로컬 모델 비용 최소화 | 웹 세션 분업과 상호검증 극대화 |
| 로컬 모델 | Luna Max 선택을 최초 1회 안내 | 현재 모델을 변경하거나 검사하지 않음 |
| 네이티브 subagent | Luna Max worker 사용 가능 | 의미 작업에는 사용 금지 |
| 웹 구조 | Pro 설계 후 직렬 구현·검증 | planner + reviewer + 병렬 writer + merger + final gate |
| Pro | 모드 계약의 설계 단계 | 별도 명시 승인 시 사전 설계 자문 1회만 가능 |

## 실행 구조

```text
로컬 결정론적 관제기
  -> regular web planner
  -> 별도 web reviewer + 파일 소유권 분할
  -> 2~5개 병렬 worktree-write web implementer (동시 최대 3)
  -> all-lanes audit barrier
  -> combined canonical 결과를 검사하는 web merger
  -> 별도 web verifier
  -> local deterministic gate
```

공식 Codex Multi-agent는 같은 filesystem에서 병렬 쓰기가 가능하지만, 서로
독립된 브라우저 웹 세션에는 동일한 프로세스 수준 소유권 경계가 없습니다.
따라서 울트라 GPT writer는 병렬성을 유지하되 각각 같은 repository/HEAD의 별도
사전 생성 Git worktree에서 실행됩니다. 각 lane은 비어 있지 않은
project-relative `owned_paths`를 선언하며 서로 같은 경로나 상위·하위 경로를
소유할 수 없습니다. host는 이 범위를 mission에 주입하고 실제 delta와 Git
metadata를 검사합니다. 한 lane이라도 실패하거나 범위를 벗어나면 아무 결과도
병합하지 않습니다. 모든 lane 통과 후에만 canonical checkout에 결합합니다.
worktree는 `<output_dir>\worktrees` 아래에 만들며, v2 parent/lane hash binding을
검증한 뒤 canonical 프로젝트의 기존 DevSpace qualification을 상속합니다. 따라서
일시적인 worktree마다 `allowedRoots`를 추가하거나 서비스를 재시작하지 않습니다.

웹 앱이 안전 정책에 따라 `write/edit/bash`를 모델에 노출하지 않는 경우에도
planner/reviewer는 중단하지 않습니다. 각 단계는 닫힌 JSON envelope로
output/next mission/receipt를 반환하고, 로컬 관제기가 workflow identity와
UTF-8 바이트를 검증해 자동화 소유 stage 디렉터리에만 원자적으로 materialize합니다.
writer lane은 같은 방식으로 lane/parent/source-mission에 결속된 writeset을
반환할 수 있습니다. host는 파일 수·총 바이트·`owned_paths`·symlink/reparse
경계·Git delta를 검증한 뒤 격리 worktree에 적용하며, 직접 쓰기와 writeset을
동시에 사용하면 실패 폐쇄합니다. 쓰기 도구를 읽기 전용으로 허위 표시하지 않습니다.

일반 `read`가 50KB를 넘는 단일 행을 반환하지 못하면 읽기 전용
`read_chunk`를 사용합니다. 0 byte offset에서 시작해 반환된
`nextOffsetBytes`를 그대로 이어 쓰며 `eof=true`까지 읽습니다. 각 chunk는
24KiB 이하이고 전체 파일 SHA-256, 전체 byte 수, UTF-8 경계를 함께 검증합니다.
사전검증은 전체 DevSpace 서버 모듈을 import하지 않고, 해시로 게이트된
설치본의 정확한 `readUtf8Chunk` 함수 본문만 최소 Node 프로세스에서
실행합니다. 이로써 무관한 MCP dependency graph 로딩이 version resolution을
막지 않습니다.

## Manifest

```json
{
  "schema": "codex.chatgpt.oracle-comprehensive/v1",
  "workflow_id": "stable-hex-or-uuid",
  "workflow_profile": "ultra-gpt",
  "initial_stage": "plan",
  "allow_pro": false,
  "project_root": "D:\\project",
  "workflow_dir": "D:\\project\\.workflow\\ultra-gpt",
  "initial_mission_path": "D:\\project\\missions\\ultra-gpt-plan.md",
  "app_name": "codex",
  "model": "gpt-5.6",
  "max_stages": 8,
  "local_gate_command": ["python", "-m", "pytest", "-q"]
}
```

먼저 dry-run합니다.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_comprehensive.py" `
  --manifest D:\project\ultra-gpt-workflow.json --dry-run
```

## 선택형 폐쇄 감사

폐쇄 감사는 별도 실행 모드가 아닙니다. 동일한 `ultra-gpt` 실행에 재현성과
책임 추적이 필요한 경우에만 다음 계약을 명시적으로 추가합니다.

```json
{
  "workflow_profile": "ultra-gpt",
  "closed_audit": {
    "contract_path": "D:\\project\\audit\\contract.json",
    "contract_sha256": "<64 lowercase hex>"
  }
}
```

이 옵션은 기존 planner/reviewer/writer wave/merger/final gate를 그대로 사용하면서
SHA-256으로 결속된 dependency·authority manifest, advisory-only Research
Governor, identity ledger, 실제 결속 파일을 연 local-gate receipt, 최종 workflow
audit를 추가합니다. 일반 `ultra-gpt`에는 자동으로 적용되지 않습니다.

과거 `workflow_profile: "strict-ultra"` 입력과
`codex.chatgpt.strict-ultra-*/v1` 산출물 스키마는 정확한 기존 실행의 복구와
재검증을 위해 계속 허용합니다. 신규 workflow에서는 사용하지 않습니다. 세부
감사 산출물 계약은 [Ultra GPT 폐쇄 감사 호환 문서](STRICT_ULTRA.md)를 참고하세요.

`ultra-gpt` 실행기는 다음 조건을 제출 전에 실패 폐쇄합니다.

- `initial_stage`가 `plan`이 아님
- 내부 `allow_pro=true`
- `max_stages`가 5 미만
- planner가 `review` 외 단계로 전환
- reviewer의 `PASS`/`PASS_WITH_NOTES`가 `web-multi` 외 단계로 전환
- reviewer의 유효한 `FAIL` receipt가 아닌데 `ready_for_next=false`,
  `next_stage=null`로 workflow를 종결하려 함
- solver 수가 2~5 범위를 벗어남
- 동시 실행 수가 3을 초과함
- Multi schema가 `codex.chatgpt.oracle-multi/v2`가 아님
- solver가 `worktree-write`가 아니거나 `owned_paths`가 비어 있음
- writer root가 사전 생성된 동일 repository/HEAD worktree가 아님
- 두 solver의 소유 경로가 같거나 상위·하위로 겹침
- 실제 변경이 선언 범위를 벗어나거나 Git metadata가 변경됨
- 일부 lane만 성공한 상태에서 merger를 요청함
- host writeset identity/scope/key set/byte limit이 불일치함
- 직접 workspace delta와 host writeset이 동시에 존재함

Pro 설계 자문이 필요하면 사용자의 별도 명시 승인을 받은 한 세션을 워크플로
전에 실행하고, 그 결과를 initial mission의 고정 입력으로 넣습니다. Pro 세션은
울트라 GPT workflow identity나 복구 체인에 섞지 않습니다.

완료는 final web PASS receipt와 local gate exit code 0이 모두 있어야 합니다.
review가 hash-bound `FAIL` receipt를 반환하면 구현 lane을 만들지 않고 workflow를
`BLOCKED / REVIEW_FAILED`로 종결해 scope를 해제합니다. `FAIL`은
`ready_for_next=false`, `next_stage=null`, 비어 있지 않은 blocker와 유효한
critical finding 결속을 모두 만족해야 하며, PASS 계열만 `web-multi`로 갑니다.
제출 여부가 불명확한 세션은 같은 exact slug만 복구하며 새 세션으로 대체하지
않습니다. 80분은 상태 점검 시점이지 종료 시간이 아닙니다.

사용자가 provider UI에서 응답을 직접 중지한 뒤 해당 workflow 전체를 그만두라고
명시한 경우에는 JSON을 수동 수정하지 않습니다. 유지보수자는 설치된 comprehensive
runner의 `--cancel-user-stopped`를 dry-run한 뒤 실행하며, exact workflow/scope/run
state의 사전 SHA-256과 `user-confirmed-provider-stop` 확인 토큰을 모두 제공해야
합니다. Oracle run이 terminal-harvested가 아니거나 결속이 하나라도 바뀌면 정산과
scope 해제가 모두 실패 폐쇄됩니다.
