# 예외 승객 처리: LLM 제안 + 결정적 검증기

> 상태: **P1~P4 구현 완료** (검증기, 에이전트 루프 연동, shadow 기록, assist 모드 승인, LLMOps)

## 배경

cuOpt가 자동 배정하지 못한 승객(`MANUAL_REVIEW` / `NO_FEASIBLE`)은 지금도 `explore_exception_options`로 분석되지만,
결과는 브리핑 안의 자유 텍스트 권고로만 나갑니다. 운영자가 할 수 있는 선택은 "solver가 정한 편을 승인할지"뿐입니다.

이 설계는 그 권고를 **검증 가능한 구조화 제안**으로 바꿉니다. 자동 배정 31명은 건드리지 않으며,
"LLM은 배정을 결정하지 않는다"는 원칙도 그대로 유지합니다. LLM은 코드가 열거한 선택지 안에서만 고르고,
최종 판정은 solver와 같은 제약 코드를 쓰는 검증기가 내립니다.

## 원칙

| 원칙 | 의미 |
|---|---|
| 닫힌 선택지 | 편과 좌석은 검색된 대체편 중에서만 고릅니다. 편명을 새로 만들면 `UNKNOWN_FLIGHT`로 거부됩니다 |
| 검증기가 최종 판정 | 통과한 제안만 운영자에게 "권고"로 표시됩니다 |
| 자동배정 불가침 | 예외 승객만 대상입니다. 그 외 승객에 대한 제안은 `TARGET`으로 거부됩니다 |
| 사람 승인 후 실행 | 정책 예외(waiver)는 duty manager 역할만 승인할 수 있습니다 |

## 흐름

```mermaid
flowchart LR
    A[optimize_rebooking<br/>cuOpt] --> B{예외 승객?}
    B -- 없음 --> P[propose_rebooking]
    B -- 있음 --> C[explore_exception_options<br/>코드가 옵션 열거]
    C --> D[propose_exception_resolution<br/>LLM이 액션 선택]
    D --> E[verify_proposals<br/>결정적 검증]
    E -- 위반 --> D2[위반 사유를 LLM에 반환<br/>1회 재시도]
    D2 --> E
    E -- 재실패 --> F[권고 없음<br/>운영자 직접 판단]
    E -- 통과 --> P
    F --> P
    P --> G{운영자: 수락/수정/거절}
    G --> H[재검증 → 실행 토큰 → Booking API]
```

## 액션 (닫힌 집합) — `app/resolution/models.py`

| 액션 | 대상 | 좌석 | 비고 |
|---|---|---|---|
| `CONFIRM_SOLVER_ASSIGNMENT` | MANUAL_REVIEW | solver 좌석 유지 | 체크리스트 필수 (예: WCHC 지상조업 재확인) |
| `REASSIGN_TO_OPTION` | 예외 승객 | 다른 대체편 | 정책상 feasible한 편만 |
| `REQUEST_POLICY_WAIVER` | 예외 승객 | 정책 차단 편 | 면제 가능한 차단만 있어야 함, 해당 정책 ID 인용 필수, duty manager 승인 |
| `OFFER_REFUND` | 예외 승객 | 없음 | 환불/duty of care 후속 태스크 |
| `REROUTE_OFFLINE` | 예외 승객 | 없음 | 시스템 밖 처리 (타사, 육로 등) |

LLM의 자기평가 confidence 필드는 일부러 두지 않았습니다. 품질 신호는 검증 결과와 운영자 수락률로 봅니다.

## 공용 제약 함수 — `app/optimization/constraints.py`

제약 판정이 `formulation.py`와 `tools/exceptions.py` 두 곳에 흩어져 있던 것을 한 곳으로 모았습니다.
MILP formulation, 예외 분석 툴, 검증기가 모두 이 모듈을 씁니다. 그래서 "모델에게 feasible하다고 보여준 옵션을
검증기가 거부"하거나 그 반대가 되는 일은 구조적으로 생기지 않습니다(`test_verifier_agrees_with_the_options_shown_to_the_model`로 고정).

