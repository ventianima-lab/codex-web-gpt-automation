# 초절약모드

초절약모드는 로컬 Codex 비용을 최소화하면서 설계·구현·검토 품질을 웹
ChatGPT 단계에 맡기는 선택형 종합 워크플로입니다. 일반 전역 설정을 바꾸지
않으며, 사용자가 해당 작업의 모델을 직접 `gpt-5.6-luna` / `max`로 선택한
뒤 최초 1회 확인한 경우에만 시작합니다.

## 활성화

새 작업에서 다음처럼 요청합니다.

```text
$ultra-economy-mode 초절약모드로 이 작업을 끝내줘.
```

스킬은 현재 선택 상태를 읽거나 추론하지 않습니다. 대신 초절약모드의 **최초
요청에만** `GPT-5.6 Luna`와 추론 강도 `Max`를 선택하고 완료를 알려 달라고
항상 한 번 안내합니다. 이미 선택했다고 말했더라도 최초 안내는 한 번 합니다.

사용자가 완료를 확인하면 같은 Codex 작업에서는 활성화가 끝난 것으로 처리해
중간 단계, 후속 요청, 복구, 컨텍스트 압축 뒤에도 다시 묻거나 모델 상태를
재검사하지 않습니다. 새 Codex 작업에서 초절약모드를 처음 요청할 때만 다시 한
번 안내합니다. `~/.codex/config.toml`을 자동 변경하지도 않습니다.

이 활성화 확인은 Pro 사용 승인이 아닙니다. 초절약모드의 첫 읽기 전용 Pro 설계
자문을 쓰려면 사용자가 별도로 Pro 사용을 명시해야 합니다. 승인이 없으면
`ultra-economy` 실행을 시작하지 않고 일반 non-Pro 종합 모드를 제안합니다.

## 작업 분담

```text
로컬 Luna Max 지휘관
  ├─ exact-root 최초 자격 확인
  ├─ 최소 미션·영수증·해시·상태 관리
  └─ 최종 결정론적 명령 1회

웹 세션
  ├─ Pro: 별도 명시 승인된 읽기 전용 설계
  ├─ regular: 별도 설계 검토 + 구현 미션 작성
  ├─ regular: 코드 구현 + 프로젝트 테스트
  └─ regular: 별도 최종 검증, 필요하면 다음 구현 세션으로 수리 반송
```

로컬에서 의미 판단이 꼭 필요하면 지휘관이 직접 긴 맥락을 읽지 않고, 새
`default` 서브에이전트를 `gpt-5.6-luna` / `max`로 한 명씩 실행합니다. 전달
내용은 목표, exact 파일 경로, 현재 영수증, 권한, 성공 조건으로 제한합니다.
전역 scout/implementer/verifier 역할은 다른 모델 계약일 수 있으므로 이 모드에서
사용하지 않습니다.

## Manifest

기존 Oracle comprehensive manifest에 다음 필드를 추가합니다.

```json
{
  "schema": "codex.chatgpt.oracle-comprehensive/v1",
  "workflow_id": "stable-hex-or-uuid",
  "workflow_profile": "ultra-economy",
  "initial_stage": "pro",
  "allow_pro": true,
  "project_root": "D:\\project",
  "workflow_dir": "D:\\project\\.workflow\\ultra-economy",
  "initial_mission_path": "D:\\project\\missions\\design.md",
  "app_name": "codex",
  "model": "gpt-5.6",
  "max_stages": 8,
  "local_gate_command": ["python", "-m", "pytest", "-q"]
}
```

로컬 지휘관은 대화 안에서 최초 1회 안내·확인 상태를 유지합니다. 실행기는
후속 단계마다 `CODEX_THREAD_ID`, rollout, 화면, 설정 파일에서 모델과 추론
수준을 다시 읽지 않습니다. manifest 자기선언도 확인을 대신하지 않습니다.

먼저 dry-run을 실행합니다.

```powershell
python "$env:USERPROFILE\.codex\bin\chatgpt_oracle_comprehensive.py" `
  --manifest D:\project\workflow.json --dry-run
```

dry-run은 별도 Pro 승인에 결속된 `allow_pro: true`와 qualified Pro의 읽기 전용
DevSpace 설계·자문·검토 단계인지 확인하고 실제 제출은 하지 않습니다. 초절약모드
선택 자체를 Pro 승인으로 간주하지 않습니다. 파일 생성·수정·삭제와 명령 실행은
뒤따르는 regular `GPT-5.6` `extra-high` DevSpace 단계가 맡습니다. 실제 실행은
`--dry-run`만 제거합니다.

## 완료·중단 조건

- 새 프로젝트의 exact root 자격 확인은 첫 질문 전에 한 번 수행하며, DevSpace
  config hash가 같으면 후속 단계마다 반복하지 않습니다.
- Pro는 설계 전용입니다. 구현과 프로젝트 테스트는 별도 regular 웹 세션이
  수행하고, 또 다른 regular 웹 세션이 최종 검증합니다.
- 불명확한 제출 실패는 exact-session 복구만 허용합니다. 새 제출로 대체하지
  않습니다.
- 최종 웹 PASS 영수증과 로컬 결정론적 gate의 exit code 0이 모두 있어야
  완료입니다. 로컬 Luna의 주관적 판단만으로 완료하지 않습니다.
