# ReRoute 아키텍처

ReRoute는 **추론**(Nemotron), **근거**(NeMo Retriever), **결정**(cuOpt), **기술적 통제**(OpenShell),
**업무적 승인**(사람의 승인)을 명시적인 인터페이스를 가진 별도 구성요소로 분리합니다. 각각이 LLM 에이전트의 서로 다른 실패 모드를 막습니다.

| 실패 모드 | 막는 방법 |
|---|---|
| 지어낸 항공사 규정 | 규정은 `search_rebooking_policy` 도구로만 들어옵니다. 검색된 `policy-params`가 제약조건으로 컴파일되고, 누락된 규정은 추측하지 않고 보고합니다 |
| LLM이 누가 어느 편을 탈지 결정 | 배정은 cuOpt가 푸는 MILP입니다. LLM은 요약만 받고 재배정안에 쓸 수 없습니다 |
| 과도한 권한을 가진 에이전트 | OpenShell 샌드박스: 기본 차단 egress, L7 규칙, 승인 엔드포인트 `deny_rules`, 파일시스템 허용 목록, non-root |
| 승인 없는 상태 변경 | 승인 게이트웨이 + 서명된 1회용 토큰(승인된 승객 목록에 고정)을 Booking API가 독립 검증 |
| 보이지 않는 행동 | 모든 상태 변경, 도구 호출, 정책 판정, 승인, 예약 쓰기가 이벤트 로그 / 감사 로그에 남습니다 |