각 차단(`Block`)은 자신을 만든 정책 규칙을 기록합니다. **면제 가능 여부는 규칙으로 판정**합니다.

| 차단 | 규칙 | 면제 |
|---|---|---|
| C5 co-terminal 공항 | `coterminal` | 가능 |
| C6 interline 협정 없음 | `interline` | 가능 |
| C6 재보호 시간창 초과 | `max_delay` | 가능 |
| C6 편 자체가 비운항/지연 | 운영 사실 | **불가** |
| C6 SSR(WCHC/UMNR/MEDA) 자사편 한정 | `ssr` | **불가** (안전) |
| C4 MCT 미달 / 공항 변경 | `mct` / 물리 제약 | **불가** |
| C2 좌석 부족 | 물리 제약 | **불가** |

기존 코드는 제약 코드가 C5/C6인지로 면제 가능 여부를 판단했습니다. 그래서 "편 자체가 지연됨" 같은 운영 사실도
정책 차단처럼 보일 수 있었는데, 이번에 규칙 기반 판정으로 바로잡았습니다. KE123 데모의 배정 결과와 목적함수는 리팩터링 전후가 동일합니다.

## 검증기 — `app/resolution/verifier.py`

LLM 호출과 I/O가 없는 순수 함수입니다. `verify_proposals(proposals, req, assignments, known_policy_ids)`

| 코드 | 검사 |
|---|---|
| `TARGET` | 예외 승객인가, 확인할 solver 좌석이 있는가 |
| `DUPLICATE` | 같은 승객에 제안이 둘 이상인가 |
| `PARAMS` / `RATIONALE` / `CHECKLIST` | 액션에 맞는 인자인가, 운영자용 설명과 체크리스트가 있는가 |
| `UNKNOWN_FLIGHT` | 검색된 대체편인가 (편명 지어내기 차단) |
| `CABIN` | 탑승 가능한 등급인가 (이코노미 승급은 정책이 허용할 때만) |
| `CONSTRAINT` | 하드 제약 위반 (면제 가능하면 waiver 힌트 포함) |
| `NOT_WAIVABLE` / `NO_WAIVER_NEEDED` | 면제 불가 제약을 면제하려 하거나, 면제가 필요 없는 편인가 |
| `CITATION` | 검색되지 않은 정책 인용, 또는 면제 대상 정책을 인용하지 않은 waiver |
| `CAPACITY` | **모든 제안을 함께 적용했을 때** 좌석 초과 |

**좌석 검사는 합산으로 합니다.** `explore_exception_options`는 승객을 한 명씩 분석하므로, 두 제안이 각자 보기엔 문제없는데
같은 마지막 좌석을 가져갈 수 있습니다. 검증기는 solver 계획 위에 유효한 제안을 모두 적용합니다(원래 좌석 반납 + 새 좌석 점유).
초과가 생기면 해당 좌석을 새로 점유하려던 제안을 거부하고, 안정될 때까지 반복합니다. 거부된 제안이 반납하려던 좌석은
반납되지 않은 것으로 다시 계산되므로, "P011이 KE701을 비우면 P013이 그 자리로" 같은 연쇄 제안도 앞 제안이 거부되면
뒤 제안이 함께 거부됩니다.

제안 집합이 바뀌면(운영자 수정, 부분 승인) 검증기를 **반드시 다시 실행**합니다. 이전 판정은 재사용하지 않습니다.

## KE123 데모에서의 모습

| 승객 | solver 결과 | 기대 권고 |
|---|---|---|
| P010 | NO_FEASIBLE (SQ637 환승) | `REQUEST_POLICY_WAIVER` 7C1102 [IROP-002] → duty manager, 거부 시 `OFFER_REFUND` |
| P011 | MANUAL_REVIEW, KE701 (환승 여유 빠듯) | `CONFIRM_SOLVER_ASSIGNMENT` + 환승 리스크 체크리스트 |
| P013 | MANUAL_REVIEW, KE703 (WCHC) | `CONFIRM_SOLVER_ASSIGNMENT` + 휠체어 지상조업 체크리스트 |
| P014 | MANUAL_REVIEW, KE703 (UMNR) | `CONFIRM_SOLVER_ASSIGNMENT` + 비동반 소아 인계 체크리스트 |

