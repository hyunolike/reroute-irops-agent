# ReRoute 에이전트: 구조와 동작

**🇰🇷 한국어** · [🇺🇸 English](agent.en.md)

이 문서는 **에이전트 한 개가 어떻게 생겼고, 한 번의 요청을 어떻게 처리하는지**를 그림으로 설명합니다.
시스템 전체 구성과 배포는 [architecture.md](architecture.md)를 보세요.

| 그림 | 보여주는 것 |
|---|---|
| [1. 에이전트 구조](#1-에이전트-구조) | 에이전트를 이루는 부품 (두뇌·손·기억·안전장치) |
| [2. 에이전트 루프](#2-에이전트-루프-계획--행동--관찰) | 매 턴마다 일어나는 일: 계획 → 행동 → 관찰 |
| [3. KE123 한 번의 실행](#3-ke123-한-번의-실행) | 실제 요청 하나가 흘러가는 순서 |
| [4. 누가 무엇을 결정하는가](#4-누가-무엇을-결정하는가) | LLM · 코드 · solver · 사람 · OpenShell의 역할 분리 |
| [5. LLM이 보는 것과 보지 않는 것](#5-llm이-보는-것과-보지-않는-것) | 데이터가 LLM을 거치지 않는 구조 |
| [6. 실수했을 때의 복구 경로](#6-실수했을-때의-복구-경로) | 모델이 틀리거나 멈출 때 |
| [7. 두 가지 실행 형태](#7-두-가지-실행-형태) | API 안에서 실행 vs 샌드박스 worker |

---

## 1. 에이전트 구조

```mermaid
flowchart TB
    goal(["운영자 목표 한 문장<br/>“KE123편이 결항됐어…”"]) --> orch

    subgraph agent["ReRoute 에이전트"]
        direction TB
        orch["🧭 오케스트레이터<br/>에이전트 루프 · 상태 머신 · 가드레일<br/>(최대 30턴, 안내 최대 2회)"]

        subgraph brain["🧠 두뇌 — 계획과 도구 선택"]
            nim["Nemotron (NIM)<br/>function calling"]
            fb["스크립트 플래너<br/>(키가 없을 때만, 화면에 경고)"]
        end

        subgraph hands["🛠 손 — 도구 8종 (타입 검증된 인자)"]
            t1["조회<br/>get_disrupted_flight<br/>get_affected_passengers<br/>search_alternative_flights"]
            t2["지식<br/>search_rebooking_policy"]
            t3["결정 위임<br/>optimize_rebooking"]
            t4["예외 분석<br/>explore_exception_options"]
            t5["제안 · 실행<br/>propose_rebooking<br/>execute_rebooking 🔒"]
        end

        mem[("📒 작업 기억<br/>항공편 · 승객 · 대체편<br/>검색된 규정 · 최적화 결과<br/>예외 분석 · 재배정안 ID")]
        guard["🛡 통제된 통신<br/>모든 호출: 정책 검사 → 감사 기록"]
        log["📡 이벤트 로그<br/>→ 대시보드 실시간(SSE)"]
    end

    orch <--> brain
    orch --> hands
    hands <--> mem
    hands --> guard
    orch --> log

    guard --> svc1["Mock 항공사 API"]
    guard --> svc2["지식 서비스 → NeMo Retriever"]
    guard --> svc3["최적화 서비스 → cuOpt"]
    guard --> svc4["승인 게이트웨이"]
```

- **두뇌:** 다음에 무엇을 할지 고르는 부분입니다. 실제 모드에서는 Nemotron이 매 턴 도구와 인자를 직접 고릅니다.
- **손:** 도구 8종입니다. 도메인 작업은 전부 이 도구를 통해 HTTP API로만 합니다. 에이전트는 DB에 직접 접근하지 않습니다.
- **기억:** 승객 35명 같은 큰 데이터는 여기에 둡니다. LLM에게는 요약만 보여줍니다(5번 그림).
- **안전장치:** 오케스트레이터가 순서·인자·사전 조건을 검사합니다. 모든 외부 통신은 OpenShell 정책으로 검사되고 감사 로그에 남습니다.

## 2. 에이전트 루프 (계획 → 행동 → 관찰)

```mermaid
flowchart TD
    start(["목표 수신<br/>RECEIVED"]) --> think

    think["🧠 LLM에 전달<br/>시스템 규칙 + 대화 기록 + 도구 명세 8종"] --> resp{"LLM 응답에<br/>도구 호출이 있는가?"}

    resp -- "예 (한 번에 여러 개 가능)" --> v1{"등록된 도구?"}
    v1 -- 아니오 --> err["오류를 LLM에 돌려줌<br/>(가드레일 이벤트 기록)"]
    v1 -- 예 --> v2{"인자 형식이<br/>맞는가? (Pydantic)"}
    v2 -- 아니오 --> err
    v2 -- 예 --> v3{"사전 조건 충족?<br/>예: 최적화 전 규정 확보"}
    v3 -- 아니오 --> err
    v3 -- 예 --> act["🛠 도구 실행<br/>상태 변경 → 정책 검사 → HTTP 호출"]
    act --> obs["👀 관찰<br/>결과 요약(+ 규정 coverage)을<br/>대화에 추가 · 기억 갱신"]
    err --> think
    obs --> done{"재배정안이<br/>제안되었는가?"}
    done -- 아니오 --> think
    done -- 예 --> wait(["운영자 승인 대기<br/>WAITING_APPROVAL"])

    resp -- 아니오 --> stop{"멈춰도 되는<br/>사실이 있는가?"}
    stop -- "예: 정상 운항 / 지연이 규정 기준 미만" --> noact(["조치 불필요로 완료<br/>COMPLETED"])
    stop -- "편명 확인 불가" --> fail(["실패 보고<br/>FAILED"])
    stop -- 아니오 --> nudge{"안내 횟수 < 2?"}
    nudge -- 예 --> hint["다음 단계를 구체적으로 안내<br/>예: “optimize_rebooking(KE123)을 호출하라”"] --> think
    nudge -- 아니오 --> takeover["스크립트 플래너가 이어받음<br/>(기록됨)"] --> think
```

## 3. KE123 한 번의 실행

```mermaid
sequenceDiagram
    autonumber
    actor Op as 운영자
    participant O as 오케스트레이터
    participant L as Nemotron
    participant T as 도구
    participant M as 작업 기억
    participant S as 서비스<br/>(항공사·RAG·cuOpt·승인)

    Op->>O: "KE123편이 결항됐어. 최적 재배정안을 만들어줘."
    O->>L: 규칙 + 목표 + 도구 명세
    L-->>O: 계획 1~5단계 + get_disrupted_flight(KE123)
    O->>T: 검증 후 실행
    T->>S: GET /api/flights/KE123
    S-->>T: CANCELLED, 기체 결함
    T->>M: 항공편 저장
    T-->>O: 요약 결과
    O->>L: 관찰 추가
    L-->>O: get_affected_passengers, search_alternative_flights
    O->>T: 실행 (승객 35명 · 대체편 7편 → 기억)
    O->>L: 요약만 전달 (35명, VIP 3, 연결 5, 특수지원 2)
    L-->>O: search_rebooking_policy(질의 여러 개)
    T->>S: 규정 검색 (NeMo Retriever)
    T-->>O: 규정 + coverage {빠진 규정: 없음}
    L-->>O: optimize_rebooking(KE123)
    T->>S: 기억 속 데이터 + 규정 → MILP → cuOpt
    S-->>T: 배정 결과 (자동 31 · 검토 3 · 대안 없음 1)
    L-->>O: explore_exception_options(P010, P011, P013, P014)
    T-->>O: P010: 7C1102는 연결 가능하지만 IROP-002로 차단
    L-->>O: propose_rebooking(KE123)
    O->>L: solver 결과로 운영자 브리핑 작성 (인용 규정 검증)
    T->>S: 재배정안 저장 + 승인 요청 (PENDING)
    O-->>Op: WAITING_APPROVAL (대시보드 실시간 표시)
    Note over Op,S: 승인 후에만 execute_rebooking → 승인 게이트웨이 토큰 → Booking API
```

턴 구성(한 턴에 도구를 몇 개 부를지, 규정 검색을 몇 번 할지)은 모델이 정하므로 실행마다 달라질 수 있습니다.
달라지지 않는 것은 검증 규칙, solver 결과, 승인 경계입니다.

## 4. 누가 무엇을 결정하는가

```mermaid
flowchart TB
    subgraph llm["🧠 Nemotron이 결정"]
        l1["다음에 호출할 도구"]
        l2["검색 질의 문구 · 추가 검색 여부"]
        l3["어떤 예외 승객을 조사할지"]
        l4["운영자 브리핑 문장"]
    end
    subgraph code["🧭 오케스트레이터 코드가 강제"]
        c1["도구 등록 · 인자 형식 · 사전 조건"]
        c2["상태 머신 · 최대 턴 수"]
        c3["브리핑 인용 규정 검증"]
    end
    subgraph solver["🧮 cuOpt solver가 결정"]
        s1["누가 어느 편·좌석으로 가는지"]
    end
    subgraph rag["📚 검색된 규정이 결정"]
        r1["MCT · 제휴사 · 시간 한도 등 제약값"]
    end
    subgraph human["👤 사람이 결정"]
        h1["승인 / 반려"]
        h2["검토 대상 승객 포함 여부"]
    end
    subgraph shell["🛡 OpenShell이 결정"]
        o1["접근 가능한 호스트 · 경로 · 파일"]
    end

    llm -->|"제안"| code
    code -->|"검증된 호출"| solver
    rag -->|"제약조건"| solver
    solver -->|"배정 결과"| human
    shell -.->|"모든 통신을 감싼다"| code
```

LLM은 **어떻게 일할지**를 정하고, **결과(배정)와 권한(승인·접근)은 정하지 않습니다.**

## 5. LLM이 보는 것과 보지 않는 것

```mermaid
flowchart LR
    subgraph seen["LLM에게 보이는 것"]
        a1["목표 문장"]
        a2["도구 명세"]
        a3["결과 요약<br/>예: 35명 · VIP 3 · 연결 5"]
        a4["규정 본문 발췌 + coverage"]
        a5["최적화 요약 · 예외 목록"]
    end
    subgraph hidden["LLM을 거치지 않는 것"]
        b1["승객 35명의 전체 데이터"]
        b2["대체편 좌석 재고"]
        b3["MILP 변수 148개 · 제약 41개"]
        b4["배정 결과 원본"]
        b5["승인 서명 키 · DB 접속 정보"]
    end
    seen -->|"도구 인자는 핸들만<br/>(편명, 검색어, 승객 ID)"| tools["도구"]
    tools <--> hidden
```

그래서 LLM이 배정을 "살짝 고치거나" 승객 데이터를 외부로 흘릴 경로가 구조적으로 없습니다.

## 6. 실수했을 때의 복구 경로

```mermaid
flowchart TD
    x1["모델이 순서를 틀림<br/>예: 규정 없이 optimize 호출"] --> g1["사전 조건 오류를 돌려줌<br/>+ 무엇이 빠졌는지 안내"] --> ok(["모델이 스스로 수정"])
    x2["인자 형식 오류<br/>예: query 문자열 하나"] --> g2["허용 가능한 변형은 자동 보정<br/>그 외는 검증 오류 반환"] --> ok
    x3["도구 호출을 텍스트로 출력<br/>#lt;TOOLCALL#gt; …"] --> g3["텍스트에서 도구 호출 파싱"] --> ok
    x4["중간에 멈춤"] --> g4["다음 단계 구체적 안내<br/>(최대 2회)"] --> ok
    x4 --> g5["계속 멈추면 스크립트 플래너가 이어받음<br/>(GUARDRAIL 이벤트로 기록)"]
    x5["NIM 일시 오류 429/5xx"] --> g6["재시도 (지수 백오프)"] --> ok
    x6["NIM 장애 지속"] --> g5
    x7["허용되지 않은 호스트 호출"] --> g7["OpenShell 정책 차단 + 감사 기록"]
    x8["승인 없이 execute 시도"] --> g8["승인 게이트웨이 403 + 감사 기록"]
```

## 7. 두 가지 실행 형태

```mermaid
flowchart LR
    subgraph inline["inline 모드 (기본 데모)"]
        api1["reroute-api 프로세스"] --- ag1["에이전트<br/>(같은 프로세스)"]
        ag1 --- db1[("DB 직접 사용")]
    end
    subgraph remote["remote 모드 (보안 운영)"]
        api2["reroute-api<br/>(컨트롤 플레인)"]
        subgraph sb["OpenShell 샌드박스"]
            ag2["에이전트 worker<br/>DB 접속 정보 없음<br/>서명 키 없음"]
        end
        ag2 -->|"작업 가져오기 · 이벤트 · 재배정안<br/>/internal/agent/*"| api2
        ag2 -->|"승인 후에만<br/>실행 토큰 발급"| api2
        api2 --- db2[("DB")]
    end
```

`AGENT_EXECUTION=remote`로 두면 에이전트는 샌드박스 안의 별도 프로세스가 됩니다. 에이전트가 장악되더라도 사람의 승인 없이는 예약을 바꿀 수 없습니다.

---

관련 코드: `apps/api/app/agent/orchestrator.py` (루프·가드레일) · `app/agent/prompts.py` (규칙) · `app/tools/` (도구 8종) ·
`app/providers/llm/` (Nemotron 어댑터·선택 로직) · `app/agent/worker.py` (샌드박스 worker) · `app/agent/evaluate.py` (실제 모델 평가)