다이어그램 목록: [1. 시스템 구성](#1-시스템-구성) · [2. 에이전트 상태 머신](#2-에이전트-워크플로-상태-머신) ·
[3. 시퀀스](#3-시퀀스-ke123-결항) · [4. 보안 경계](#4-보안-경계) · [5. 승인 흐름](#5-사람-승인-흐름) ·
[6. 최적화 흐름](#6-최적화-흐름) · [7. AWS 배포 구성](#7-aws-배포-구성)

## 1. 시스템 구성

```mermaid
flowchart TB
    op([운영 통제 담당자]) --> web["Next.js 운영 대시보드"]
    web -- "REST + SSE (/api/*)" --> cp

    subgraph cp["ReRoute 컨트롤 플레인 (FastAPI)"]
        agentapi["Agent API<br/>작업 생성 · 이벤트 SSE"]
        gw["승인 게이트웨이<br/>PENDING/APPROVED/REJECTED/EXPIRED"]
        audit["감사 로그"]
        ks["지식 서비스<br/>규정 RAG"]
        os_["최적화 서비스<br/>MILP 정식화"]
        internal["내부 worker API<br/>/internal/agent/*"]
    end

    subgraph sandbox["NVIDIA OpenShell 샌드박스"]
        agent["ReRoute 에이전트 런타임<br/>오케스트레이터 + 도구"]
    end

    agentapi -. "inline 모드" .-> agent
    internal <-. "remote 모드: 작업 가져오기 · 이벤트 · 재배정안" .-> agent

    agent -- "chat/completions + tools" --> nim["NVIDIA NIM<br/>Nemotron"]
    agent -- "GET /api/flights/** · 토큰 포함 예약 POST" --> airline["Mock 항공사 API<br/>운항 · 탑승객 · 좌석 재고 · 예약"]
    agent -- "GET /api/policies/search" --> ks
    agent -- "POST /api/optimization/rebooking" --> os_
    ks -- "임베딩 + 재순위화" --> ret["NeMo Retriever NIM"]
    os_ -- "POST /cuopt/request · GET /cuopt/solution" --> cuopt["NVIDIA cuOpt 서버 (GPU)"]
    docs[("documents/*.md<br/>항공사 규정")] --> ks

    cp --> db[(PostgreSQL)]
    airline --> db
```

두 도메인(bounded context)은 PostgreSQL 인스턴스 하나를 쓰지만 테이블은 공유하지 않습니다.
- **항공사 도메인**(항공편, 승객, 예약, 결항 정보)은 항공사 서비스가 소유합니다.
- **에이전트 도메인**(작업, 이벤트, 재배정안, 승인, 감사)은 컨트롤 플레인이 소유합니다.
- 에이전트는 어느 쪽 테이블에도 직접 접근하지 않습니다.

## 2. 에이전트 워크플로 (상태 머신)

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    RECEIVED --> ANALYZING_DISRUPTION: get_disrupted_flight
    ANALYZING_DISRUPTION --> COMPLETED: 정상 운항 / 지연이 RBK-002 기준 미만
    ANALYZING_DISRUPTION --> FETCHING_PASSENGERS: get_affected_passengers
    ANALYZING_DISRUPTION --> FAILED: 항공편 확인 불가
    FETCHING_PASSENGERS --> SEARCHING_ALTERNATIVES: search_alternative_flights
    SEARCHING_ALTERNATIVES --> RETRIEVING_POLICIES: search_rebooking_policy (병렬 검색)
    RETRIEVING_POLICIES --> RETRIEVING_POLICIES: 가드레일 - 규정 누락
    RETRIEVING_POLICIES --> OPTIMIZING: optimize_rebooking
    OPTIMIZING --> GENERATING_PROPOSAL: propose_rebooking
    GENERATING_PROPOSAL --> WAITING_APPROVAL
    WAITING_APPROVAL --> EXECUTING: 운영자 승인
    WAITING_APPROVAL --> REJECTED: 운영자 반려
    WAITING_APPROVAL --> WAITING_APPROVAL: 승인 만료 - 재승인 필요
    EXECUTING --> COMPLETED: execute_rebooking + 보고
    EXECUTING --> FAILED
    COMPLETED --> [*]
    REJECTED --> [*]
    FAILED --> [*]
```

다음 도구는 Nemotron이 고르고, 오케스트레이터는 아래 규칙을 강제합니다.
- 등록된 도구만 호출할 수 있습니다.
- 도구 인자는 Pydantic으로 검증합니다.
- 도구별 사전 조건을 확인합니다. 예를 들어 `optimize_rebooking`은 승객, 대체편, 필요한 규정이 모두 준비되어야 호출됩니다.
- 한 작업의 단계 수는 최대 14로 제한합니다.
- `execute_rebooking`은 승인 게이트웨이를 통과해야만 성공합니다.

NIM을 사용할 수 없으면 해당 작업은 결정론적 플래너로 전환되고, 그 사실이 이벤트 로그에 기록됩니다.

## 3. 시퀀스 (KE123 결항)

```mermaid
sequenceDiagram
    autonumber
    actor Op as 운영자
    participant UI as 대시보드
    participant API as 컨트롤 플레인
    participant AG as 에이전트 (샌드박스)
    participant LLM as Nemotron (NIM)
    participant AL as 항공사 API
    participant KS as 지식 서비스 (NeMo Retriever)
    participant SOLVE as 최적화 서비스 (cuOpt)
    participant GW as 승인 게이트웨이

    Op->>UI: "KE123편이 결항됐어. 최적 재배정안을 만들어줘."
    UI->>API: POST /api/agent/tasks
    API-->>UI: 202 + SSE /events
    API->>AG: run(task)
    loop 계획 → 행동 → 관찰
        AG->>LLM: 메시지 + 도구 스키마
        LLM-->>AG: tool_calls
        AG->>AL: 운항 조회 / 탑승객 조회 / 대체편 검색
        AG->>KS: search_rebooking_policy × N (병렬)
    end
    AG->>SOLVE: optimize_rebooking (핸들만 전달)
    SOLVE->>SOLVE: 검색된 policy-params → 제약조건 컴파일
    SOLVE-->>AG: 배정 결과 (기준값)
    AG->>LLM: solver 결과로 브리핑 작성 (인용 규정 검증)
    AG->>GW: propose_rebooking → 재배정안 + 승인 PENDING
    GW-->>UI: WAITING_APPROVAL (SSE)
    Op->>UI: 승인 (+ 검토 대상 승객 선택)
    UI->>GW: POST /plans/{id}/approve (X-Operator-Id)
    GW->>AG: 재개
    AG->>GW: authorize_execution
    GW-->>AG: 승객 목록 해시에 고정된 1회용 서명 토큰
    AG->>AL: POST /api/bookings/rebookings + X-Approval-Token
    AL->>AL: 서명 · 만료 · 재배정안 · 승객 목록 해시 검증
    AL-->>AG: 승객별 처리 결과
    AG-->>UI: 보고 + COMPLETED (SSE)
```

## 4. 보안 경계

```mermaid
flowchart LR
    subgraph T["기술적 경계 - NVIDIA OpenShell"]
        direction TB
        A["에이전트 프로세스<br/>user: sandbox"] -->|"허용: GET /api/flights/**"| AL["airline-service:8000"]
        A -->|"허용: 규정 검색 · 최적화 POST"| RA["reroute-api:8000"]
        A -->|"허용: POST /v1/chat/completions"| N["integrate.api.nvidia.com:443"]
        A -.->|"차단: deny_rules POST /api/rebooking/plans/**"| RA
        A -.->|"차단: 정책 없음"| X["unknown-external-api.com"]
        A -.->|"차단: 파일시스템 허용 목록"| K["~/.ssh/id_rsa"]
    end
    subgraph B["업무적 경계 - 사람의 승인"]
        direction TB
        O([운영자]) -->|승인| G["승인 게이트웨이"]
        G -->|"HMAC 토큰: 재배정안 · 승인 · 목록 해시 · 만료"| BK["Booking API"]
        A2["에이전트"] -->|"토큰 없음 / 목록 변경 / 만료"| BK
        BK -.->|"401 / 403"| A2
    end
```

- **OpenShell**은 *"이 프로세스가 이 엔드포인트·파일에 접근해도 되는가?"*에 답합니다. 샌드박스 모드에서는 OpenShell이
  강제하고, 데모 모드에서는 같은 YAML을 프로세스 안에서 평가하며 화면에 "policy mirror"로 표시합니다.
- **사람의 승인**은 *"책임 있는 사람이 이 업무 변경을 승인했는가?"*에 답합니다. 백엔드에서 두 번 강제합니다.
  1. 게이트웨이는 승인됨(APPROVED), 유효기간 안, 미사용 상태인 승인에 대해서만 토큰을 발급합니다.
  2. Booking API가 그 토큰을 정확한 승객 목록과 대조해 따로 검증합니다.

remote 모드의 worker는 DB 접속 정보도 서명 키도 없습니다. 그래서 에이전트가 완전히 장악되더라도, 사람의 승인 없이는 예약을 바꿀 수 없습니다.

## 5. 사람 승인 흐름

```mermaid
stateDiagram-v2
    [*] --> PENDING: propose_rebooking (유효 30분)
    PENDING --> APPROVED: 운영자 승인 (사람 신원 필수, 에이전트 신원 거부)
    PENDING --> REJECTED: 운영자 반려
    PENDING --> EXPIRED: 유효시간 경과 (조회 시마다 확인)
    APPROVED --> Consumed: authorize_execution (1회용, 토큰 5분 만료)
    Consumed --> [*]: 예약 재발행, 재배정안 EXECUTED / PARTIALLY_EXECUTED
    REJECTED --> [*]
    EXPIRED --> [*]
```

운영자 검토가 필요한 승객(특수지원, 환승 여유 부족)은 운영자가 승인할 때 직접 포함시킨 경우에만 실행됩니다.
포함하지 않으면 `HELD_FOR_OPERATOR`(운영자 처리 대기)로 남습니다.

## 6. 최적화 흐름

```mermaid
flowchart LR
    P["승객<br/>등급 · VIP · 좌석 · 특수지원 · 후속편"] --> F
    ALT["대체편<br/>좌석등급별 잔여석 · 항공사 · 시각"] --> F
    POL["검색된 규정<br/>policy-params"] --> C["규정 컴파일러<br/>PolicyRules + 적용/누락"] --> F
    W["config/optimization.yaml<br/>가중치"] --> F
    F["정식화<br/>항공편 선별 C5/C6<br/>배정 가능한 x p,f,c C4/C6<br/>비용 C3 + 목적함수"] --> M["MilpProblem<br/>CSR 행렬 · 경계 · 이진 변수"]
    M -->|"OPTIMIZATION_PROVIDER=cuopt"| CU["NVIDIA cuOpt 서버"]
    M -->|"대체 (명시 표시)"| HI["HiGHS CPU"]
    CU --> D["디코더<br/>배정 · 상태 · 사유 · 정책 ID"]
    HI --> D
    D --> R["OptimizationResult<br/>+ 선착순 방식 비교"]
```

- 변수: `x[p,f,c]`와 `y[p]`는 모두 0/1 이진 변수입니다.
- C1: `Σx + y = 1` (승객마다 결과는 정확히 하나)
- C2: 좌석등급별 잔여 좌석
- C3: 다운그레이드 벌점 (등급 × VIP)
- C4: 최소 환승시간(MCT)
- C5: 같은 도착지
- C6: 항공사 · 시간 한도 · 운항 상태 규정
- 목적함수: 지연×등급 + VIP 지연 + 다운그레이드 + 환승 위험 + 재발권 비용 + 100,000 × 미배정

## 7. AWS 배포 구성

```mermaid
flowchart TB
    user([심사위원 · 운영자]) -->|"HTTP/HTTPS"| alb
    admin([관리자]) -.->|"SSM Session Manager"| ec2
    subgraph vpc["VPC (가용영역 2개)"]
        subgraph pub["퍼블릭 서브넷"]
            alb["ALB"]
            nat["NAT Gateway"]
        end
        subgraph priv["프라이빗 서브넷"]
            subgraph ec2["EC2 GPU 호스트 · docker compose"]
                web["web"]
                api["reroute-api"]
                air["airline-service"]
                cu["cuOpt"]
            end
            rds[("RDS PostgreSQL")]
        end
    end
    alb -->|"/*"| web
    alb -->|"/api/*"| api
    alb -.->|"/internal/* 차단"| blk(("404"))
    api --> air
    api --> cu
    api --> rds
    air --> rds
    ec2 --> nat --> nim["NVIDIA API"]
    ec2 -.-> sm["Secrets Manager"]
    ec2 -.-> ecr["ECR"]
    ec2 -.-> cw["CloudWatch Logs"]
```

상세 절차, 비밀값 흐름, 사전 점검 체크리스트는 [infra/terraform/README.md](../infra/terraform/README.md)에 있습니다.