## 단계별 계획

| 단계 | 내용 | 상태 |
|---|---|---|
| P1 | 공용 제약 함수, 검증기, 테스트 (LLM 동작 변화 없음) | ✅ 완료 |
| P2 | `propose_exception_resolution` 툴, mock 규칙 기반 proposer(데모 + eval baseline), `ExceptionResolution` 저장. **shadow 모드**: 기록만 하고 UI에는 표시하지 않음 | ✅ 완료 |
| P3 | **assist 모드**: 승인 API에 `exception_decisions`(ACCEPT/MODIFY/REJECT) 추가, control plane 재검증, duty manager 확인, waiver 감사 기록, 승인 화면 카드, MCP 노출 | ✅ 완료 |
| P4 | LLMOps: 예외 단위 트레이싱과 OTLP export, 골든셋, CI(mock)·nightly(Nemotron) eval 게이트 | ✅ 완료 |

자동 실행은 단계 계획에 없습니다. 상태를 바꾸는 작업은 계속 사람이 승인합니다.

## P2 구현 내용 (shadow 모드)

| 구성 요소 | 위치 | 내용 |
|---|---|---|
| 제안 툴 | `app/tools/resolution.py` | 승객당 1개 제안을 받아, 이미 통과한 다른 승객들의 제안과 **함께** 검증합니다. 거부되면 위반 사유를 돌려주고 1회 수정 기회를 줍니다(승객당 최대 2회). 이미 다른 승객에게 권고된 좌석을 뺏는 제안도 거부합니다 |
| 규칙 기반 제안기 | `app/resolution/baseline.py` | `explore_exception_options` 결과만 보고 제안합니다. mock 플래너(데모)와 eval baseline이 같이 씁니다 |
| 저장 | `exception_resolutions` 테이블 | 모든 시도를 저장합니다(최종 채택 여부 `final` 포함). 플래너(`provider/model`)와 **프롬프트 버전**(시스템 프롬프트 내용 해시)을 함께 기록합니다. `create_plan` 경로로 저장하므로 DB가 없는 sandbox worker에서도 동작합니다 |
| 조회 | `GET /api/rebooking/plans/{id}/exception-resolutions` | 시도 목록과 지표(coverage, 1차 통과율, 위반 코드 분포, 액션, waiver 요청) |
| 평가 | `make eval-llm` | KE123 시나리오에 "예외 승객 전원 권고" 체크가 추가되고, 1차 통과율과 baseline 일치율을 출력합니다 |
| 설정 | `EXCEPTION_RESOLUTION_MODE` | `shadow`(기본) 또는 `off`. `off`면 툴이 등록되지 않고 프롬프트에서도 빠집니다 |

shadow 모드에서는 권고가 에이전트 타임라인과 위 조회 API에만 남습니다. 브리핑, 승인 화면, 승인·실행 흐름은 `off`일 때와 **동일**합니다(`test_shadow_mode_does_not_change_what_the_operator_sees_or_approves`로 고정).

KE123 mock 실행 결과: 4/4 권고, 1차 통과율 100%. P010은 7C1102 waiver(duty manager), P011·P013·P014는 체크리스트가 붙은 확인입니다.

## P3 구현 내용 (assist 모드)

`EXCEPTION_RESOLUTION_MODE=assist`로 켭니다. 운영자는 승인 화면의 예외 권고 카드에서 승객별로 수락/거절을 고르고, 그 결정이 계획 승인과 함께 전송됩니다.

