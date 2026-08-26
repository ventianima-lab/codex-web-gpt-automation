# 동결 자산 경계 (frozen legacy)

이 저장소는 신규 ChatGPT 작업을 **Oracle 하나로만** 실행합니다. 아래
자산은 삭제하지 않았지만 새 제출에는 사용하지 않습니다. 이미 저장된
예전 실행을 그 실행의 원래 신원 그대로 관찰·복구·정리할 때만 남아
있습니다.

## 원칙

- 신규 제출 경로는 Oracle뿐입니다. 일반·계획·검토·수정·지휘·심층
  리서치·종합모드·Web Multi는 Oracle + 수동 등록 DevSpace 앱을 쓰고,
  명시적으로 요청된 신규 Pro도 같은 수동 등록 DevSpace 앱을 `pro-devspace-readonly`로
  사용하지만 설계·자문·검토 전용 읽기 권한만 가집니다. 파일 생성·수정·삭제와
  명령 실행은 최고 지원 비-Pro `GPT-5.6` `extra-high` regular DevSpace 단계가
  수행합니다. 명시적 `pro-attachment`는 불변·외부 증거를 위한 별도 읽기 전용
  경로이며 자동 fallback이 아닙니다. 저장된 legacy `pro-devspace` 쓰기 실행은
  정확한 복구에서만 원래 transport와 권한을 유지합니다.
- Oracle 실패는 다른 백엔드로 전환할 권한을 만들지 않습니다.
  agbrowse·CodexPro·in-app Browser·`@chrome`·Playwright/CDP·Proxima는
  fallback이 아닙니다.
- 동결 자산은 새 실행을 만들 수 없습니다. 새 전송을 시도하면 브라우저를
  건드리기 전에 거부됩니다.
- 예전 실행의 복구에서는 정확한 대화 URL과 그 실행이 소유한 프롬프트
  파일명이 신원입니다. 웹에서 확인된 terminal 증거가 PID·heartbeat·lock·
  로컬 poll 진단보다 우선합니다.
- 불확실한 예전 실행은 대체 제출을 허용하지 않습니다.

## 동결된 런타임

| 경로 | 상태 |
|---|---|
| `bin/chatgpt_agbrowse_bridge.py` | 복구 전용 |
| `bin/chatgpt_agbrowse_composer.py` | 복구 전용 |
| `bin/chatgpt_agbrowse_contract.py` | 복구 전용 |
| `bin/chatgpt_agbrowse_run.py` | 복구 전용 |
| `bin/chatgpt_agbrowse_state.py` | 복구 전용 |
| `bin/chatgpt_agbrowse_tabs.py` | 복구 전용 |
| `bin/codexpro_agbrowse_app.py` | 복구 전용 |
| `bin/codexpro_exact_unit_authority.py` | 복구 전용 |
| `bin/codexpro_exact_unit_cloudflare_bootstrap.ps1` | 복구 전용 |
| `bin/codexpro_fixed_runtime_watchdog.py` | 복구 전용 |
| `bin/codexpro_mcp_identity.py` | 복구 전용 |
| `bin/codexpro_project_app_manager.py` | 복구 전용 |
| `bin/codexpro_project_cloudflare_bootstrap.ps1` | 복구 전용 |
| `bin/chatgpt_web_multi_runtime.py` | 복구 전용 (신규 Web Multi는 `chatgpt_oracle_multi.py`) |
| `bin/chatgpt_web_multi_upstream.py` | 복구 전용 |
| `bin/chatgpt_parallel_implementation_runtime.py` | 복구 전용 |
| `bin/chatgpt_git_isolation.py` | 복구 전용 (v3 병렬 구현 host Git 격리) |
| `bin/chatgpt_goal_contract.py` | 복구 전용 (v4 goal 사이클 계약) |
| `bin/chatgpt_goal_supervisor.py` | 복구 전용 (v4 goal 사이클 감독기) |

`bin/codexpro_windows_process_identity.py`, `bin/chatgpt_prompt_profiles.py`,
`bin/mcp_resource_guard.py`는 동결 대상이 아닙니다. 각각 Windows 숨김 창
규칙, 공용 프롬프트 프로필 정의, 부모가 사라진 헬퍼 정리에 쓰입니다.
마지막 항목은 실행을 막는 preflight로 쓰지 않습니다.

## 현행 런타임

| 경로 | 역할 |
|---|---|
| `bin/chatgpt_oracle_dispatch.py` | 모드별 Oracle manifest 생성·제출 |
| `bin/chatgpt_oracle_run.py` | 실행·정확 slug 복구·회수 |
| `bin/chatgpt_oracle_state.py` | 프로젝트 잠금·신원·상태 장부 |
| `bin/chatgpt_oracle_comprehensive.py` | 종합모드 단계 실행기 |
| `bin/chatgpt_oracle_multi.py` | 진짜 Web Multi-GPT wave 실행 |
| `bin/chatgpt_oracle_compat.py` | 기본 Oracle 0.18.0 해시 검증과 Oracle 0.17.1 롤백 LKG/과거 실행 복구 계약 |
| `bin/chatgpt_oracle_profiles.py` | lane별 throwaway 프로필 |
| `bin/chatgpt_oracle_diagnose.py` | 실패 서명 분류 |
| `bin/chatgpt_oracle_incident.py` | 단일 수리 소유자 인계 패킷 |
| `bin/chatgpt_devspace_compat.py` | DevSpace 1.0.8 current 호환 패치와 1.0.7 롤백 LKG |

## 레거시 스텁 문서

`ARCHITECTURE_V2.md`, `ARCHITECTURE_V3.md`, `ARCHITECTURE_V4.md`,
`ARCHITECTURE_GOAL_SUPERVISOR_V1.md`,
`codexpro-gpt55-orchestrator-runbook.md`,
`gpt55-operation-mode-prompts.md`, `DCINSIDE_POST_KO.md`는 예전 링크가
깨지지 않도록 남긴 스텁입니다. 각 파일은 현행 라우팅으로 안내만 하며
새 작업의 명령 출처가 아닙니다.

## 테스트

동결 자산의 테스트는 회귀 보호 목적으로 계속 실행됩니다. 특히
`tests/test_legacy_new_submission_freeze.py`는 동결 경로가 새 제출을
만들지 못한다는 계약을 고정합니다.