```mermaid
sequenceDiagram
    participant A as Agent (worker 가능)
    participant CP as Control plane
    participant AL as Airline API
    participant O as 운영자
    A->>CP: create_plan (+ 권고와 에이전트 측 판정)
    O->>CP: GET exception-resolutions
    CP->>AL: 항공편·승객·대체편 (최신 재고)
    CP-->>O: 권고 + control plane 재검증 판정
    O->>CP: approve (exception_decisions)
    CP->>AL: 최신 재고로 다시 조회
    CP->>CP: 결정 전체를 함께 검증 + duty manager 확인
    CP-->>O: 422/403 (아무것도 승인 안 됨) 또는 승인
    A->>AL: 실행 토큰으로 예약 변경 (재고는 예약 API가 최종 확인)
```

| 항목 | 동작 |
|---|---|
| 재검증 위치 | **control plane**(`app/resolution/review.py`). worker가 보낸 판정은 참고용이며, 권고를 보여줄 때와 승인할 때 항공 API에서 재고를 새로 읽어 다시 검증합니다 |
| 결정 | `ACCEPT`(권고 그대로) · `MODIFY`(운영자 자신의 제안, 같은 검증기를 통과해야 함) · `REJECT`(이번 계획에서 조치 없음) |
| 원자성 | 결정 중 하나라도 검증에 실패하면 **아무것도 승인되지 않습니다**(422, 승객별 위반 사유) |
| waiver 권한 | `DUTY_MANAGERS`에 있는 operator만 승인할 수 있습니다(403 `WAIVER_AUTHORITY`). 승인하면 감사 로그에 `policy.waiver`로 남습니다 |
| 체크박스와의 관계 | 승객에 대한 결정이 있으면 기존 manual-review 체크박스보다 **결정이 우선**합니다 |
| 실행 | 수락된 CONFIRM은 solver 좌석으로, REASSIGN/WAIVER는 새 좌석으로 실행 토큰에 포함됩니다. 승인 이후 좌석이 사라지면 예약 API가 거부하고 `EXECUTION_FAILED`로 남습니다 |
| 최종 리포트 | 편별 집계에 waiver 좌석이 포함되고, 환불/외부 처리는 후속 조치로 나옵니다 |
| 지표 | `operator_decisions`(수락/수정/거절 수)가 조회 API 지표에 추가됩니다 |
| 외부 에이전트 | MCP에 `propose_exception_resolution`이 추가되어 OpenClaw의 권고도 같은 검증기를 거칩니다 |

KE123을 대시보드에서 끝까지 실행했을 때: 4건 모두 수락한 뒤 일반 operator로 승인하면 403으로 거부되고 계획은 PENDING으로 남습니다. `dm.park`로 승인하면 **35명 전원이 재배정**되고, P010은 7C1102로 실행됩니다.

## LLMOps (P4)

### 트레이싱 — `app/observability/`

| 항목 | 내용 |
|---|---|
| LLM 호출 기록 | 플래너 턴마다 모델, 입력·출력 토큰, 지연시간을 기록합니다. 텍스트가 있는 턴은 PLANNER 이벤트에, 툴만 호출한 턴은 첫 TOOL_CALL 이벤트에 붙여서 **타임라인에 행이 늘지 않습니다** |
| 트레이스 | `GET /api/agent/tasks/{id}/trace`. 이미 저장된 이벤트·권고 기록·승인에서 사후에 만들기 때문에 in-process, sandbox worker, 외부 MCP 플래너 모두 똑같이 동작합니다 |
| 구조 | `recovery_task` 아래에 LLM 호출, 툴 호출, 예외 승객마다 `exception P010` 스팬. 예외 스팬 안에 분석 → 제안(검증 판정 포함) → 운영자 결정이 들어갑니다 |
| 속성 이름 | OpenTelemetry GenAI 규약(`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.tool.name`)과 `reroute.*` |
| OTLP export | `OTEL_EXPORTER_OTLP_ENDPOINT`를 설정하면 작업이 끝날 때(COMPLETED/REJECTED/FAILED) 운영자 결정까지 포함한 트레이스를 한 번 보냅니다. Langfuse, Arize Phoenix, Jaeger, Grafana Tempo 등 OTLP를 받는 곳이면 됩니다. export가 실패해도 작업에는 영향이 없습니다 |

### 골든셋과 평가 — `data/evals/exception_golden.yaml`, `app/evals/exceptions.py`

- **케이스**: seed 위에 선언적 변경(매진, SSR 추가, 환승 시간 이동, 편 결항, 좌석 증감)을 얹은 5개 케이스, 예외 승객 20명. 승객마다 `acceptable`(관제사가 승인할 액션), `preferred`(먼저 고를 액션), `flights`(맞는 편), `why`(근거)를 사람이 라벨링했습니다.
- **drift 검사**: 채점 전에 라벨이 현재 solver·검증기와 맞는지 확인합니다. 예외 승객 집합이 달라졌거나, 라벨의 편이 더 이상 가능하지도 면제 가능하지도 않으면 채점하지 않고 **drift로 실패**합니다. 데이터가 바뀌었는데 라벨이 조용히 모델을 벌하거나 봐주는 일을 막습니다.
- **실행**: `make eval-exceptions` (`REPEATS=3`이면 일관성까지). assist 모드로 끝까지 실행하고, control plane 재검증 결과도 같이 확인합니다.

| 게이트 (`config/eval_gates.yaml`) | 기준 | 의미 |
|---|---|---|
| `golden_set_drift` | 0 | 라벨이 현재 데이터와 맞음 |
| `verifier_bypass` | 0 | 거부된 제안이 운영자에게 권고로 보인 적 없음 |
| `coverage` | 1.0 | 예외 승객 전원 권고 |
| `first_pass_accept_rate` | ≥ 0.9 | 재시도 없이 검증 통과 |
| `action_accuracy` | ≥ 0.9 | 최종 액션(과 편)이 라벨 안에 있음 |
| `citation_precision` | ≥ 0.95 | 인용한 정책이 그 승객의 분석에 실제로 등장 |
| `consistency` | ≥ 0.8 | 반복 실행해도 같은 액션·편 |

함께 보고되는 값은 게이트가 아닙니다: 규칙 기반 baseline의 정확도, preferred 일치율, 케이스당 LLM 호출·토큰·지연.

규칙 기반 baseline은 현재 골든셋에서 정확도 1.0이라서 "baseline 이상"은 게이트로 쓰지 않았습니다. 이 값은 비교용으로만 보고합니다. 골든셋에 규칙으로 풀리지 않는 케이스가 늘어나면 그때 게이트로 올리는 게 맞습니다.

**게이트가 실제로 잡는지** 테스트로 고정했습니다(`tests/test_evals.py`). 전원 환불하는 플래너는 모든 제안이 검증을 통과해도 정확도 0.25로 실패하고, 편을 지어내는 플래너는 1차 통과율 0으로 실패합니다. 매번 다른 답을 내는 플래너는 일관성 0.75로, 낡은 라벨은 drift로 실패합니다.

### CI

| 언제 | 무엇 |
|---|---|
| 모든 push/PR (`ci.yml`) | pytest(게이트 테스트 포함) + scripted 플래너로 골든셋 평가, 리포트 artifact |
| 매일 03:17 KST (`eval-nightly.yml`) | Nemotron으로 에이전트 시나리오와 골든셋(3회 반복) 평가, 리포트 artifact. `NVIDIA_API_KEY` secret이 없으면 건너뜀 |

### 아직 남은 것

- **온라인 지표 대시보드**: 운영자 수락/수정/거절률은 API 지표(`operator_decisions`)와 트레이스에 있지만, 프롬프트 버전별로 모아 보는 화면은 없습니다. OTLP 백엔드의 대시보드를 쓰는 편이 낫습니다.
- **운영자 수정 → 골든셋**: MODIFY 결정을 골든셋 후보로 뽑아내는 도구는 아직 없습니다. 라벨은 사람이 검토해야 하므로 자동 추가는 하지 않는 게 맞습니다.
- **골든셋 규모**: 지금은 5케이스·20명입니다. 좌석 경쟁(여러 예외 승객이 같은 면제 좌석을 두고 경쟁)과 비즈니스 다운그레이드 판단 케이스를 우선 늘리는 게 좋습니다.
